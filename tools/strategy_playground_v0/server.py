"""Loopback-only research UI. No exchange or worker dispatch endpoints."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import secrets
import sys
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import core

MAX_BODY=32768
TOKEN=secrets.token_urlsafe(32)
ROOTS=[]
PROVENANCE={}

class Handler(BaseHTTPRequestHandler):
    server_version='BTC5MPlayground/0'
    def log_message(self, fmt, *args):
        # Request content is neither executed nor interpolated into a shell.
        print(json.dumps({'http':fmt % args},ensure_ascii=True),flush=True)
    def safe_host(self):
        return self.headers.get('Host') in ('127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port))
    def send(self,code,value,ctype='application/json; charset=utf-8'):
        data=json.dumps(value,ensure_ascii=False,allow_nan=False).encode() if not isinstance(value,bytes) else value
        self.send_response(code)
        self.send_header('Content-Type',ctype)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if not self.safe_host():return self.send(403,{'error':'Invalid Host'})
        path=urlsplit(self.path).path
        if path=='/health':return self.send(200,{'ok':True,'service':core.VERSION,'pid':os.getpid(),'live_authority':False,'native_hft_dispatch':False})
        if path=='/api/catalog':
            roots=[]
            for r in ROOTS:
                v=core.view(core.prepare(r,core.DEFAULT))
                roots.append({'id':r['id'],'market':r['market'],'tag':r['tag'],'source_model':r['source_model'],
                              'index':r['index'],'remaining_ms':r.get('remaining_ms'),'t_ms':r.get('t_ms'),
                              'payoff':v['payoff'],'owners':v['owner_count']})
            return self.send(200,{'version':core.VERSION,'token':TOKEN,'roots':roots,'defaults':core.DEFAULT,
                                  'scenarios':core.SCENARIOS,'variables':sorted(core.NAMES),'provenance':PROVENANCE})
        files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/app.css':('app.css','text/css; charset=utf-8')}
        if path in files:
            name,ctype=files[path];p=core.HERE/'static'/name
            if p.is_file():return self.send(200,p.read_bytes(),ctype)
        return self.send(404,{'error':'Not found'})
    def do_POST(self):
        if not self.safe_host():return self.send(403,{'error':'Invalid Host'})
        allowed={'http://127.0.0.1:'+str(self.server.server_port),'http://localhost:'+str(self.server.server_port)}
        if self.headers.get('Origin') not in allowed or not secrets.compare_digest(self.headers.get('X-Playground-Token',''),TOKEN):
            return self.send(403,{'error':'Same-origin token required'})
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send(415,{'error':'JSON only'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=MAX_BODY:raise ValueError('Request too large or empty')
            body=json.loads(self.rfile.read(length))
            root,c,scenario,history=core.resolve_request(ROOTS,body)
            path=urlsplit(self.path).path
            if path=='/api/state':
                n,latched,rows=core.replay_prefix(root,c,scenario,history)
                return self.send(200,{'state':core.view(n),'actions':core.proposals_view(n,c),'history':rows,
                    'stop_latched':latched,'event':len(history),'training_eligible':False})
            if path=='/api/compare':
                result=core.experiment(root,c,scenario,history,body.get('horizon',8))
                return self.send(200,{**result,'provenance':PROVENANCE})
            if path=='/api/export':
                note=body.get('note','');kind=body.get('kind','HYPOTHESIS')
                if not isinstance(note,str) or len(note)>4000:raise ValueError('想法最多 4000 字元。')
                if kind not in ('HYPOTHESIS','NATIVE_VALIDATION_REQUEST'):raise ValueError('Invalid export kind')
                result=core.experiment(root,c,scenario,history,body.get('horizon',8))
                when=datetime.now(timezone.utc).isoformat()
                artifact={'schema':core.VERSION,'created_utc':when,'kind':kind,'status':'SAVED_NOT_SUBMITTED',
                    'note':note,'request':{'root_id':root['id'],'config':c,'scenario':scenario,'history':history,'horizon':body.get('horizon',8)},
                    'provenance':PROVENANCE,'result':result,
                    'evaluation_contract':{'destination':'VERIFIED_SECOND_PC','max_threads':4,'smoke_markets':1,
                        'promotion':False,'runtime_eligible':False,'target_and_winner':'POSTHOC_ONLY',
                        'native_adapter_status':'NOT_CONNECTED_IN_V0','requires_frozen_candidate':True,
                        'requires_matched_R65_baseline':True,'requires_actual_receipts_and_costs':True,
                        'do_not_submit_existing_R77_R78_jobs':True,'human_data_training_eligible':False}}
                out=core.STORE/'experiments';out.mkdir(parents=True,exist_ok=True)
                name=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:10]+'.json'
                text=json.dumps(artifact,ensure_ascii=False,indent=2,allow_nan=False)
                with (out/name).open('x',encoding='utf-8',newline='\n') as f:f.write(text)
                return self.send(200,{'ok':True,'path':str((out/name).relative_to(core.ROOT)),
                    'sha256':hashlib.sha256(text.encode()).hexdigest(),'filename':name,'artifact':artifact})
            return self.send(404,{'error':'Not found'})
        except (ValueError,TypeError,KeyError,AssertionError,RecursionError) as e:
            return self.send(400,{'error':str(e) or type(e).__name__})
        except Exception as e:
            print(json.dumps({'error_type':type(e).__name__,'error':str(e)}),flush=True)
            return self.send(500,{'error':'研究服務錯誤；請查看本服務日誌。沒有交易或派送動作。'})


def main():
    global ROOTS,PROVENANCE
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=int,default=4331);ap.add_argument('--check',action='store_true');args=ap.parse_args()
    ROOTS,PROVENANCE=core.load_roots()
    if args.check:
        print(json.dumps({'ok':True,'roots':len(ROOTS),'provenance':PROVENANCE},ensure_ascii=False));return
    if not 1024<=args.port<=65535:raise ValueError('Port out of range')
    http=HTTPServer(('127.0.0.1',args.port),Handler)
    http.timeout=10
    print(json.dumps({'service':core.VERSION,'url':'http://127.0.0.1:'+str(args.port),'pid':os.getpid(),'roots':len(ROOTS)},ensure_ascii=False),flush=True)
    try:http.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:pass
    finally:http.server_close()

if __name__=='__main__':main()
