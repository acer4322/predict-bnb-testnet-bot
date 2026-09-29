from __future__ import annotations
import argparse,json,os,sys,tempfile,zipfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STAGED=Path.cwd()/'.lan_worker_v1'/'staging'
if (STAGED/'run_lane_g_multi_action_exact_fork_v1b.py').exists():
    sys.path.insert(0,str(STAGED));import run_lane_g_multi_action_exact_fork_v1b as ma
else:
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from tools import run_lane_g_multi_action_exact_fork_v1b as ma

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--spec',required=True);ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    spec=json.loads(Path(a.spec).read_text(encoding='utf-8'))['markets'][str(a.market_id)];ma.FROZEN[a.market_id]=spec
    td=Path(tempfile.mkdtemp(prefix='diag_r303_corr_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            cohort={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']};tape=td/f'{a.market_id}.json.xz';tape.write_bytes(z.read(f'tapes/{a.market_id}.json.xz'))
        s=ma.MultiActionExactForkSim(tape,a.market_id,'R303_CONTINGENT_COMPOSITE',1,4)
        try:
            r=s.run_r264(cohort[a.market_id]['winner']);s._audit_r247();parent=s.r303Ledger.describe_parent(int(s.r303Parent['pid'])) if s.r303Parent else None
            cons=True if parent is None else abs(float(parent['repairPaid'])+float(parent['remainingDebt'])-float(parent['initialDebt']))<=1e-8
            diag={'marketId':a.market_id,'r264CorrectnessPass':bool(r.get('r264CorrectnessPass')),'r263CorrectnessPass':r.get('r263CorrectnessPass'),'r255CorrectnessPass':r.get('r255CorrectnessPass'),'unauthorizedOverflowQty':float(r.get('unauthorizedOverflowQty',0.0)),'repairQuotaExcessMax':float(r.get('repairQuotaExcessMax',0.0)),'r303SplitMismatch':s.r303SplitMismatch,'r303AuthorityOverrun':s.r303AuthorityOverrun,'r303OverflowRisk':s.r303OverflowRisk,'parentConservation':cons,'parentState':parent,'r303Authority':s.r303Authority,'triggerParityErrors':s.triggerParityErrors,'forkTriggered':s.forkTriggered,'forkResolved':s.forkResolved,'r247Audit':r.get('r247Audit'),'r255Audit':r.get('r255Audit'),'r263Audit':r.get('r263Audit'),'r264Audit':r.get('r264Audit'),'keys':sorted(k for k in r.keys() if 'correct' in k.lower() or 'overflow' in k.lower() or 'quota' in k.lower() or 'audit' in k.lower()),'terminal':{'pnl':r.get('pnlDiagnosticOnly'),'best':r.get('best'),'floor':r.get('floor')}}
        finally:s.close()
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(diag,indent=2),encoding='utf-8');print(json.dumps(diag,ensure_ascii=False,indent=2))
    finally:shutil.rmtree(td,ignore_errors=True)
if __name__=='__main__':main()
