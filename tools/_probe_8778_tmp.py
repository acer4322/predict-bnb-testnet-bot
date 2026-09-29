import json,urllib.request
for path in ('/health','/state'):
 try:
  x=json.loads(urllib.request.urlopen('http://127.0.0.1:8778'+path,timeout=4).read().decode())
  print(path,json.dumps(x,ensure_ascii=False)[:20000])
 except Exception as e: print(path,repr(e))
