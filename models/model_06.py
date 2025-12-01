import os, json, random, urllib.request, time
def get(u):
    return json.loads(urllib.request.urlopen(u, timeout=5).read().decode('utf-8'))
p=int(os.environ.get('FLASK_PORT','5000'))
b=f"http://127.0.0.1:{p}"
interval=int(os.environ.get('MODEL_INTERVAL_SEC','10'))
while True:
    latest=get(f"{b}/api/latest")
    hist=get(f"{b}/api/history?hours=24")
    cols=random.sample(['temperature','light','image'], random.choice([1,2,3]))
    res={'name':'model_06.py','selected':cols}
    if 'temperature' in cols:
        arr=hist.get('temperature_data',[])
        vals=[x.get('value') for x in arr if isinstance(x,dict)]
        res['temp_slope']=round(((vals[-1]-vals[0])/(len(vals) or 1)),2) if len(vals)>=2 else None
    if 'light' in cols:
        res['light']=latest.get('light')
    if 'image' in cols:
        res['image']=bool(latest.get('image_path'))
    print(json.dumps(res, ensure_ascii=False), flush=True)
    time.sleep(interval)