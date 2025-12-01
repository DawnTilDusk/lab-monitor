import os, json, urllib.request
host=os.environ.get('FLASK_HOST','127.0.0.1')
port=int(os.environ.get('FLASK_PORT','5000'))
url=f"http://{host}:{port}/api/capture"
req=urllib.request.Request(url, data=b"{}", headers={'Content-Type':'application/json'})
d=json.loads(urllib.request.urlopen(req,timeout=10).read().decode('utf-8'))
p=d.get('image_path','')
if p:
    img=urllib.request.urlopen(f"http://{host}:{port}{p}",timeout=10).read()
    out_dir=os.path.join(os.path.dirname(__file__),'out')
    os.makedirs(out_dir,exist_ok=True)
    out=os.path.join(out_dir,'cam_sample.jpg')
    open(out,'wb').write(img)
    print(f"CAM {p} -> {out}")
else:
    print("CAM no image")