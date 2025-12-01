#!/usr/bin/env python3
"""
昆仑哨兵·实验室多模态监控系统
Kunlun Sentinel Lab Monitor - 主应用文件
部署于 Orange Pi Kunpeng Pro (openEuler 22.03 LTS SP4)
适配 openGauss 5.0.0，当前端口 7654，数据库 lab_monitor，用户 labuser
"""

import os
import random
import psycopg2
from psycopg2.extensions import connection as PsyConnection
from psycopg2 import pool as pg_pool
# 优先尝试 openGauss 兼容驱动（如已安装），否则回退到 psycopg2
OG_AVAILABLE = False
OG_DBAPI = None
try:
    from py_opengauss.driver import dbapi20 as og_dbapi  # DB-API 2.0 接口
    OG_AVAILABLE = True
    OG_DBAPI = og_dbapi
    print("[DB] 检测到 py-opengauss 驱动，将优先使用兼容握手连接")
except Exception:
    OG_AVAILABLE = False
import cv2
import numpy as np
from datetime import datetime
import threading
import time
from flask import Flask, render_template, jsonify, request, Response
import json
from queue import Queue
import glob
import zlib

# ==================== 配置 ====================
BASE_DIR = "/home/openEuler/lab_monitor"
STATIC_DIR = os.path.join(BASE_DIR, "static")
IMAGES_DIR = os.path.join(STATIC_DIR, "images")
MODELS_DIR = os.path.join(BASE_DIR, "models")
RUNTIME_DIR = os.path.join(BASE_DIR, "runtime")
MODELS_STATUS_PATH = os.path.join(RUNTIME_DIR, "models_status.json")
MODELS_CMD_PATH = os.path.join(RUNTIME_DIR, "models_commands.json")
MODELS_CFG_PATH = os.path.join(MODELS_DIR, "config.json")
MODELS_CACHE = []
try:
    os.makedirs(RUNTIME_DIR, exist_ok=True)
except Exception:
    pass

# 数据库连接配置（支持环境变量与 TCP 回退）
# 优先读取环境变量（DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD），否则保持原有行为（UNIX socket + peer）
# 默认强制使用 TCP 连接，以避免 UNIX socket 路径不兼容
ENV_DB_HOST = os.getenv('DB_HOST', '127.0.0.1')
ENV_DB_PORT = int(os.getenv('DB_PORT', '7654'))
ENV_DB_NAME = os.getenv('DB_NAME', 'lab_monitor')
# 默认使用业务账号与密码，避免环境变量未继承导致连接失败
ENV_DB_USER = os.getenv('DB_USER', 'labuser')
ENV_DB_PASSWORD = os.getenv('DB_PASSWORD', 'LabUser@12345')
ENV_FLASK_PORT = int(os.getenv('FLASK_PORT', '5000'))

DB_CONFIG = {
    'host': ENV_DB_HOST,
    'port': ENV_DB_PORT,
    'database': ENV_DB_NAME,
    'user': ENV_DB_USER,
    'password': ENV_DB_PASSWORD if ENV_DB_PASSWORD else None,
    'sslmode': 'disable'
}

PG_POOL = None

DS18B20_PATH_PATTERN = "/sys/bus/w1/devices/28-*/w1_slave"
CAMERA_DEVICE = "/dev/video0"

# ==================== 初始化 ====================
app = Flask(__name__, static_folder=STATIC_DIR)
LATEST_CACHE = None
HEARTBEAT = { 'temp': 0, 'light': 0, 'image': 0 }
HB_TIMEOUT = 60
os.makedirs(IMAGES_DIR, exist_ok=True)
SUBSCRIBERS = set()

def broadcast(data):
    msg = json.dumps(data)
    for q in list(SUBSCRIBERS):
        try:
            q.put(msg, block=False)
        except Exception:
            pass

def build_status():
    now_ts = int(time.time())
    db_conn = get_db_connection()
    db_ok = bool(db_conn)
    if db_conn:
        try:
            close_db_connection(db_conn)
        except Exception:
            pass
    return {
        "ds18b20": "online" if (now_ts - HEARTBEAT['temp'] < HB_TIMEOUT) else "offline",
        "light": "online" if (now_ts - HEARTBEAT['light'] < HB_TIMEOUT) else "offline",
        "camera": "online" if (now_ts - HEARTBEAT['image'] < HB_TIMEOUT) else "offline",
        "db": "online" if db_ok else "offline"
    }


