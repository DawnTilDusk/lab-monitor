import json
import time
import socket
import os
import numpy as np
import cv2

HOST = os.getenv("RELAY_HOST", "127.0.0.1")
PORT = int(os.getenv("RELAY_PORT", "9999"))
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

BASE = os.getenv("LAB_DIR", os.path.join(os.path.dirname(__file__), '..'))
IMAGES_DIR = os.path.join(BASE, "static", "images")
os.makedirs(IMAGES_DIR, exist_ok=True)

def gen_image(w=640, h=480, t=None):
    x = np.linspace(0, 2*np.pi, w)
    y = np.linspace(0, 2*np.pi, h)
    xv, yv = np.meshgrid(x, y)
    a = 0.5 + 0.5*np.sin(xv + (t or 0)*0.001)
    b = 0.5 + 0.5*np.cos(yv + (t or 0)*0.001)
    r = (a * 255.0).astype(np.uint8)
    g = (b * 255.0).astype(np.uint8)
    b2 = (((r.astype(np.uint16) + g.astype(np.uint16)) // 2)).astype(np.uint8)
    img = np.stack([r, g, b2], axis=2)
    img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    return img_bgr

def send_once():
    ts_ms = int(time.time() * 1000)
    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime(ts_ms/1000.0))
    ms = ts_ms % 1000
    filename = f"relay_cam_{ts}_{ms:03d}.jpg"
    fp = os.path.join(IMAGES_DIR, filename)
    img = gen_image(640, 480, t=ts_ms)
    cv2.imwrite(fp, img)
    payload = {
        "device_id": "sim-image-1",
        "timestamp_ms": ts_ms,
        "image_path": f"/static/images/{filename}"
    }
    data = json.dumps(payload).encode("utf-8")
    try:
        sock.sendto(data, (HOST, PORT))
    except Exception:
        pass

if __name__ == "__main__":
    while True:
        try:
            send_once()
        except Exception:
            pass
        time.sleep(1)
