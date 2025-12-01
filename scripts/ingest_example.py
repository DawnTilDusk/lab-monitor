import os, json, random, urllib.request, zlib, time
host=os.environ.get('FLASK_HOST','127.0.0.1')
port=int(os.environ.get('FLASK_PORT','5000'))
w=h=8
pixels=[]
buf=bytearray()
for y in range(h):
    row=[]
    for x in range(w):
        r=random.randint(0,255); g=random.randint(0,255); b=random.randint(0,255)
        row.append({'r':r,'g':g,'b':b})
        buf.extend([r,g,b])
    pixels.append(row)
checksum=f"{zlib.crc32(buf)&0xFFFFFFFF:08x}"
payload={
  'device_id':'sim-001',
  'timestamp_ms':int(time.time()*1000),
  'temperature_c': round(random.uniform(22.0,28.0),1),
  'light': random.randint(300,800),
  'frame': {'width':w,'height':h,'pixels':pixels},
  'checksum_frame': checksum
}
req=urllib.request.Request(f"http://{host}:{port}/api/ingest", data=json.dumps(payload).encode('utf-8'), headers={'Content-Type':'application/json'})
r=urllib.request.urlopen(req,timeout=10).read().decode('utf-8')
print(f"INGEST {r}")