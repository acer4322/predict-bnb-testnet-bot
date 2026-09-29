from __future__ import annotations
import argparse,json,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,'tools')
import run_btc5m_transfer_structural_worker_v1 as w
PKGS={
 'a70':'.lan_worker_v1/v49_oracle_good_structure_slowadd_a70_v1_20260915',
 'a60':'.lan_worker_v1/v49_oracle_good_structure_slowadd_a60_v1_20260915',
 'a00':'.lan_worker_v1/v49_oracle_good_structure_slowadd_a00_v1_20260915',
}
WIN={2021217:'UP',2021179:'DOWN',2021158:'UP',2018839:'UP'}
TERMINAL={'succeeded','failed','runner_error','cancelled'}

def stage_and_check():
 out={}
 for arm,pkg in PKGS.items():
  st=w.dispatch.cmd_stage(w.HOST,pkg)
  remote=st['remote_absolute']+'\\money_runner.py'
  argv=[w.dispatch.REMOTE_PY,remote,'--market-id','2021217','--mode','ORACLE_UP','--direction-rule','ORACLE_FIXED','--money-mode','PARALLEL_PAYOFF_ZERO','--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE','--mg-pressure','1','--mg-tail','2','--check-only']
  c=w.dispatch.parse_json_output(w.dispatch.ssh_raw(w.HOST,subprocess.list2cmdline(argv),timeout=30))
  assert c['status']=='PASS',c
  out[arm]=(st,remote,c)
 return out

def jid(mid,arm):return f'v49-good-structure-slowadd-{arm}-{mid}-20260915-v1'

def run(markets):
 stages=stage_and_check(); pending=[(m,a) for m in markets for a in ('a70','a60','a00')]; live={};done=[]
 while pending or live:
  while pending and len(live)<3:
   m,a=pending.pop(0);job=jid(m,a);state=w.dispatch.cmd_status(w.HOST,job)
   if state['state']=='missing':
    remote=stages[a][1];argv=[w.dispatch.REMOTE_PY,remote,'--market-id',str(m),'--mode','ORACLE_'+WIN[m],'--direction-rule','ORACLE_FIXED','--money-mode','PARALLEL_PAYOFF_ZERO','--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE','--mg-pressure','1','--mg-tail','2']
    print('SUBMIT',m,a,flush=True);w.dispatch.cmd_submit(w.HOST,argv,'.',job,4,6,180,auto_collect=False)
   live[job]=(m,a)
  for job,(m,a) in list(live.items()):
   st=w.dispatch.cmd_status(w.HOST,job)
   if st['state'] in TERMINAL:
    print('DONE',m,a,st['state'],st.get('elapsed_seconds'),flush=True)
    col=w.dispatch.cmd_collect(w.HOST,job)
    done.append(dict(market=m,arm=a,state=st,collect=col));del live[job]
  if live:time.sleep(3)
 return done

if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('markets',nargs='+',type=int);a=ap.parse_args();print(json.dumps({'status':'COMPLETE','rows':run(a.markets)},default=str))