# ==================== 数据库函数 ====================
def get_db_connection():
    """获取数据库连接（失败返回 None）。
    逻辑：
    1) 若 py-opengauss 驱动可用，优先使用其 DB-API 连接以适配 openGauss 握手。
    2) 否则按当前 DB_CONFIG 使用 psycopg2 连接。
    3) 若为 UNIX socket 且失败，则回退到 TCP: 127.0.0.1 + 环境端口，用户优先 ENV_DB_USER（默认 labuser），密码 ENV_DB_PASSWORD。
    """
    # 0) 优先使用 py-opengauss（如可用）
    if OG_AVAILABLE and OG_DBAPI is not None:
        try:
            dsn = f"opengauss://{ENV_DB_USER}:{ENV_DB_PASSWORD}@{ENV_DB_HOST}:{ENV_DB_PORT}/{ENV_DB_NAME}"
            conn = OG_DBAPI.connect(dsn)  # DB-API 连接对象，提供 cursor()/commit()/close()
            print("[DB] 连接成功（py-opengauss）")
            return conn
        except Exception as e:
            print(f"[DB] py-opengauss 连接失败，回退到 psycopg2: {e}")

    try:
        if PG_POOL is not None:
            return PG_POOL.getconn()
        return psycopg2.connect(**{k: v for k, v in DB_CONFIG.items() if v is not None})
    except Exception as e:
        print(f"[DB] 首次连接失败: {e}")

    # 若当前为 UNIX socket（host 为空），尝试 TCP 回退
    try:
        if (DB_CONFIG.get('host', '') == ''):
            # 若有 py-opengauss，则也尝试其 TCP 连接
            if OG_AVAILABLE and OG_DBAPI is not None:
                try:
                    dsn = f"opengauss://{ENV_DB_USER}:{ENV_DB_PASSWORD}@127.0.0.1:{ENV_DB_PORT}/{ENV_DB_NAME}"
                    conn = OG_DBAPI.connect(dsn)
                    print("[DB] TCP 回退连接成功（py-opengauss）")
                    return conn
                except Exception as e2:
                    print(f"[DB] py-opengauss TCP 回退失败: {e2}")
            # psycopg2 TCP 回退
            fallback = {
                'host': '127.0.0.1',
                'port': ENV_DB_PORT,
                'database': ENV_DB_NAME,
                'user': os.getenv('DB_USER', 'labuser'),
                'password': os.getenv('DB_PASSWORD', ''),
                'sslmode': 'disable'
            }
            if not fallback['password']:
                fallback.pop('password')
            return psycopg2.connect(**fallback)
    except Exception as e:
        print(f"[DB] TCP 回退连接失败: {e}")
    return None


def init_database():
    """首次运行自动建库表（幂等，与 db_init.sql 对齐）"""
    conn = get_db_connection()
    if not conn:
        return False
    cursor = None
    try:
        cursor = conn.cursor()
        # 创建 sensor_data 表（与初始化脚本一致），增加 light 列（可空）
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS sensor_data (
            id BIGSERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ DEFAULT NOW(),
            temperature REAL NOT NULL,
            image_path TEXT NOT NULL,
            light INT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """)
        # 兼容已有表，补充缺失的 light 列
        cursor.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='sensor_data' AND column_name='light'")
        if cursor.fetchone() is None:
            cursor.execute("ALTER TABLE sensor_data ADD COLUMN light INT")
        conn.commit()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS model_outputs (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            output TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """)
        conn.commit()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS board_tags (
            name TEXT PRIMARY KEY
        );
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS board_tag_current (
            name TEXT
        );
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS scripts (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            lang TEXT NOT NULL,
            content TEXT NOT NULL,
            author TEXT,
            org TEXT,
            license TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS script_exec_log (
            id BIGSERIAL PRIMARY KEY,
            script_id BIGINT REFERENCES scripts(id) ON DELETE CASCADE,
            status TEXT,
            output TEXT,
            started_at TIMESTAMPTZ DEFAULT NOW(),
            finished_at TIMESTAMPTZ
        );
        """)
        try:
            cursor.execute("ALTER TABLE script_exec_log ADD COLUMN IF NOT EXISTS pid BIGINT;")
        except Exception:
            pass
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS script_commands (
            id BIGSERIAL PRIMARY KEY,
            script_id BIGINT REFERENCES scripts(id) ON DELETE CASCADE,
            cmd TEXT NOT NULL,
            status TEXT,
            issued_at TIMESTAMPTZ DEFAULT NOW(),
            processed_at TIMESTAMPTZ,
            note TEXT
        );
        """)
        conn.commit()
        print("[DB] 表 sensor_data 已确保存在")
        return True
    except Exception as e:
        print(f"[DB] 初始化失败: {e}")
        return False
    finally:
        if cursor:
            cursor.close()
        if conn:
            close_db_connection(conn)

def close_db_connection(conn):
    try:
        if PG_POOL is not None and isinstance(conn, PsyConnection):
            try:
                PG_POOL.putconn(conn)
                return
            except Exception:
                pass
        conn.close()
    except Exception:
        pass


# ==================== 传感器函数 ====================
def read_temperature():
    """读取 DS18B20 温度"""
    try:
        files = glob.glob(DS18B20_PATH_PATTERN)
        if not files:
            return {"error": "ds18b20 offline"}
        
        with open(files[0], 'r') as f:
            lines = f.readlines()
        
        if len(lines) < 2 or "YES" not in lines[0]:
            return {"error": "ds18b20 data invalid"}
        
        t_line = lines[1]
        if "t=" not in t_line:
            return {"error": "ds18b20 parse error"}
        
        t_raw = t_line.split("t=")[1].strip()
        temp_c = float(t_raw) / 1000.0
        return {"temp": round(temp_c, 2)}  # 注意：字段名是 temp
    except Exception as e:
        print(f"[TEMP] 读取异常: {e}")
        return {"error": "ds18b20 offline"}


