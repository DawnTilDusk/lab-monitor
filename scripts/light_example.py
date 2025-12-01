import os, json, urllib.request
host=os.environ.get('FLASK_HOST','127.0.0.1')
port=int(os.environ.get('FLASK_PORT','5000'))
u=f"http://{host}:{port}/api/latest"
d=json.loads(urllib.request.urlopen(u,timeout=5).read().decode('utf-8'))
lv=d.get('light',None)
print(f"LIGHT {lv if lv is not None else 'N/A'}")