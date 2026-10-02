from __future__ import annotations
import json,math,os
from pathlib import Path
EPS=1e-9

def classify(projected,room,ceiling,repair_bid):
 if projected>=-EPS:return {'recoverable':True,'reason':'EXISTING_REPAIR_RESERVATION_COVERS_PROJECTED_FLOOR','admissible':None,'need':0.0,'legal':0.0,'required':0.0}
 if ceiling is None or repair_bid is None:
  return {'recoverable':False,'reason':'NO_ADMISSIBLE_FUTURE_REPAIR_PRICE','admissible':None,'need':None,'legal':None,'required':None}
 admissible=min(float(repair_bid),float(ceiling))
 if not(EPS<admissible<1-EPS):return {'recoverable':False,'reason':'NO_ADMISSIBLE_FUTURE_REPAIR_PRICE','admissible':admissible,'need':None,'legal':None,'required':None}
 need=max(0.0,-float(projected))/(1.0-admissible);legal=1.0/admissible;req=max(need,legal);ok=req<=float(room)+EPS
 return {'recoverable':ok,'reason':'PASS' if ok else 'FUTURE_REPAIR_QTY_EXCEEDS_ROOM','admissible':admissible,'need':need,'legal':legal,'required':req}

def main():
 cases=[
  ('owned_covers',0.05,0.0,0.60,0.55,True),
  ('required_lt_room',-0.50,3.0,0.60,0.50,True),
  ('required_eq_room',-0.50,2.0,0.50,0.50,True),
  ('required_gt_room',-1.00,1.5,0.50,0.50,False),
  ('no_admissible_price',-0.20,5.0,0.0,0.45,False),
  ('venue_min_exceeds_room',-0.10,1.5,0.80,0.50,False),
  ('ceiling_tighter_than_bid',-0.30,2.5,0.40,0.70,True),
  ('zero_gap_negative_floor',-0.10,0.0,None,0.50,False),
 ]
 rows=[]
 for name,projected,room,ceiling,bid,expected in cases:
  r=classify(projected,room,ceiling,bid);rows.append({'name':name,'projectedFloorAfterOwnedRepair':projected,'repairRoom':room,'economicRepairCeiling':ceiling,'repairBid':bid,'expectedRecoverable':expected,**r,'pass':bool(r['recoverable'])==expected})
 gates={'allEightExpected':len(rows)==8 and all(x['pass'] for x in rows),'hasPositive':any(x['recoverable'] for x in rows),'hasNegative':any(not x['recoverable'] for x in rows),'ceilingActuallyBinds':next(x for x in rows if x['name']=='ceiling_tighter_than_bid')['admissible']==0.40}
 decision='KEEP_V73_RECOVERABILITY_KERNEL_NOT_TAUTOLOGICAL' if all(gates.values()) else 'REJECT_V73_RECOVERABILITY_KERNEL_FORMULA'
 out={'version':'ETH_REPAIR_V73D_RECOVERABILITY_KERNEL_NEGATIVE_CONTROL_MICROWORLD','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'rows':rows,'gates':gates,'decision':decision,'boundary':['functional micro-world only','same V73 closed-form geometry','no winner/PnL','no threshold sweep','no H100','no 8781']}
 outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));outdir.mkdir(parents=True,exist_ok=True);(outdir/'result.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'rows':rows},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
