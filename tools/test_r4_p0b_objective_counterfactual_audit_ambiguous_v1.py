from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_objective_counterfactual_audit_v2 as audit
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,default=0);ap.add_argument('--count',type=int,default=5);a=ap.parse_args()
 rows=[r for r in audit.load_role_rows(True) if r.get('role')=='AMBIGUOUS_TRADEOFF'];sel=rows[a.start:a.start+a.count];out=[];errs=[]
 for i,r in enumerate(sel,1):
  try:
   z=audit.audit_one(r);out.append(z);print(json.dumps({'progress':i,'marketId':z['marketId'],'candidateKey':z['candidateKey'],'mechanicalSuppression':z['creditAudit'].get('mechanicallySuppressedFutureQty'),'routingChanged':z['causalDivergence']['additiveVsReservationRouting']['routingChanged']},ensure_ascii=False),flush=True)
  except Exception as e:
   errs.append({'marketId':r.get('marketId'),'candidateKey':r.get('candidateKey'),'error':f'{type(e).__name__}:{e}'});print(json.dumps(errs[-1],ensure_ascii=False),flush=True)
 p=P/f'r4_p0b_objective_counterfactual_audit_ambiguous_chunk_{a.start}_{len(sel)}_v1.json';p.write_text(json.dumps({'rows':out,'errors':errs,'totalAmbiguous':len(rows)},ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(p.relative_to(ROOT)),'rows':len(out),'errors':errs,'totalAmbiguous':len(rows)},ensure_ascii=False))
if __name__=='__main__':main()
