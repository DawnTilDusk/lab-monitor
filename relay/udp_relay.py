# relay/udp_relay.py
# 
# UDP协议相关：
# - socket: 用于创建UDP套接字，监听来自客户端的数据包
# - 数据包结构示例：
#   {
#     "type": "sensor",           # 或 "model"
#     "temperature_c": 25.5,      # 温度值（摄氏度）
#     "light": 100,               # 光照强度
#     "frame": {                  # 图像帧数据（可选）
#       "width": 32,
#       "height": 32,
#       "pixels": [
#         [{"r": 255, "g": 0, "b": 0}, ...],  # 第一行像素
#         ...
#       ]
#     },
#     "image_path": "/path/to/image.jpg",  # 图片路径（可选）
#     "timestamp_ms": 1234567890123,       # 时间戳（毫秒，可选）
#     "name": "model_name",                # 模型名称（当type为model时）
#     "output": {...}                      # 模型输出（当type为model时）
#   }
# 
# UDP数据对应的数据库表结构（分表设计）：
# 
# 表1: temperature_data (温度数据表)
# +----------------+--------------+------+-----+---------+----------------+
# | Field          | Type         | Null | Key | Default | Extra          |
# +----------------+--------------+------+-----+---------+----------------+
# | id             | SERIAL       | NO   | PRI | NULL    | auto_increment |
# | timestamp      | TIMESTAMPTZ  | YES  |     | NOW()   |                |
# | value          | REAL         | NO   |     | NULL    |                |
# | device_id      | VARCHAR      | YES  |     | temp_main|               |
# | unit           | VARCHAR      | YES  |     | C       |                |
# | created_at     | TIMESTAMPTZ  | YES  |     | NOW()   |                |
# +----------------+--------------+------+-----+---------+----------------+
# 
# 表2: image_data (图像数据表)
# +----------------+--------------+------+-----+---------+----------------+
# | Field          | Type         | Null | Key | Default | Extra          |
# +----------------+--------------+------+-----+---------+----------------+
# | id             | SERIAL       | NO   | PRI | NULL    | auto_increment |
# | timestamp      | TIMESTAMPTZ  | YES  |     | NOW()   |                |
# | image_path     | TEXT         | NO   |     | NULL    |                |
# | width          | INTEGER      | YES  |     | NULL    |                |
# | height         | INTEGER      | YES  |     | NULL    |                |
# | device_id      | VARCHAR      | YES  |     | camera_main|             |
# | file_size      | BIGINT       | YES  |     | NULL    |                |
# | created_at     | TIMESTAMPTZ  | YES  |     | NOW()   |                |
# | bubble         | BOOLEAN      | YES  |     | FALSE   |                |
# +----------------+--------------+------+-----+---------+----------------+
# 
# 表3: light_data (光敏数据表)
# +----------------+--------------+------+-----+---------+----------------+
# | Field          | Type         | Null | Key | Default | Extra          |
# +----------------+--------------+------+-----+---------+----------------+
# | id             | SERIAL       | NO   | PRI | NULL    | auto_increment |
# | timestamp      | TIMESTAMPTZ  | YES  |     | NOW()   |                |
# | value          | INTEGER      | YES  |     | NULL    |                |
# | device_id      | VARCHAR      | YES  |     | light_main|              |
# | unit           | VARCHAR      | YES  |     | lux     |                |
# | created_at     | TIMESTAMPTZ  | YES  |     | NOW()   |                |
# +----------------+--------------+------+-----+---------+----------------+
# 
# 表4: model_outputs (模型输出表)
# +-------+---------+------+-----+---------+----------------+
# | Field | Type    | Null | Key | Default | Extra          |
# +-------+---------+------+-----+---------+----------------+
# | id    | SERIAL  | NO   | PRI | NULL    | auto_increment |
# | name  | VARCHAR | NO   |     | NULL    |                |
# | output| TEXT    | NO   |     | NULL    |                |
# | created_at | TIMESTAMPTZ | YES |     | NOW()   |                |
# +-------+---------+------+-----+---------+----------------+

import os
import json
import socket
import time
import urllib.request
from datetime import datetime
import psycopg2
import numpy as np
import cv2
import threading
import time
import socket

# 从环境变量获取或设置默认值
LAB_DIR = os.getenv("LAB_DIR", "/home/openEuler/lab_monitor")  # 实验室监控主目录
IMAGES_DIR = os.path.join(LAB_DIR, "static", "images")  # 存放图片的目录
os.makedirs(IMAGES_DIR, exist_ok=True)  # 确保图片目录存在
IMAGE_TTL_SEC = int(os.getenv("IMAGE_TTL_SEC", "60"))  # 图片在本地的生存时间（秒）
IDLE_IMAGE_SEC = int(os.getenv("IDLE_IMAGE_SEC", "15"))  # 空闲时多久生成一张图片（秒）

