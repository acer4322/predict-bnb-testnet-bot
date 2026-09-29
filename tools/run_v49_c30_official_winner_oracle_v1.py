from __future__ import annotations
import argparse,json,time,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_btc5m_transfer_structural_worker_v1 as w
ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research'
P=R/'BTC5M_V49_C30_OFFICIAL_WINNER_ORACLE_V1_20260914_PROTOCOL.json'
REMOTE=r'C:\BTC5M-worker\.lan_worker_v1\staging\v49_generalization_c30_20260914_v2\money_runner.py'
PROGRESS=R/'BTC5M_V49_C30_OFFICIAL_WINNER_ORACLE_V1_20260914_PROGRESS.json'
TERMINAL={'succeeded','failed','runner_error','cancelled'}

def load(): return json.loads(P.read_text(encoding='utf-8'))
def jid(mid): return f'v49-c30-winner-oracle-{mid}-20260914-v1'
def save(rows): PROGRESS.write_text(json.dumps({'rows':rows},indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

def run_one(mid,winner):
    job=jid(mid); state=w.dispatch.cmd_status(w.HOST,job)
    if state['state']=='missing':
        gs=w.old.global_state()
        assert not gs['other_processes'] and not any(x['pid_exists'] for x in gs['nonterminal']),gs
        mode='ORACLE_'+winner
        argv=[w.dispatch.REMOTE_PY,REMOTE,'--market-id',str(mid),'--mode',mode,'--direction-rule','LEGACY','--money-mode','PARALLEL_PAYOFF_ZERO','--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE']
        w.dispatch.cmd_submit(w.HOST,argv,'.',job,4,6,90,auto_collect=False)
    while True:
        state=w.dispatch.cmd_status(w.HOST,job)
        if state['state'] in TERMINAL: break
        time.sleep(2)
    if state['state']!='succeeded': raise RuntimeError(f'{job}:{state}')
    col=w.dispatch.cmd_collect(w.HOST,job)
    return {'market':mid,'winner':winner,'job_id':job,'state':state,'collect':col}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,default=0);ap.add_argument('--end',type=int,default=30);a=ap.parse_args()
    d=load(); ids=d['cohort']; wm=d['winner_map']; rows=[]
    for i in range(a.start,min(a.end,len(ids))):
        mid=ids[i]; print(f'[{i}] {mid} {wm[str(mid)]}',flush=True)
        row=run_one(mid,wm[str(mid)]);rows.append(row);save(rows)
        print(f"  {row['state']['state']} {row['state'].get('elapsed_seconds')}s",flush=True)
    print(json.dumps({'status':'COMPLETE_BATCH','start':a.start,'end':a.end,'count':len(rows)}))
if __name__=='__main__': main()
