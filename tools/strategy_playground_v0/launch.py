"""Start/status/stop only this loopback Playground; no other service is touched."""
import argparse,json,os,signal,socket,subprocess,sys,time,webbrowser
from pathlib import Path
from urllib.request import urlopen
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
STORE=ROOT/'data/research/strategy_playground_v0_20260921'
URL='http://127.0.0.1:4331'
IDENTITY='BTC5M_STRATEGY_PLAYGROUND_V0'

def health():
    try:
        with urlopen(URL+'/health',timeout=2) as r:return json.load(r)
    except Exception:return None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['start','status','stop'],nargs='?',default='start');ap.add_argument('--no-browser',action='store_true');a=ap.parse_args()
    h=health();state=STORE/'SERVER_PROCESS.json'
    if a.action=='status':print(json.dumps(h or {'status':'NOT_RUNNING'}));return
    if a.action=='stop':
        if not h:print(json.dumps({'status':'NOT_RUNNING'}));return
        saved=json.loads(state.read_text(encoding='utf-8')) if state.exists() else {}
        if h.get('service')!=IDENTITY or h.get('pid')!=saved.get('pid'):raise RuntimeError('Service identity mismatch; will not terminate any process')
        os.kill(h['pid'],signal.SIGTERM);print(json.dumps({'status':'STOP_REQUESTED','pid':h['pid']}));return
    if h:
        if h.get('service')!=IDENTITY:raise RuntimeError('Port 4331 is occupied by another service; not touched')
        print(json.dumps({'status':'ALREADY_RUNNING','url':URL,'pid':h['pid']}))
        if not a.no_browser:webbrowser.open(URL)
        return
    with socket.socket() as s:
        if s.connect_ex(('127.0.0.1',4331))==0:raise RuntimeError('Port 4331 occupied; no process changed')
    if not (HERE/'VENDOR_PINS.json').exists():raise RuntimeError('Run prepare.py first')
    STORE.mkdir(parents=True,exist_ok=True)
    flags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)|getattr(subprocess,'DETACHED_PROCESS',0)
    with (STORE/'server.stdout.log').open('a',encoding='utf-8') as so,(STORE/'server.stderr.log').open('a',encoding='utf-8') as se:
        p=subprocess.Popen([sys.executable,'-X','utf8',str(HERE/'server.py')],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=so,stderr=se,creationflags=flags)
    state.write_text(json.dumps({'pid':p.pid,'service':IDENTITY,'url':URL,'python':sys.executable,'script':str(HERE/'server.py')},indent=2),encoding='utf-8')
    for _ in range(40):
        h=health()
        if h and h.get('service')==IDENTITY and h.get('pid')==p.pid:
            print(json.dumps({'status':'RUNNING','url':URL,'pid':p.pid,'live_authority':False}))
            if not a.no_browser:webbrowser.open(URL)
            return
        if p.poll() is not None:raise RuntimeError('Playground exited; see its stderr log')
        time.sleep(.2)
    raise RuntimeError('Readiness not confirmed; inspect this PID, do not duplicate launch')
if __name__=='__main__':main()
