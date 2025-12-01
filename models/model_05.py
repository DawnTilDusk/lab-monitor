import os, json, random, urllib.request, time
def get(u):
    return json.loads(urllib.request.urlopen(u, timeout=5).read().decode('utf-8'))
p=int(os.environ.get('FLASK_PORT','5000'))
b=f"http://127.0.0.1:{p}"
interval=int(os.environ.get('MODEL_INTERVAL_SEC','10'))
while True:
    latest=get(f"{b}/api/latest")
    hist=get(f"{b}/api/history?hours=3")
    cols=random.sample(['temperature','light','image'], random.choice([1,2,3]))
    res={'name':'model_05.py','selected':cols}
    warn=False
    if 'temperature' in cols:
        arr=hist.get('temperature_data',[])
        vals=[x.get('value') for x in arr if isinstance(x,dict)]
        ta=round(sum(vals)/len(vals),2) if vals else None
        res['temp_avg']=ta
        warn = warn or (ta is not None and ta>35)
    if 'light' in cols:
        lv=latest.get('light')
        res['light']=lv
        warn = warn or (lv in [0,'0'])
    if 'image' in cols:
        res['image']=bool(latest.get('image_path'))
    res['alert']='yes' if warn else 'no'
    print(json.dumps(res, ensure_ascii=False), flush=True)
    time.sleep(interval)