def capture_image():
    """采集 USB 摄像头图像"""
    try:
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return {"error": "camera offline"}
        
        ret, frame = cap.read()
        cap.release()
        
        if not ret:
            return {"error": "camera capture failed"}
        
        # 保存图像
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"capture_{ts}.jpg"
        filepath = os.path.join(IMAGES_DIR, filename)
        cv2.imwrite(filepath, frame)
        
        return {"image_path": f"/static/images/{filename}"}
    except Exception as e:
        print(f"[CAM] 采集异常: {e}")
        return {"error": "camera offline"}




def image_from_pixels(frame_obj):
    """将前端/外部发送的像素矩阵(frame.width/height/pixels)转换并保存为图像。
    返回 {image_path, checksum_frame_calc} 或 {error}
    """
    try:
        width = int(frame_obj.get("width", 0))
        height = int(frame_obj.get("height", 0))
        pixels = frame_obj.get("pixels")
        if width <= 0 or height <= 0 or not isinstance(pixels, list):
            return {"error": "frame invalid"}

        # 组装为 numpy 图像
        arr = np.zeros((height, width, 3), dtype=np.uint8)
        flat_bytes = bytearray()
        for y in range(height):
            row = pixels[y]
            if not isinstance(row, list) or len(row) != width:
                return {"error": "frame row invalid"}
            for x in range(width):
                pix = row[x]
                r = int(pix.get("r", 0)) & 0xFF
                g = int(pix.get("g", 0)) & 0xFF
                b = int(pix.get("b", 0)) & 0xFF
                arr[y, x, 0] = r
                arr[y, x, 1] = g
                arr[y, x, 2] = b
                flat_bytes.extend([r, g, b])

        # 计算 CRC32 校验（与C端一致）
        checksum_calc = zlib.crc32(flat_bytes) & 0xFFFFFFFF
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"ingest_{ts}.png"
        filepath = os.path.join(IMAGES_DIR, filename)
        # OpenCV 期望 BGR 顺序；当前 arr 是 RGB → 转换
        arr_bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        cv2.imwrite(filepath, arr_bgr)
        return {"image_path": f"/static/images/{filename}", "checksum_frame_calc": f"{checksum_calc:08x}"}
    except Exception as e:
        print(f"[FRAME] 转换异常: {e}")
        return {"error": "frame convert failed"}


# ==================== 数据存取 ====================
def save_sensor_data(temp, image_path, light=None):
    """保存到 openGauss.sensor_data 表"""
    conn = get_db_connection()
    cursor = None
    try:
        cursor = conn.cursor()
        if light is None:
            cursor.execute(
                "INSERT INTO sensor_data (temperature, image_path) VALUES (%s, %s)",
                (temp, image_path)
            )
        else:
            cursor.execute(
                "INSERT INTO sensor_data (temperature, image_path, light) VALUES (%s, %s, %s)",
                (temp, image_path, int(light))
            )
        conn.commit()
        return True
    except Exception as e:
        print(f"[SAVE] 失败: {e}")
        conn.rollback()
        return False
    finally:
        if cursor:
            cursor.close()
        if conn:
            close_db_connection(conn)

def save_sensor_data_bulk(rows):
    conn = get_db_connection()
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO sensor_data (temperature, image_path, light) VALUES (%s, %s, %s)",
            [(float(t), str(p), (None if l is None else int(l))) for (t, p, l) in rows]
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"[SAVE] 批量失败: {e}")
        conn.rollback()
        return False
    finally:
        if cursor:
            cursor.close()
        if conn:
            close_db_connection(conn)


def get_latest_data():
    """获取最新一条数据"""
    conn = get_db_connection()
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT temperature, image_path, light, timestamp
            FROM sensor_data ORDER BY timestamp DESC LIMIT 1
        """)
        row = cursor.fetchone()
        if not row:
            return None
        img = row[1]
        if isinstance(img, str) and img.startswith("/static/images/"):
            fname = os.path.basename(img)
            fpath = os.path.join(IMAGES_DIR, fname)
            if not os.path.exists(fpath):
                cap = capture_image()
                img = cap.get("image_path") if isinstance(cap, dict) else None
        return {
            "temperature": row[0],
            "image_path": img,
            "light": row[2],
            "timestamp": row[3].strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        print(f"[QUERY] latest failed: {e}")
        return None
    finally:
        if cursor:
            cursor.close()
        if conn:
            close_db_connection(conn)


def get_history_data(hours=24):
    """获取最近 N 小时历史数据（适配 openGauss INTERVAL）"""
    conn = get_db_connection()
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT timestamp, temperature
            FROM sensor_data
            WHERE timestamp > NOW() - INTERVAL '%s hours'
            ORDER BY timestamp ASC
        """, (hours,))
        rows = cursor.fetchall()
        
        t_data = []
        for ts, temp in rows:
            t_data.append({"timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"), "value": temp})
        return {"temperature_data": t_data}
    except Exception as e:
        print(f"[QUERY] history failed: {e}")
        return {"temperature_data": [], "bubble_data": []}
    finally:
        if cursor:
            cursor.close()
        if conn:
            close_db_connection(conn)


