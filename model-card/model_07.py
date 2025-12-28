import os, json, urllib.request, time
def get(u):
    return json.loads(urllib.request.urlopen(u, timeout=5).read().decode('utf-8'))
p=int(os.environ.get('FLASK_PORT','5000'))
b=f"http://127.0.0.1:{p}"
interval=int(os.environ.get('MODEL_INTERVAL_SEC','10'))
while True:
    latest=get(f"{b}/api/latest")
    hist=get(f"{b}/api/history?hours=24")
    res={'name':'model_07.py'}
    arr=hist.get('temperature_data',[])
    vals=[x.get('value') for x in arr if isinstance(x,dict)]
    if len(vals)>=2:
        k=min(10,len(vals))
        seg=vals[-k:]
        m=sum(seg)/k
        var=sum([(v-m)**2 for v in seg])/k
        res['temp_std_last10']=round(var**0.5,2)
    else:
        res['temp_std_last10']=None
    res['light_latest']=latest.get('light')
    res['has_image']=bool(latest.get('image_path'))
    print(json.dumps(res, ensure_ascii=False), flush=True)
    time.sleep(interval)

