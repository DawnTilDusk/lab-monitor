import os
import sys
import json
import time
from datetime import datetime
import stat
import pwd
import grp

def get_out_dir():
    d = os.getcwd()
    os.makedirs(d, exist_ok=True)
    return d

def parse_device():
    dev = os.environ.get('CAMERA_DEVICE')
    if not dev:
        dev = sys.argv[1] if len(sys.argv) > 1 else '/dev/video0'
    idx = None
    if dev.startswith('/dev/video'):
        s = dev.replace('/dev/video', '')
        try:
            idx = int(s)
        except Exception:
            idx = 0
    return dev, idx

def save_path(out_dir):
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'test_{ts}.jpg'
    return os.path.join(out_dir, filename)

def try_opencv(idx, w=640, h=480):
    try:
        import cv2
    except Exception:
        return None
    cap = cv2.VideoCapture(idx, apiPreference=getattr(__import__('cv2'), 'CAP_V4L2', 200))
    if not cap.isOpened():
        cap = __import__('cv2').VideoCapture(idx)
        if not cap.isOpened():
            return None
    cap.set(__import__('cv2').CAP_PROP_FRAME_WIDTH, w)
    cap.set(__import__('cv2').CAP_PROP_FRAME_HEIGHT, h)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return None
    return frame

def main():
    out_dir = get_out_dir()
    dev, idx = parse_device()
    try:
        st = os.stat(dev)
        dev_group = grp.getgrgid(st.st_gid).gr_name
    except Exception:
        dev_group = ''
    user = pwd.getpwuid(os.getuid()).pw_name
    groups = [grp.getgrgid(g).gr_name for g in os.getgroups()]
    diag = {
        'device': dev,
        'device_group': dev_group,
        'user': user,
        'user_in_video_group': ('video' in groups)
    }
    frame = None
    backend = ''
    if idx is not None:
        frame = try_opencv(idx)
        backend = 'opencv' if frame is not None else ''
    if frame is None:
        path = save_path(out_dir)
        rc = os.system(f'fswebcam -q --no-banner -d {dev} -r 640x480 {path}')
        if rc == 0 and os.path.exists(path):
            print(json.dumps({'image_path': path, 'device': dev, 'backend': 'fswebcam', 'diagnostics': diag}))
            return
        print(json.dumps({'error': 'capture failed', 'device': dev, 'fswebcam_missing': True, 'diagnostics': diag}))
        return
    try:
        import cv2
        path = save_path(out_dir)
        cv2.imwrite(path, frame)
        print(json.dumps({'image_path': path, 'device': dev, 'backend': backend, 'diagnostics': diag}))
    except Exception as e:
        print(json.dumps({'error': 'save failed', 'device': dev, 'diagnostics': diag}))

if __name__ == '__main__':
    main()