# ==================== Web API ====================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/open-source', strict_slashes=False)
@app.route('/open-source/', strict_slashes=False)
@app.route('/open_source', strict_slashes=False)
@app.route('/openSource', strict_slashes=False)
def open_source():
    base = datetime.now()
    conn = get_db_connection()
    cur = None
    scripts = []
    try:
        cur = conn.cursor()
        cur.execute("SELECT id,name,lang,author,org,license,created_at,content FROM scripts ORDER BY created_at DESC")
        rows = cur.fetchall()
        for r in rows:
            sid = r[0]
            name = r[1]
            lang = r[2]
            author = r[3]
            org = r[4]
            lic = r[5]
            updated = r[6].strftime('%Y-%m-%d') if r[6] else ''
            content = r[7] or ''
            first_line = ''
            if content:
                lines = [ln for ln in content.splitlines() if ln.strip()]
                first_line = lines[0][:120] if lines else ''
            lower_name = (name or '').lower()
            capture = '通用'
            if any(k in lower_name for k in ['temp','温度','ds18b20']):
                capture = '温度'
            elif any(k in lower_name for k in ['camera','image','图像','uvc']):
                capture = '图像'
            elif any(k in lower_name for k in ['light','光敏','光照']):
                capture = '光敏'
            stack = 'Python' if str(lang).lower() == 'py' else 'C'
            scripts.append({'id': sid, 'name': name, 'capture': capture, 'purpose': first_line, 'stack': stack, 'author': author, 'org': org, 'license': lic, 'updated': updated})
    except Exception as e:
        print(f"[SCRIPT] 列表失败: {e}")
    finally:
        if cur:
            cur.close()
        if conn:
            close_db_connection(conn)
    stats = {
        'total_scripts': len(scripts)
    }
    return render_template('open_source.html', scripts=scripts, stats=stats, current_time=base.strftime('%Y年%m月%d日'))


@app.route('/db')
def db_page():
    return render_template('db.html')

@app.route('/api/db/tables')
def api_db_tables():
    conn = get_db_connection()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        rows = cur.fetchall()
        return jsonify([r[0] for r in rows])
    except Exception as e:
        print(f"[DB] 表列表失败: {e}")
        return jsonify([])
    finally:
        if cur:
            cur.close()
        if conn:
            close_db_connection(conn)

