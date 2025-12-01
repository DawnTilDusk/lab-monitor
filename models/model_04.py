import os, json, random, urllib.request, time
def get(u):
    return json.loads(urllib.request.urlopen(u, timeout=5).read().decode('utf-8'))
p=int(os.environ.get('FLASK_PORT','5000'))
b=f"http://127.0.0.1:{p}"
interval=int(os.environ.get('MODEL_INTERVAL_SEC','10'))
while True:
    latest=get(f"{b}/api/latest")
    hist=get(f"{b}/api/history?hours=12")
    cols=random.sample(['temperature','light','image'], random.choice([1,2,3]))
    res={'name':'model_04.py','selected':cols}
    if 'temperature' in cols:
        arr=hist.get('temperature_data',[])
        vals=[x.get('value') for x in arr if isinstance(x,dict)]
        res['temp_min']=min(vals) if vals else None
    if 'light' in cols:
        res['light_flag']=1 if (latest.get('light') not in [0,'0',None]) else 0
    if 'image' in cols:
        res['image_present']=1 if latest.get('image_path') else 0
    print(json.dumps(res, ensure_ascii=False), flush=True)
    time.sleep(interval)