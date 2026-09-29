"""Loopback-only time playback and explicit native research job endpoints."""
from __future__ import annotations
import argparse,json,os,secrets,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
from pathlib import Path
import transport as t
from settings import VERSION,DEFAULT,MODEL_PINS,validate_request
TOKEN=secrets.token_urlsafe(32)

class Handler(BaseHTTPRequestHandler):
    server_version='BTC5MTimePlayground/1'
    def log_message(self,fmt,*args):print(json.dumps({'http':fmt%args}),flush=True)
    def allowed_host(self):return self.headers.get('Host') in ('127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port))
    def send(self,code,value,ctype='application/json; charset=utf-8'):
        data=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(code)
        for k,v in {'Content-Type':ctype,'Content-Length':str(len(data)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
            'X-Frame-Options':'DENY','Referrer-Policy':'no-referrer',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"}.items():self.send_header(k,v)
        self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if not self.allowed_host():return self.send(403,{'error':'Invalid Host'})
        u=urlsplit(self.path);q=parse_qs(u.query)
        try:
            if u.path=='/health':return self.send(200,{'service':VERSION,'ok':True,'pid':os.getpid(),'live_authority':False})
            if u.path=='/api/catalog':
                history=[]
                for p in sorted((t.STORE/'jobs').glob('*/RESULT.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:50]:
                    r=t.read(p);history.append({'job_id':p.parent.name,'kind':r['kind'],'request':r['request'],'native_new':r['native_new']})
                return self.send(200,{'token':TOKEN,'version':VERSION,'defaults':DEFAULT,'models':MODEL_PINS,
                    'markets':t.read(t.STORE/'catalog.json')['markets'],'history':history})
            if u.path=='/api/status':return self.send(200,t.status(q.get('job_id',[''])[0]))
            if u.path=='/api/playback':return self.send(200,t.playback(q.get('job_id',[''])[0],q.get('which',['baseline'])[0]))
            if u.path=='/api/worker':return self.send(200,t.probe())
            files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/app.css':('app.css','text/css; charset=utf-8')}
            if u.path in files:
                name,ctype=files[u.path];return self.send(200,(t.P/'static'/name).read_bytes(),ctype)
            return self.send(404,{'error':'Not found'})
        except Exception as e:return self.send(400,{'error':str(e)})
    def do_POST(self):
        allowed={'http://127.0.0.1:'+str(self.server.server_port),'http://localhost:'+str(self.server.server_port)}
        if not self.allowed_host() or self.headers.get('Origin') not in allowed or not secrets.compare_digest(self.headers.get('X-Playground-Token',''),TOKEN):
            return self.send(403,{'error':'Same-origin token required'})
        try:
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<=24000 or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('Bounded JSON request required')
            body=json.loads(self.rfile.read(n));path=urlsplit(self.path).path
            if path in ('/api/baseline','/api/run'):
                req=validate_request(body)
                if path=='/api/baseline':req.update(candidate=req['baseline'],apply_ms=0,note='')
                return self.send(200,t.submit('BASELINE' if path=='/api/baseline' else 'CANDIDATE',req))
            if path=='/api/export':
                req=validate_request(body)
                name=time.strftime('%Y%m%dT%H%M%S')+'_'+secrets.token_hex(4)+'.json'
                artifact={'version':VERSION,'request':req,'status':'HYPOTHESIS_ONLY','native_rerun_submitted':False,
                    'note':'Export does not schedule or run a replay. Playback speed is display-only.'}
                p=t.STORE/'notes'/name;t.save(p,artifact)
                return self.send(200,{'path':str(p.relative_to(t.ROOT)),'filename':name,'artifact':artifact})
            return self.send(404,{'error':'Not found'})
        except Exception as e:return self.send(400,{'error':str(e)})

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=int,default=4332);a=ap.parse_args()
    if not (t.P/'MANIFEST.json').is_file() or not (t.STORE/'catalog.json').is_file():raise RuntimeError('Run prepare.py first')
    http=ThreadingHTTPServer(('127.0.0.1',a.port),Handler);http.daemon_threads=True
    print(json.dumps({'service':VERSION,'url':'http://127.0.0.1:'+str(a.port),'pid':os.getpid()}),flush=True)
    try:http.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:pass
    finally:http.server_close()
if __name__=='__main__':main()