# 数据库连接配置
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "7654"))
DB_NAME = os.getenv("DB_NAME", "lab_monitor")
DB_USER = os.getenv("DB_USER", "labuser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "LabUser@12345")

# 加载可接受的UDP数据键配置
RELAY_DIR = os.path.join(LAB_DIR, "relay")
CONFIG_FILE_PATH = os.path.join(RELAY_DIR, "udp_config.json")

def load_config():
    try:
        with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
            print(f"[CONFIG] 从 {CONFIG_FILE_PATH} 加载配置")
            return config
    except FileNotFoundError:
        alt_path = os.path.join(RELAY_DIR, "config.json")
        try:
            with open(alt_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                print(f"[CONFIG] 主配置缺失，已回退至 {alt_path}")
                return config
        except Exception as e:
            print(f"[CONFIG] 配置文件缺失或不可读: {CONFIG_FILE_PATH}，回退失败: {e}")
            raise
    except Exception as e:
        print(f"[CONFIG] 加载配置失败: {e}")
        raise e

def db_connect():
    """
    创建并返回一个数据库连接对象
    """
    cfg = {
        "host": DB_HOST,
        "port": DB_PORT,
        "database": DB_NAME,
        "user": DB_USER,
        "password": DB_PASSWORD,
        "sslmode": "disable",
    }
    conn = psycopg2.connect(**cfg)
    # 设置时区为中国标准时间
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'Asia/Shanghai'")
    return conn


def schedule_delete(image_path):
    """
    计划在指定时间后删除图片文件
    
    :param image_path: 图片路径
    """
    try:
        name = os.path.basename(str(image_path or ""))
        if not name:
            return
        fp = os.path.join(IMAGES_DIR, name)
    except Exception:
        return
    
    def job():
        """
        延迟执行的删除任务
        """
        try:
            time.sleep(IMAGE_TTL_SEC)
            try:
                os.remove(fp)
            except Exception:
                pass
        except Exception:
            pass
    
    try:
        threading.Thread(target=job, daemon=True).start()
    except Exception:
        pass

def save_image(frame):
    """
    将帧数据保存为图片文件
    
    :param frame: 包含图像信息的字典，格式如注释中所示
    :return: 保存后的图片路径（相对于静态资源目录）
    """
    w = int(frame.get("width", 0))
    h = int(frame.get("height", 0))
    pixels = frame.get("pixels")
    if w <= 0 or h <= 0 or not isinstance(pixels, list):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = os.path.join(IMAGES_DIR, f"relay_{ts}.png")
        arr = np.zeros((32, 32, 3), dtype=np.uint8)
        cv2.imwrite(fp, arr)
        return f"/static/images/{os.path.basename(fp)}"
    
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(h):
        row = pixels[y]
        for x in range(w):
            p = row[x]
            r = int(p.get("r", 0)) & 0xFF
            g = int(p.get("g", 0)) & 0xFF
            b = int(p.get("b", 0)) & 0xFF
            arr[y, x, 0] = r
            arr[y, x, 1] = g
            arr[y, x, 2] = b
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fp = os.path.join(IMAGES_DIR, f"relay_{ts}.png")
    arr_bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    scale = 10
    dst = cv2.resize(arr_bgr, (w * scale, h * scale), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(fp, dst)
    return f"/static/images/{os.path.basename(fp)}"

def capture_uvc_image():
    """
    从UVC摄像头设备捕获一张图片
    
    :return: 保存的图片路径，失败则返回None
    """
    dev = os.getenv("CAMERA_DEVICE", "/dev/video0")
    idx = None
    try:
        if str(dev).startswith("/dev/video"):
            idx = int(str(dev).replace("/dev/video", ""))
        else:
            idx = int(str(dev))
    except Exception:
        idx = 0
    
    try:
        cap = cv2.VideoCapture(idx, apiPreference=getattr(cv2, 'CAP_V4L2', 200))
        if not cap.isOpened():
            cap = cv2.VideoCapture(idx)
            if not cap.isOpened():
                return None
        
        ret, frame = cap.read()
        cap.release()
        if not ret:
            return None
        
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = os.path.join(IMAGES_DIR, f"relay_cam_{ts}.jpg")
        cv2.imwrite(fp, frame)
        return f"/static/images/{os.path.basename(fp)}"
    except Exception:
        return None

last_image_at = 0.0

def ensure_image_uptime():
    global last_image_at
    while True:
        try:
            time.sleep(5)
            now = time.time()
            if IDLE_IMAGE_SEC > 0 and (now - last_image_at) > IDLE_IMAGE_SEC:
                p = capture_uvc_image()
                if p:
                    try:
                        conn = db_connect()
                        insert_image_db(conn, p, bubble=True)
                        try:
                            conn.close()
                        except Exception:
                            pass
                    except Exception:
                        pass
                    payload = { "temperature": None, "light": None, "image_path": p }
                    schedule_delete(p)
                    notify_backend(payload)
                    last_image_at = now
        except Exception:
            time.sleep(1)

def insert_temperature_db(conn, temp_value, device_id='temp_main', ts_ms=None):
    """
    插入温度数据到分表
    :param conn: 数据库连接
    :param temp_value: 温度值
    :param device_id: 设备ID
    :param ts_ms: 时间戳（毫秒）
    """
    cur = conn.cursor()
    try:
        if ts_ms is None:
            # 使用当前时间插入温度数据
            cur.execute(
                "INSERT INTO temperature_data (value, device_id) VALUES (%s, %s)",
                (float(temp_value) if temp_value is not None else 0.0, str(device_id)),
            )
        else:
            # 使用指定时间戳插入温度数据
            cur.execute(
                "INSERT INTO temperature_data (value, device_id, timestamp) VALUES (%s, %s, to_timestamp(%s/1000.0) AT TIME ZONE 'Asia/Shanghai')",
                (float(temp_value) if temp_value is not None else 0.0, str(device_id), int(ts_ms)),
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        cur.close()

def insert_image_db(conn, image_path, device_id='camera_main', bubble=False, ts_ms=None):
    """
    插入图像数据到分表
    :param conn: 数据库连接
    :param image_path: 图像路径
    :param device_id: 设备ID
    :param bubble: 是否为定时生成的图片
    :param ts_ms: 时间戳（毫秒）
    """
    cur = conn.cursor()
    try:
        if ts_ms is None:
            # 使用当前时间插入图像数据
            cur.execute(
                "INSERT INTO image_data (image_path, device_id, bubble) VALUES (%s, %s, %s)",
                (str(image_path), str(device_id), bool(bubble)),
            )
        else:
            # 使用指定时间戳插入图像数据
            cur.execute(
                "INSERT INTO image_data (image_path, device_id, bubble, timestamp) VALUES (%s, %s, %s, to_timestamp(%s/1000.0) AT TIME ZONE 'Asia/Shanghai')",
                (str(image_path), str(device_id), bool(bubble), int(ts_ms)),
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        cur.close()

def insert_light_db(conn, light_value, device_id='light_main', ts_ms=None):
    """
    插入光敏数据到分表
    :param conn: 数据库连接
    :param light_value: 光敏值
    :param device_id: 设备ID
    :param ts_ms: 时间戳（毫秒）
    """
    cur = conn.cursor()
    try:
        if ts_ms is None:
            # 使用当前时间插入光敏数据
            cur.execute(
                "INSERT INTO light_data (value, device_id) VALUES (%s, %s)",
                (int(light_value) if light_value is not None else 0, str(device_id)),
            )
        else:
            # 使用指定时间戳插入光敏数据
            cur.execute(
                "INSERT INTO light_data (value, device_id, timestamp) VALUES (%s, %s, to_timestamp(%s/1000.0) AT TIME ZONE 'Asia/Shanghai')",
                (int(light_value) if light_value is not None else 0, str(device_id), int(ts_ms)),
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        cur.close()

def insert_db(conn, temp, image_path, light, ts_ms=None, bubble=0):
    """
    【兼容函数】插入数据到原合并表（为了向后兼容）
    """
    cur = conn.cursor()
    try:
        if ts_ms is None:
            cur.execute(
                "INSERT INTO sensor_data (temperature, image_path, light, bubble_count) VALUES (%s,%s,%s,%s)",
                (float(temp) if temp is not None else 0.0, str(image_path), None if light is None else int(light), int(bubble)),
            )
        else:
            cur.execute(
                "INSERT INTO sensor_data (temperature, image_path, light, timestamp, bubble_count) VALUES (%s,%s,%s, to_timestamp(%s/1000.0) AT TIME ZONE 'Asia/Shanghai', %s)",
                (float(temp) if temp is not None else 0.0, str(image_path), None if light is None else int(light), int(ts_ms), int(bubble)),
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        cur.close()

def insert_model_db(conn, name, output_text):
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO model_outputs (name, output) VALUES (%s, %s)",
            (str(name), str(output_text)),
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        cur.close()

def notify_backend(payload):
    url = os.getenv("BACKEND_NOTIFY_URL", "http://127.0.0.1:5000/api/relay_notify")
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass

def notify_backend_model(name, output_obj):
    url = os.getenv("BACKEND_MODEL_URL", "http://127.0.0.1:5000/api/model_output")
    payload = { 'name': name, 'output': output_obj }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass

def main():
    """
    主函数：创建UDP套接字，接收数据并处理
    """
    # 在主函数开始时加载配置
    config = load_config()
    
    conn = None
    while True:
        try:
            conn = db_connect()
            break
        except Exception:
            time.sleep(1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((os.getenv("RELAY_HOST", "0.0.0.0"), int(os.getenv("RELAY_PORT", "9999"))))
    try:
        threading.Thread(target=ensure_image_uptime, daemon=True).start()
    except Exception:
        pass
    while True:
        try:
            data, addr = sock.recvfrom(65507)
            try:
                j = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            if j.get("type") == "model" or (j.get("name") and (j.get("output") or j.get("result"))):
                # 从配置中获取模型数据的允许键
                valid_model_keys = config.get("valid_keys", {}).get("model", [])
                # 过滤掉无效的键
                filtered_j = {k: v for k, v in j.items() if k in valid_model_keys}
                
                name = filtered_j.get("name") or filtered_j.get("model_name")
                out_obj = filtered_j.get("output") if filtered_j.get("output") is not None else filtered_j.get("result")
                out_text = json.dumps(out_obj, ensure_ascii=False) if isinstance(out_obj, (dict, list)) else str(out_obj)
                if conn is None:
                    try:
                        conn = db_connect()
                    except Exception:
                        pass
                if conn is not None:
                    insert_model_db(conn, name or "unknown", out_text)
                notify_backend_model(name or "unknown", out_obj)
                continue
            
            # 从配置中获取传感器数据的允许键
            valid_sensor_keys = config.get("valid_keys", {}).get("sensor", [])
            # 过滤掉无效的键
            j = {k: v for k, v in j.items() if k in valid_sensor_keys}
            
            # 从配置中获取传感器字段映射，并动态解析数据
            sensor_fields = config.get("sensor_fields", {})
            # 创建一个字典来存储解析后的传感器数据
            parsed_sensor_data = {}
            # 遍历配置中的映射关系
            for logical_name, json_key in sensor_fields.items():
                # 从接收到的 JSON 数据 j 中获取对应的值
                parsed_sensor_data[logical_name] = j.get(json_key)
            
            # 现在可以从 parsed_sensor_data 字典中获取各个值
            temp_c = parsed_sensor_data.get("temperature")  # 温度
            light = parsed_sensor_data.get("light")        # 光照
            frame = parsed_sensor_data.get("frame")        # 图像帧数据
            image_path = parsed_sensor_data.get("image_path") # 图像路径
            ts_ms = parsed_sensor_data.get("timestamp")    # 时间戳（毫秒）
            
            if image_path:
                try:
                    name = os.path.basename(str(image_path))
                    fp = os.path.join(IMAGES_DIR, name)
                    if not os.path.exists(fp):
                        if isinstance(frame, dict):
                            image_path = save_image(frame)
                        else:
                            tmp = capture_uvc_image()
                            image_path = tmp if tmp else save_image({})
                except Exception:
                    pass
            else:
                if isinstance(frame, dict):
                    image_path = save_image(frame)
                else:
                    tmp = capture_uvc_image()
                    image_path = tmp if tmp else save_image({})
            
            if conn is None:
                try:
                    conn = db_connect()
                except Exception:
                    pass
            
            if conn is not None:
                # 分别插入不同类型的数据到对应的分表
                # 插入温度数据（如果存在）
                if temp_c is not None:
                    insert_temperature_db(conn, temp_c, ts_ms=ts_ms)
                
                # 插入图像数据（如果存在）
                if image_path:
                    bubble = (temp_c is None)  # 如果温度为None，说明这是定时生成的图片
                    insert_image_db(conn, image_path, bubble=bubble, ts_ms=ts_ms)
                
                # 插入光敏数据（如果存在）
                if light is not None:
                    insert_light_db(conn, light, ts_ms=ts_ms)
                
                # 为了向后兼容，同时插入到原表
                insert_db(conn, temp_c if temp_c is not None else 0.0, image_path, light, ts_ms, 0 if temp_c is not None else 1)
            
            payload = {"temperature": temp_c, "light": light, "image_path": image_path, "timestamp_ms": ts_ms}
            schedule_delete(image_path)
            notify_backend(payload)
            try:
                last_image_at = time.time()
            except Exception:
                pass
        except Exception:
            time.sleep(0.1)

if __name__ == "__main__":
    main()
