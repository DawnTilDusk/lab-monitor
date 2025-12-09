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

LAB_DIR = os.getenv("LAB_DIR", "/home/openEuler/lab_monitor")
IMAGES_DIR = os.path.join(LAB_DIR, "static", "images")
os.makedirs(IMAGES_DIR, exist_ok=True)
IMAGE_TTL_SEC = int(os.getenv("IMAGE_TTL_SEC", "60"))
IDLE_IMAGE_SEC = int(os.getenv("IDLE_IMAGE_SEC", "15"))

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "7654"))
DB_NAME = os.getenv("DB_NAME", "lab_monitor")
DB_USER = os.getenv("DB_USER", "labuser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "LabUser@12345")

def db_connect():
    cfg = {
        "host": DB_HOST,
        "port": DB_PORT,
        "database": DB_NAME,
        "user": DB_USER,
        "password": DB_PASSWORD,
        "sslmode": "disable",
    }
    return psycopg2.connect(**cfg)

def schedule_delete(image_path):
    try:
        name = os.path.basename(str(image_path or ""))
        if not name:
            return
        fp = os.path.join(IMAGES_DIR, name)
    except Exception:
        return
    def job():
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
                        insert_db(conn, 0.0, p, None, None)
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

def insert_db(conn, temp, image_path, light, ts_ms=None):
    cur = conn.cursor()
    try:
        if ts_ms is None:
            cur.execute(
                "INSERT INTO sensor_data (temperature, image_path, light) VALUES (%s,%s,%s)",
                (float(temp) if temp is not None else 0.0, str(image_path), None if light is None else int(light)),
            )
        else:
            cur.execute(
                "INSERT INTO sensor_data (temperature, image_path, light, timestamp) VALUES (%s,%s,%s, to_timestamp(%s/1000.0))",
                (float(temp) if temp is not None else 0.0, str(image_path), None if light is None else int(light), int(ts_ms)),
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
                name = j.get("name") or j.get("model_name")
                out_obj = j.get("output") if j.get("output") is not None else j.get("result")
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
            temp_c = j.get("temperature_c")
            light = j.get("light")
            frame = j.get("frame")
            image_path = j.get("image_path")
            ts_ms = j.get("timestamp_ms")
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
                insert_db(conn, temp_c if temp_c is not None else 0.0, image_path, light, ts_ms)
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
