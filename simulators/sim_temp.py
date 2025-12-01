import json
import time
import socket
import os

HOST = os.getenv("RELAY_HOST", "127.0.0.1")
PORT = int(os.getenv("RELAY_PORT", "9999"))
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def send_once():
    payload = {
        "device_id": "sim-temp-1",
        "timestamp_ms": int(time.time() * 1000),
        "temperature_c": round(24.5 + (time.time() % 5), 1)
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