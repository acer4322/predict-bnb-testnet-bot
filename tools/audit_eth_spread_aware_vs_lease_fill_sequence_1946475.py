from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path: sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_reanchor_handoff_lease_1946475 as lease
import tools.run_eth_spread_aware_rolling_handoff_1946475 as spread
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
MID=1946475; pe=base.pe

def fill_rows(sim):
    out=[]
    sim._refresh_carrier_ledger(int(getattr(sim,'capEnd',0)))
    for k,e in getattr(sim,'carrierLedger',{}).items():
        q=float(e.get('actualFilled') or 0.0)
        if q<=1e-9: continue
        o=getattr(sim,'orders',{}).get(str(k),{})
        out.append({
            'key':str(k),'side':str(e.get('side') or o.get('side') or '').upper(),
            'price':float(o.get('price') or e.get('price') or 0.0),'qty':q,
            'role':str(e.get('objectiveRole') or o.get('objective_role') or ''),
            'lane':str(e.get('lane') or ''),'parentId':e.get('parentId'),
            'objectiveId':e.get('objectiveId'),'submittedAt':e.get('submittedAt') or o.get('submittedAt'),
            'terminalConfirmed':bool(e.get('terminalConfirmed')),
        })
    out.sort(key=lambda z:(int(z.get('submittedAt') or 0),z['key']))
    return out

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']: ap.add_argument('--'+n,required=True)
    ap.add_argument('--output',required=True); a=ap.parse_args(); tmp=Path(tempfile.mkdtemp(prefix='fillseq_1946475_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a); t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']; tape=tmp/'tapes'/f'{MID}.json.xz'
        rows={}; metrics={}
        for name,cls,runner in [('LEASE',lease.ReanchorHandoffLeaseHFT,'run_lease'),('SPREAD',spread.SpreadAwareRollingHandoffHFT,'run_spread')]:
            s=pe.make(cls,tape,models,life,cap,tim,econ,price,sur,t44,t47)
            try:
                r=getattr(s,runner)(models,cr['winner']); rows[name]=fill_rows(s); metrics[name]=base.slim(r)
            finally:s.close()
        out={'version':'ETH_SPREAD_AWARE_VS_LEASE_FILL_SEQUENCE_1946475_V1','date':'2026-09-05','marketId':MID,'winnerPostHocOnly':cr['winner'],'metrics':metrics,'fills':rows,'deltaPnl':metrics['SPREAD']['pnlDiagnosticOnly']-metrics['LEASE']['pnlDiagnosticOnly'],'boundary':['behavior inert audit','same frozen market/runtime','winner posthoc only','no Target runtime input','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False))
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
