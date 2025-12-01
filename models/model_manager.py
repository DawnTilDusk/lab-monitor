import os, json, time, subprocess, shlex, urllib.request, socket

RECENT_FINISH_SEC = int(os.environ.get('MODEL_FINISH_HOLD_SEC', '5'))

BASE = os.environ.get('LAB_DIR', os.path.join(os.path.dirname(__file__), '..'))
MODELS_DIR = os.path.join(BASE, 'models')
RUNTIME_DIR = os.path.join(BASE, 'runtime')
os.makedirs(RUNTIME_DIR, exist_ok=True)
CFG_PATH = os.path.join(MODELS_DIR, 'config.json')
STATUS_PATH = os.path.join(RUNTIME_DIR, 'models_status.json')
CMD_PATH = os.path.join(RUNTIME_DIR, 'models_commands.json')

def load_cfg():
    try:
        with open(CFG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return { 'autostart': [] }

def save_cfg(cfg):
    tmp = CFG_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CFG_PATH)

def list_models():
    arr = []
    for fn in sorted(os.listdir(MODELS_DIR)):
        if fn.startswith('model_') and fn.endswith('.py') and fn != 'model_manager.py':
            arr.append(fn)
    return arr

procs = {}
last_status = {}
def meta_for(name):
    cfg = load_cfg()
    m = (cfg.get('meta') or {}).get(name) or {}
    t = m.get('title') or name
    d = m.get('description') or f"简易模型 {name.split('_')[1].split('.')[0]}"
    return t, d

def start_model(name):
    path = os.path.join(MODELS_DIR, name)
    if not os.path.isfile(path):
        return False
    if name in procs and procs[name] and procs[name].poll() is None:
        return True
    p = subprocess.Popen(['python3', '-u', path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    procs[name] = p
    last_status[name] = { 'status': 'running', 'pid': p.pid, 'started_at': int(time.time()) }
    try:
        def reader():
            try:
                host = os.environ.get('RELAY_HOST','127.0.0.1')
                port = int(os.environ.get('RELAY_PORT','9999'))
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                while True:
                    ln = p.stdout.readline()
                    if not ln:
                        break
                    txt = ln.decode('utf-8', errors='ignore').strip()
                    if not txt:
                        continue
                    payload = None
                    try:
                        payload = json.loads(txt)
                    except Exception:
                        payload = {'text': txt}
                    try:
                        msg = json.dumps({'type':'model','name': name, 'output': payload}, ensure_ascii=False).encode('utf-8')
                        sock.sendto(msg, (host, port))
                    except Exception:
                        pass
                try:
                    last_status[name] = { 'status': 'stopped', 'pid': None, 'finished_at': int(time.time()) }
                    snapshot_status()
                except Exception:
                    pass
            except Exception:
                pass
        import threading
        threading.Thread(target=reader, daemon=True).start()
    except Exception:
        pass
    return True

def stop_model(name):
    if name in procs and procs[name]:
        try:
            p = procs[name]
            p.terminate()
            try:
                p.wait(timeout=2)
            except Exception:
                p.kill()
        except Exception:
            pass
        procs.pop(name, None)
        last_status[name] = { 'status': 'stopped', 'pid': None, 'finished_at': int(time.time()) }
        return True
    return False

def delete_model(name):
    stop_model(name)
    try:
        os.remove(os.path.join(MODELS_DIR, name))
    except Exception:
        pass
    cfg = load_cfg()
    if name in cfg.get('autostart', []):
        cfg['autostart'] = [x for x in cfg['autostart'] if x != name]
        save_cfg(cfg)
    return True

def add_autostart(name):
    cfg = load_cfg()
    if name not in cfg.get('autostart', []):
        cfg['autostart'].append(name)
        save_cfg(cfg)
        start_model(name)
        snapshot_status()
    return True

def remove_autostart(name):
    cfg = load_cfg()
    if name in cfg.get('autostart', []):
        cfg['autostart'] = [x for x in cfg['autostart'] if x != name]
        save_cfg(cfg)
        snapshot_status()
    return True

def read_commands():
    try:
        with open(CMD_PATH, 'r', encoding='utf-8') as f:
            arr = json.load(f)
    except Exception:
        arr = []
    try:
        with open(CMD_PATH, 'w', encoding='utf-8') as f:
            json.dump([], f)
    except Exception:
        pass
    return arr if isinstance(arr, list) else []

def snapshot_status():
    cfg = load_cfg()
    autostart = set(cfg.get('autostart', []))
    items = []
    for name in list_models():
        p = procs.get(name)
        running = p is not None and p.poll() is None
        st = 'running' if running else 'stopped'
        if not running:
            try:
                info = last_status.get(name) or {}
                fa = info.get('finished_at')
                if isinstance(fa, int) and (int(time.time()) - fa) <= RECENT_FINISH_SEC:
                    st = 'finished'
            except Exception:
                pass
        pid = (p.pid if running else None)
        title, desc = meta_for(name)
        items.append({ 'name': name, 'title': title, 'description': desc, 'status': st, 'pid': pid, 'autostart': (name in autostart) })
    tmp = STATUS_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False)
    os.replace(tmp, STATUS_PATH)
    try:
        port = int(os.environ.get('FLASK_PORT','5000'))
        url = f"http://127.0.0.1:{port}/api/models/notify"
        req = urllib.request.Request(url, data=json.dumps({'models': items}).encode('utf-8'), headers={'Content-Type':'application/json'})
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass

def ensure_autostart():
    cfg = load_cfg()
    for name in cfg.get('autostart', []):
        if os.path.isfile(os.path.join(MODELS_DIR, name)):
            start_model(name)

def main():
    if not os.path.isfile(CMD_PATH):
        with open(CMD_PATH, 'w', encoding='utf-8') as f:
            json.dump([], f)
    ensure_autostart()
    snapshot_status()
    while True:
        for cmd in read_commands():
            act = str(cmd.get('action') or '').lower()
            name = str(cmd.get('name') or '')
            if not name:
                continue
            if act == 'start':
                start_model(name)
            elif act == 'stop':
                stop_model(name)
            elif act == 'delete':
                delete_model(name)
            elif act == 'add_autostart':
                add_autostart(name)
            elif act == 'remove_autostart':
                remove_autostart(name)
            snapshot_status()
        snapshot_status()
        time.sleep(1)

if __name__ == '__main__':
    main()