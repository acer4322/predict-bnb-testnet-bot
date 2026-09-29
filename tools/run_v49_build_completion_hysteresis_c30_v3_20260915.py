from __future__ import annotations
import json,time,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_btc5m_transfer_structural_worker_v1 as w
ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research';CAP=R/'market_capsule_v1/source_bundle_v49_generalization_c30_20260914_v1'
ROWS=[json.loads(x) for x in (CAP/'market_results.jsonl').read_text().splitlines() if x.strip()]
IDS=[int(r['market_id']) for r in ROWS];WM={int(r['market_id']):r['winner'] for r in ROWS}
PKG='.lan_worker_v1/v49_oracle_build_completion_hysteresis_v3_20260915';PROGRESS=R/'BTC5M_BUILD_COMPLETION_HYSTERESIS_C30_V3_20260915_PROGRESS.json'
TERMINAL={'succeeded','failed','runner_error','cancelled'};MAX_PARALLEL=3

def jid(mid):return f'v49-build-complete-c30-{mid}-20260915-v3'

def main():
    stage=w.dispatch.cmd_stage(w.HOST,PKG);remote=stage['remote_absolute']+'\\money_runner.py';collected=set();done=[]
    while len(collected)<len(IDS):
        statuses={mid:w.dispatch.cmd_status(w.HOST,jid(mid)) for mid in IDS}
        # collect any terminal jobs exactly once
        for mid,st in statuses.items():
            if mid in collected:continue
            if st['state'] in TERMINAL:
                col=w.dispatch.cmd_collect(w.HOST,jid(mid));done.append({'market_id':mid,'winner':WM[mid],'job_id':jid(mid),'state':st,'collect':col});collected.add(mid)
                print('DONE',mid,WM[mid],st['state'],st.get('elapsed_seconds'),flush=True)
        # count live c30 jobs only
        live=[mid for mid,st in statuses.items() if st['state'] not in TERMINAL and st['state']!='missing']
        # fill available slots
        for mid in IDS:
            if len(live)>=MAX_PARALLEL:break
            if mid in collected:continue
            st=statuses[mid]
            if st['state']!='missing':continue
            argv=[w.dispatch.REMOTE_PY,remote,'--market-id',str(mid),'--mode','ORACLE_'+WM[mid],'--direction-rule','ORACLE_FIXED','--money-mode','PARALLEL_PAYOFF_ZERO','--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE','--mg-pressure','1','--mg-tail','2']
            try:
                out=w.dispatch.cmd_submit(w.HOST,argv,'.',jid(mid),4,6,90,auto_collect=False);print('SUBMIT',mid,WM[mid],out.get('probe',{}).get('cpu_pct'),out.get('probe',{}).get('memory',{}).get('used_pct'),flush=True)
            except RuntimeError as ex:
                if 'job already exists' not in str(ex):raise
                print('RACE_REUSE',mid,flush=True)
            live.append(mid)
        PROGRESS.write_text(json.dumps({'collected':sorted(collected),'live':live,'remaining':[m for m in IDS if m not in collected]},indent=2,ensure_ascii=False)+'\n')
        if len(collected)<len(IDS):time.sleep(2)
    failed=[x for x in done if x['state']['state']!='succeeded'];print(json.dumps({'status':'COMPLETE_BATCH' if not failed else 'COMPLETE_WITH_FAILURES','count':len(done),'failed':[(x['market_id'],x['state']['state']) for x in failed]},ensure_ascii=False),flush=True)
    if failed:raise SystemExit(2)
if __name__=='__main__':main()