@app.route('/api/db/query', methods=['POST'])
def api_db_query():
    data = request.get_json(silent=True) or {}
    sql = str(data.get('sql', '')).strip()
    if not sql.lower().startswith('select'):
        return jsonify({"error": "only SELECT allowed"}), 400
    conn = get_db_connection()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchmany(1000)
        cols = [d[0] for d in cur.description] if cur.description else []
        return jsonify({"columns": cols, "rows": rows})
    except Exception as e:
        print(f"[DB] 查询失败: {e}")
        return jsonify({"error": "query failed"}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            close_db_connection(conn)

@app.route('/api/db/clear', methods=['POST'])
def api_db_clear():
    data = request.get_json(silent=True) or {}
    table = str(data.get('table', '')).strip()
    if table != 'sensor_data':
        return jsonify({"error": "only sensor_data allowed"}), 400
    conn = get_db_connection()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("TRUNCATE TABLE sensor_data")
        conn.commit()
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"[DB] 清空失败: {e}")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return jsonify({"error": "clear failed"}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            close_db_connection(conn)

@app.route('/api/latest')
def api_latest():
    latest = LATEST_CACHE or get_latest_data()
    now_ts = int(time.time())
    db_conn = get_db_connection()
    if db_conn:
        try:
            close_db_connection(db_conn)
        except Exception:
            pass
    status = {
        "ds18b20": "online" if (now_ts - HEARTBEAT['temp'] < HB_TIMEOUT) else "offline",
        "light": "online" if (now_ts - HEARTBEAT['light'] < HB_TIMEOUT) else "offline",
        "camera": "online" if (now_ts - HEARTBEAT['image'] < HB_TIMEOUT) else "offline",
        "db": "online" if db_conn else "offline"
    }

    if latest:
        latest["sensor_status"] = status
        return jsonify(latest)
    else:
        return jsonify({
            "sensor_status": status,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })


@app.route('/api/history')
def api_history():
    hours = request.args.get('hours', 24, type=int)
    return jsonify(get_history_data(hours))


@app.route('/api/events')
def api_events():
    def gen():
        q = Queue(maxsize=100)
        SUBSCRIBERS.add(q)
        try:
            while True:
                msg = q.get()
                yield f"data: {msg}\n\n"
        finally:
            try:
                SUBSCRIBERS.discard(q)
            except Exception:
                pass
    return Response(gen(), mimetype='text/event-stream')

@app.route('/api/relay_notify', methods=['POST'])
def api_relay_notify():
    global LATEST_CACHE
    data = request.get_json(silent=True) or {}
    t = data.get('temperature')
    l = data.get('light')
    p = data.get('image_path')
    ts = int(time.time())
    if t is not None:
        HEARTBEAT['temp'] = ts
    if l is not None:
        HEARTBEAT['light'] = ts
    if p:
        HEARTBEAT['image'] = ts
    cur = LATEST_CACHE or {}
    if t is not None:
        cur['temperature'] = t
    if l is not None:
        cur['light'] = l
    if p:
        cur['image_path'] = p
    cur['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur['sensor_status'] = build_status()
    LATEST_CACHE = cur
    try:
        broadcast(cur)
    except Exception:
        pass
    return jsonify({ 'status': 'ok' })

@app.route('/api/tags', methods=['GET', 'POST'])
def api_tags():
    if request.method == 'GET':
        conn = get_db_connection()
        cur = None
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM board_tags ORDER BY name ASC")
            rows = cur.fetchall()
            return jsonify([r[0] for r in rows])
        except Exception as e:
            print(f"[TAG] 查询失败: {e}")
            return jsonify([])
        finally:
            if cur:
                cur.close()
            if conn:
                close_db_connection(conn)
    data = request.get_json(silent=True) or {}
    name = str(data.get('name','')).strip()
    if not name:
        return jsonify({'error':'invalid tag'}), 400
    conn = get_db_connection()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO board_tags(name) VALUES(%s) ON CONFLICT DO NOTHING", (name,))
        conn.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        print(f"[TAG] 创建失败: {e}")
        conn.rollback()
        return jsonify({'error':'create failed'}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            close_db_connection(conn)


@app.route('/api/tags/current', methods=['GET', 'PUT'])
def api_tag_current():
    if request.method == 'GET':
        conn = get_db_connection()
        cur = None
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM board_tag_current LIMIT 1")
            row = cur.fetchone()
            return jsonify({'name': row[0] if row else None})
        except Exception as e:
            print(f"[TAG] 当前标签查询失败: {e}")
            return jsonify({'name': None})
        finally:
            if cur:
                cur.close()
            if conn:
                close_db_connection(conn)
    data = request.get_json(silent=True) or {}
    name = data.get('name')
    conn = get_db_connection()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM board_tag_current")
        cur.execute("INSERT INTO board_tag_current(name) VALUES(%s)", (name,))
        conn.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        print(f"[TAG] 设置失败: {e}")
        conn.rollback()
        return jsonify({'error':'set failed'}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            close_db_connection(conn)


@app.route('/api/scripts', methods=['GET','POST'])
def api_scripts():
    if request.method == 'GET':
        conn = get_db_connection()
        cur = None
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT 
                    s.id, s.name, s.lang, s.author, s.org, s.license, s.created_at,
                    l.status, l.started_at, l.finished_at
                FROM scripts s
                LEFT JOIN script_exec_log l
                  ON l.script_id = s.id
                 AND l.id = (
                    SELECT MAX(id) FROM script_exec_log WHERE script_id = s.id
                 )
                ORDER BY s.created_at DESC
            """)
            rows = cur.fetchall()
            def fmt(dt):
                return (dt.strftime('%Y-%m-%d %H:%M:%S') if dt else None)
            return jsonify([{
                'id': r[0],
                'name': r[1],
                'lang': r[2],
                'author': r[3],
                'org': r[4],
                'license': r[5],
                'created_at': fmt(r[6]),
                'last_status': r[7],
                'last_started_at': fmt(r[8]),
                'last_finished_at': fmt(r[9])
            } for r in rows])
        except Exception as e:
            print(f"[SCRIPT] 列表失败: {e}")
            return jsonify([])
        finally:
            if cur:
                cur.close()
            if conn:
                close_db_connection(conn)
    data = request.get_json(silent=True) or {}
    name = str(data.get('name','')).strip()
    lang = str(data.get('lang','')).strip().lower()
    content = str(data.get('content',''))
    author = data.get('author')
    org = data.get('org')
    lic = data.get('license')
    if not name or lang not in ('py','c') or not content:
        return jsonify({'error':'invalid script'}), 400
    conn = get_db_connection()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO scripts(name,lang,content,author,org,license) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id", (name,lang,content,author,org,lic))
        new_id = cur.fetchone()[0]
        conn.commit()
        return jsonify({'status':'ok','id':new_id})
    except Exception as e:
        print(f"[SCRIPT] 创建失败: {e}")
        conn.rollback()
        return jsonify({'error':'create failed'}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            close_db_connection(conn)


def run_script_record(script_id):
    conn = get_db_connection()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT lang, content FROM scripts WHERE id=%s", (script_id,))
        row = cur.fetchone()
        if not row:
            return {'error':'script not found'}, 404
        lang, content = row[0], row[1]
        tmp_dir = os.path.join(BASE_DIR, 'tmp')
        os.makedirs(tmp_dir, exist_ok=True)
        if lang == 'py':
            src = os.path.join(tmp_dir, f'script_{script_id}.py')
            with open(src,'w') as f:
                f.write(content)
            env = os.environ.copy()
            env['DATA_QUERY_URL'] = f"http://127.0.0.1:5000/api/history"
            env['DATA_QUERY_HOURS'] = '24'
            import subprocess
            p = subprocess.Popen(['python3', src], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
            out, _ = p.communicate()
            status = 'success' if p.returncode == 0 else 'failed'
            cur.execute("INSERT INTO script_exec_log(script_id,status,output,finished_at) VALUES(%s,%s,%s,NOW())", (script_id, status, out.decode(errors='ignore')))
            conn.commit()
            return {'status':status, 'output': out.decode(errors='ignore')}
        else:
            src = os.path.join(tmp_dir, f'script_{script_id}.c')
            bin_path = os.path.join(tmp_dir, f'script_{script_id}.out')
            with open(src,'w') as f:
                f.write(content)
            cc = os.getenv('BISHENG_CC') or os.getenv('AARCH64_CC') or 'gcc'
            cflags = os.getenv('BISHENG_CFLAGS') or '-O2 -std=c11'
            import subprocess
            env = os.environ.copy()
            env['DATA_QUERY_URL'] = f"http://127.0.0.1:5000/api/history"
            env['DATA_QUERY_HOURS'] = '24'
            compile_cmd = [cc] + cflags.split() + [src, '-o', bin_path]
            cp = subprocess.Popen(compile_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            cout, _ = cp.communicate()
            if cp.returncode != 0:
                cur.execute("INSERT INTO script_exec_log(script_id,status,output,finished_at) VALUES(%s,%s,%s,NOW())", (script_id, 'compile_failed', cout.decode(errors='ignore')))
                conn.commit()
                return {'status':'compile_failed','output':cout.decode(errors='ignore')}
            rp = subprocess.Popen([bin_path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
            rout, _ = rp.communicate()
            status = 'success' if rp.returncode == 0 else 'failed'
            cur.execute("INSERT INTO script_exec_log(script_id,status,output,finished_at) VALUES(%s,%s,%s,NOW())", (script_id, status, rout.decode(errors='ignore')))
            conn.commit()
            return {'status':status,'output':rout.decode(errors='ignore')}
    except Exception as e:
        print(f"[SCRIPT] 执行异常: {e}")
        try:
            cur.execute("INSERT INTO script_exec_log(script_id,status,output,finished_at) VALUES(%s,%s,%s,NOW())", (script_id, 'error', str(e)))
            conn.commit()
        except Exception:
            pass
        return {'error':'exec error'}, 500
    finally:
        if cur:
            cur.close()
        if conn:
            close_db_connection(conn)


@app.route('/api/scripts/run/<int:script_id>', methods=['POST'])
def api_scripts_run(script_id):
    conn = get_db_connection()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO script_commands(script_id, cmd, status) VALUES(%s, %s, %s)", (script_id, 'run', 'pending'))
        conn.commit()
        return jsonify({'accepted': True})
    except Exception as e:
        print(f"[SCRIPT] 入队失败: {e}")
        return jsonify({'error':'enqueue failed'}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            close_db_connection(conn)

@app.route('/api/models', methods=['GET'])
def api_models():
    try:
        if os.path.isfile(MODELS_STATUS_PATH):
            with open(MODELS_STATUS_PATH, 'r', encoding='utf-8') as f:
                arr = json.load(f)
            return jsonify(arr if isinstance(arr, list) else [])
        if MODELS_CACHE:
            return jsonify(MODELS_CACHE)
        items = []
        meta = {}
        autostart_set = set()
        try:
            if os.path.isfile(MODELS_CFG_PATH):
                with open(MODELS_CFG_PATH, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                meta = cfg.get('meta') or {}
                autostart_set = set(cfg.get('autostart') or [])
        except Exception:
            meta = {}
        if os.path.isdir(MODELS_DIR):
            for fn in sorted(os.listdir(MODELS_DIR)):
                if fn.startswith('model_') and fn.endswith('.py') and fn != 'model_manager.py':
                    m = meta.get(fn) or {}
                    items.append({ 'name': fn, 'title': (m.get('title') or fn), 'description': (m.get('description') or f"简易模型 {fn.split('_')[1].split('.')[0]}"), 'status': 'stopped', 'pid': None, 'autostart': (fn in autostart_set) })
        return jsonify(items)
    except Exception:
        return jsonify([])

@app.route('/api/models/command', methods=['POST'])
def api_models_command():
    try:
        data = request.get_json(silent=True) or {}
        act = str(data.get('action') or '')
        name = str(data.get('name') or '')
        if not act or not name:
            return jsonify({'error':'bad request'}), 400
        arr = []
        if os.path.isfile(MODELS_CMD_PATH):
            try:
                with open(MODELS_CMD_PATH, 'r', encoding='utf-8') as f:
                    arr = json.load(f)
            except Exception:
                arr = []
        if not isinstance(arr, list):
            arr = []
        arr.append({'action': act, 'name': name})
        tmp = MODELS_CMD_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(arr, f, ensure_ascii=False)
        os.replace(tmp, MODELS_CMD_PATH)
        return jsonify({'ok': True})
    except Exception:
        return jsonify({'error':'internal'}), 500

@app.route('/api/models/notify', methods=['POST'])
def api_models_notify():
    global MODELS_CACHE
    data = request.get_json(silent=True) or {}
    items = data if isinstance(data, list) else data.get('models')
    if not isinstance(items, list):
        return jsonify({'error':'bad payload'}), 400
    MODELS_CACHE = items
    try:
        tmp = MODELS_STATUS_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False)
        os.replace(tmp, MODELS_STATUS_PATH)
    except Exception:
        pass
    try:
        broadcast({ 'models': items })
    except Exception:
        pass
    return jsonify({'ok': True})

@app.route('/api/models/download/<path:name>')
def api_models_download(name):
    try:
        safe = name
        if '/' in safe or '..' in safe:
            return jsonify({'error':'bad name'}), 400
        path = os.path.join(MODELS_DIR, safe)
        if not os.path.isfile(path):
            return jsonify({'error':'not found'}), 404
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        resp = Response(content)
        resp.headers['Content-Type'] = 'text/plain; charset=utf-8'
        resp.headers['Content-Disposition'] = f'attachment; filename="{safe}"'
        return resp
    except Exception:
        return jsonify({'error':'download failed'}), 500


@app.route('/api/scripts/logs')
def api_script_logs():
    sid = request.args.get('script_id', type=int)
    conn = get_db_connection()
    cur = None
    try:
        cur = conn.cursor()
        if sid:
            cur.execute("SELECT id,status,output,started_at,finished_at FROM script_exec_log WHERE script_id=%s ORDER BY id DESC LIMIT 20", (sid,))
        else:
            cur.execute("SELECT id,script_id,status,output,started_at,finished_at FROM script_exec_log ORDER BY id DESC LIMIT 20")
        rows = cur.fetchall()
        if sid:
            return jsonify([{'id':r[0],'status':r[1],'output':r[2],'started_at':r[3].strftime('%Y-%m-%d %H:%M:%S'),'finished_at': (r[4].strftime('%Y-%m-%d %H:%M:%S') if r[4] else None)} for r in rows])
        return jsonify([{'id':r[0],'script_id':r[1],'status':r[2],'output':r[3],'started_at':r[4].strftime('%Y-%m-%d %H:%M:%S'),'finished_at': (r[5].strftime('%Y-%m-%d %H:%M:%S') if r[5] else None)} for r in rows])
    except Exception as e:
        print(f"[SCRIPT] 日志查询失败: {e}")
        return jsonify([])
    finally:
        if cur:
            cur.close()
        if conn:
            close_db_connection(conn)

@app.route('/api/scripts/download/<int:script_id>')
def api_scripts_download(script_id):
    conn = get_db_connection()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT name, lang, content FROM scripts WHERE id=%s", (script_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error':'not found'}), 404
        name, lang, content = row
        ext = 'py' if str(lang).lower() == 'py' else 'c'
        filename = f"{name}.{ext}"
        resp = Response(content)
        resp.headers['Content-Type'] = 'text/plain; charset=utf-8'
        resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        return resp
    except Exception as e:
        print(f"[SCRIPT] 下载失败: {e}")
        return jsonify({'error':'download failed'}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            close_db_connection(conn)

@app.route('/api/scripts/stop/<int:script_id>', methods=['POST'])
def api_scripts_stop(script_id):
    conn = get_db_connection()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO script_commands(script_id, cmd, status) VALUES(%s, %s, %s)", (script_id, 'stop', 'pending'))
        conn.commit()
        return jsonify({'accepted': True})
    except Exception as e:
        print(f"[SCRIPT] 停止入队失败: {e}")
        return jsonify({'error':'enqueue failed'}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            close_db_connection(conn)

@app.route('/api/scripts/<int:script_id>', methods=['DELETE'])
def api_scripts_delete(script_id):
    conn = get_db_connection()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM scripts WHERE id=%s", (script_id,))
        conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        print(f"[SCRIPT] 删除失败: {e}")
        return jsonify({'error':'delete failed'}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            close_db_connection(conn)


@app.route('/api/capture', methods=['POST'])
def api_capture():
    # 1. 温度
    temp_res = read_temperature()
    if "error" in temp_res:
        return jsonify(temp_res), 503
    
    # 2. 图像
    img_res = capture_image()
    if "error" in img_res:
        return jsonify(img_res), 503
    
    # 存库
    if not save_sensor_data(
        temp_res["temp"],
        img_res["image_path"]
    ):
        return jsonify({"error": "database save failed"}), 500
    
    return jsonify({
        "temperature": temp_res["temp"],
        "image_path": img_res["image_path"],
        "status": "success"
    })


@app.route('/api/ingest', methods=['POST'])
def api_ingest():
    global LATEST_CACHE
    """接收外部模拟器发送的JSON数据并入库。
    支持部分字段：device_id, timestamp_ms, temperature_c?, light?, frame{width,height,pixels}?, checksum_frame?
    """
    data = request.get_json(silent=True, force=True)
    if not data:
        return jsonify({"error": "invalid json"}), 400

    device_id = str(data.get("device_id", "unknown"))
    has_temp = ("temperature_c" in data)
    has_light = ("light" in data)
    has_frame = ("frame" in data)
    temp_c = data.get("temperature_c") if has_temp else None
    light_val = data.get("light") if has_light else None
    frame_obj = data.get("frame") if has_frame else None
    checksum_sent = str(data.get("checksum_frame", "")).lower()

    # 温度校验与规范化
    temp_val = None
    if has_temp:
        try:
            temp_val = round(float(temp_c), 1)
        except Exception:
            return jsonify({"error": "temperature invalid"}), 400

    # 帧转换与校验
    img_path = None
    if isinstance(frame_obj, dict):
        img_res = image_from_pixels(frame_obj)
        if "error" in img_res:
            return jsonify(img_res), 400
        if checksum_sent and img_res.get("checksum_frame_calc") and (checksum_sent != img_res["checksum_frame_calc"]):
            return jsonify({"error": "checksum mismatch", "calc": img_res["checksum_frame_calc"], "sent": checksum_sent}), 400
        img_path = img_res["image_path"]

    # 入库
    if img_path is not None and temp_val is not None:
        ok = save_sensor_data(temp_val, img_path, light=light_val)
        if not ok:
            return jsonify({"error": "database save failed"}), 500
    
    ts = int(time.time())
    if temp_val is not None:
        HEARTBEAT['temp'] = ts
    if has_light and light_val is not None:
        HEARTBEAT['light'] = ts
    if img_path:
        HEARTBEAT['image'] = ts

    cur = LATEST_CACHE or {}
    if temp_val is not None:
        cur['temperature'] = temp_val
    if has_light:
        cur['light'] = light_val
    if img_path is not None:
        cur['image_path'] = img_path
    cur['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur['sensor_status'] = build_status()
    LATEST_CACHE = cur
    try:
        broadcast(cur)
    except Exception:
        pass

    return jsonify({
        "status": "success",
        "device_id": device_id,
        "temperature": temp_val,
        "light": light_val,
        "image_path": img_path,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })


# ==================== 启动 ====================
if __name__ == '__main__':
    print("=" * 50)
    print("🚀 昆仑哨兵·实验室多模态监控系统启动中...")
    print(f"📁 BASE_DIR: {BASE_DIR}")
    print(f"📸 IMAGES_DIR: {IMAGES_DIR}")
    print(f"🗃️  DB: host={DB_CONFIG['host']} port={DB_CONFIG['port']} db={DB_CONFIG['database']} user={DB_CONFIG['user']}")
    print("=" * 50)
    print(f"🌐 Web: 0.0.0.0:{ENV_FLASK_PORT}")

    # 初始化数据库（幂等）；若失败也继续启动，避免应用直接退出
    try:
        if not init_database():
            print("⚠️ 数据库初始化失败，应用仍将启动（页面可能显示数据库错误）")
    except Exception as e:
        print(f"⚠️ 数据库初始化异常: {e}，继续启动应用")

    def init_connection_pool():
        global PG_POOL
        try:
            PG_POOL = pg_pool.SimpleConnectionPool(1, 10, **{k: v for k, v in DB_CONFIG.items() if v is not None})
            print("[DB] 连接池启用")
        except Exception as e:
            PG_POOL = None
            print(f"[DB] 连接池启用失败: {e}")

    try:
        init_connection_pool()
    except Exception as e:
        print(f"[DB] 连接池初始化失败: {e}")

    # 启动
    try:
        def updater():
            global LATEST_CACHE
            while True:
                try:
                    latest = get_latest_data()
                    if latest is not None:
                        LATEST_CACHE = latest
                except Exception:
                    pass
                time.sleep(1)
        th = threading.Thread(target=updater, daemon=True)
        th.start()
        print("[APP] 正在启动Flask应用...")
        app.run(host='0.0.0.0', port=ENV_FLASK_PORT, debug=False, threaded=True)
    except Exception as e:
        print(f"[APP] Flask启动失败: {e}")
        import traceback
        traceback.print_exc()
@app.route('/api/model_output', methods=['POST'])
def api_model_output():
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '')
    output = data.get('output')
    try:
        broadcast({'model_output': {'name': name, 'output': output}})
    except Exception:
        pass
    return jsonify({'ok': True})
