"""Start/status/stop only the V1 loopback UI, preserving V0 and live services."""
import argparse,json,os,signal,socket,subprocess,sys,time,webbrowser
from pathlib import Path
from urllib.request import urlopen
from settings import VERSION
P=Path(__file__).resolve().parent;ROOT=P.parents[1]
STORE=ROOT/'data/research/strategy_playground_v1_20260921';URL='http://127.0.0.1:4332'
def health():
    try:
        with urlopen(URL+'/health',timeout=2) as r:return json.load(r)
    except Exception:return None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=('start','status','stop'),default='start',nargs='?');ap.add_argument('--no-browser',action='store_true');a=ap.parse_args()
    h=health();state=STORE/'SERVER_PROCESS.json'
    if a.action=='status':print(json.dumps(h or {'state':'NOT_RUNNING'}));return
    if a.action=='stop':
        if not h:print(json.dumps({'state':'NOT_RUNNING'}));return
        old=json.loads(state.read_text(encoding='utf-8')) if state.exists() else {}
        if h.get('service')!=VERSION or h.get('pid')!=old.get('pid'):raise RuntimeError('UI identity mismatch; not terminating anything')
        os.kill(h['pid'],signal.SIGTERM);print(json.dumps({'state':'STOP_REQUESTED','pid':h['pid']}));return
    if h:
        if h.get('service')!=VERSION:raise RuntimeError('4332 occupied by another service')
        print(json.dumps({'state':'ALREADY_RUNNING','url':URL,'pid':h['pid']}))
        if not a.no_browser:webbrowser.open(URL)
        return
    with socket.socket() as s:
        if s.connect_ex(('127.0.0.1',4332))==0:raise RuntimeError('4332 occupied; other service is preserved')
    STORE.mkdir(parents=True,exist_ok=True)
    flags=getattr(subprocess,'DETACHED_PROCESS',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
    with (STORE/'server.stdout.log').open('ab') as so,(STORE/'server.stderr.log').open('ab') as se:
        p=subprocess.Popen([sys.executable,'-X','utf8',str(P/'server.py')],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=so,stderr=se,creationflags=flags)
    state.write_text(json.dumps({'pid':p.pid,'url':URL,'service':VERSION,'script':str(P/'server.py')},indent=2),encoding='utf-8')
    for _ in range(40):
        h=health()
        if h and h.get('service')==VERSION and h.get('pid')==p.pid:
            print(json.dumps({'state':'RUNNING','url':URL,'pid':p.pid,'live_changes':0}))
            if not a.no_browser:webbrowser.open(URL)
            return
        if p.poll() is not None:raise RuntimeError('V1 UI exited; inspect its stderr')
        time.sleep(.2)
    raise RuntimeError('V1 readiness unknown; check existing PID before another start')
if __name__=='__main__':main()
