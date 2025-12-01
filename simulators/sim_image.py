import json
import time
import math
import socket
import os

HOST = os.getenv("RELAY_HOST", "127.0.0.1")
PORT = int(os.getenv("RELAY_PORT", "9999"))
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def gen_frame(w=64, h=48, t=None):
    pixels = []
    for y in range(h):
        row = []
        for x in range(w):
            a = 128 + int(127 * math.sin((x + (t or 0)) * 0.1))
            b = 128 + int(127 * math.cos((y + (t or 0)) * 0.1))
            r = max(0, min(255, a))
            g = max(0, min(255, b))
            b2 = (r + g) // 2
            row.append({"r": r, "g": g, "b": b2})
        pixels.append(row)
    return {"width": w, "height": h, "pixels": pixels}

def send_once():
    ts = int(time.time() * 1000)
    payload = {
        "device_id": "sim-image-1",
        "timestamp_ms": ts,
        "frame": gen_frame(64, 48, t=ts // 100)
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