import os, json, urllib.request, time
def get(u):
    return json.loads(urllib.request.urlopen(u, timeout=5).read().decode('utf-8'))
p=int(os.environ.get('FLASK_PORT','5000'))
b=f"http://127.0.0.1:{p}"
interval=int(os.environ.get('MODEL_INTERVAL_SEC','10'))
while True:
    latest=get(f"{b}/api/latest")
    hist=get(f"{b}/api/history?hours=24")
    res={'name':'model_20.py'}
    arr=hist.get('temperature_data',[])
    vals=[x.get('value') for x in arr if isinstance(x,dict)]
    if len(vals)>=3:
        m=sum(vals)/len(vals)
        var=sum([(v-m)**2 for v in vals])/len(vals)
        std=(var**0.5) if var>0 else 0
        if std>0:
            skew=sum([(v-m)**3 for v in vals])/(len(vals)*(std**3))
            res['temp_skew']=round(skew,3)
        else:
            res['temp_skew']=None
    else:
        res['temp_skew']=None
    res['light_latest']=latest.get('light')
    res['has_image']=bool(latest.get('image_path'))
    print(json.dumps(res, ensure_ascii=False), flush=True)
    time.sleep(interval)

