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
    ma.FROZEN[a.market_id]=json.loads(Path(a.spec).read_text(encoding='utf-8'))['markets'][str(a.market_id)]
    td=Path(tempfile.mkdtemp(prefix='diag_r303_corr2_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            cohort={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']};tape=td/f'{a.market_id}.json.xz';tape.write_bytes(z.read(f'tapes/{a.market_id}.json.xz'))
        s=ma.MultiActionExactForkSim(tape,a.market_id,'R303_CONTINGENT_COMPOSITE',1,4)
        try:
            r=s.run_r264(cohort[a.market_id]['winner']);s._audit_r247();parent=s.r303Ledger.describe_parent(int(s.r303Parent['pid'])) if s.r303Parent else None
            cons=True if parent is None else abs(float(parent['repairPaid'])+float(parent['remainingDebt'])-float(parent['initialDebt']))<=1e-8
            diag={'marketId':a.market_id,'r247ServiceCorrectnessPass':r.get('r247ServiceCorrectnessPass'),'r247ServiceChecks':r.get('r247ServiceChecks'),'riskTrancheOverrunMax':r.get('riskTrancheOverrunMax'),'riskDebtOutstanding':r.get('riskDebtOutstanding'),'riskDebtPeak':r.get('riskDebtPeak'),'r255CorrectnessPass':r.get('r255CorrectnessPass'),'r263CorrectnessPass':r.get('r263CorrectnessPass'),'r264CorrectnessPass':r.get('r264CorrectnessPass'),'unauthorizedOverflowQty':r.get('unauthorizedOverflowQty'),'repairQuotaExcessMax':r.get('repairQuotaExcessMax'),'r303SplitMismatch':s.r303SplitMismatch,'r303AuthorityOverrun':s.r303AuthorityOverrun,'r303OverflowRisk':s.r303OverflowRisk,'parentConservation':cons,'parentState':parent,'r303Authority':s.r303Authority,'triggerParityErrors':s.triggerParityErrors}
        finally:s.close()
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(diag,indent=2),encoding='utf-8');print(json.dumps(diag,ensure_ascii=False,indent=2))
    finally:shutil.rmtree(td,ignore_errors=True)
if __name__=='__main__':main()
