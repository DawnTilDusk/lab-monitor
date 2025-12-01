import os, json, urllib.request
host=os.environ.get('FLASK_HOST','127.0.0.1')
port=int(os.environ.get('FLASK_PORT','5000'))
u=f"http://{host}:{port}/api/history?hours=6"
d=json.loads(urllib.request.urlopen(u,timeout=5).read().decode('utf-8'))
vals=[x.get('value') for x in d.get('temperature_data',[]) if isinstance(x,dict) and 'value' in x]
if vals:
    avg=sum(vals)/len(vals)
    print(f"TEMP n={len(vals)} avg={avg:.2f}")
else:
    print("TEMP no data")