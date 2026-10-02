from __future__ import annotations
import json, urllib.request
out={}
for port in (8784,8785):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=3) as r:
            out[str(port)]=json.loads(r.read().decode('utf-8'))
    except Exception as e:
        out[str(port)]={'ok':False,'error':repr(e)}
print(json.dumps(out,ensure_ascii=False,indent=2))
