from __future__ import annotations
import json,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
V83=ROOT/'data/research/lan_worker_returns/eth-v83-fresh-unseen4-dispatch-20260903-v1/result.json'
DB=ROOT/'data/target_wallet_official_v1.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V83_FRESH4_POSTMARKET_TEACHER_AUDIT_20260903.json'
d=json.loads(V83.read_text(encoding='utf-8')); con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
out=[]
for row in d['rows']:
 mid=int(row['marketId']); win=str(row['winnerPostHocOnly']).upper(); ours=row['candidateV83']
 rs=con.execute("select role,side,average_price,shares,first_event_ms,parent_id from target_parent_orders where market_id=? and asset='ETH' order by first_event_ms,parent_id",(mid,)).fetchall()
 u=dwn=cost=0.0; comps=0; repair_qty=expand_qty=0.0; maker=taker=0; repair_components=expand_components=0
 for r in rs:
  q=float(r['shares'] or 0);p=float(r['average_price'] or 0);side=str(r['side']);gap=abs(u-dwn);weak='UP' if u<dwn-1e-9 else 'DOWN' if dwn<u-1e-9 else None
  rq=min(q,gap) if side==weak else 0.0;eq=q-rq
  if rq>1e-9:repair_components+=1;repair_qty+=rq
  if eq>1e-9:expand_components+=1;expand_qty+=eq
  if rq>1e-9 and eq>1e-9:comps+=1
  maker+=str(r['role'])=='MAKER';taker+=str(r['role'])=='TAKER'
  if side=='UP':u+=q
  else:dwn+=q
  cost+=q*p
 tf=min(u,dwn)-cost;tb=max(u,dwn)-cost;tp=(u if win=='UP' else dwn)-cost
 out.append({'marketId':mid,'winner':win,'ours':{'fills':int(ours.get('actualFillEvents') or 0),'floor':float(ours.get('floor') or 0),'best':max(float(ours.get('truthInv',{}).get('UP',0) if isinstance(ours.get('truthInv'),dict) else 0),0) if False else None,'pnl':float(ours.get('pnlDiagnosticOnly') or 0),'economicDeficit':float(ours.get('v80EconomicDeficitAmountAtEnd') or 0),'admissionAllows':int(ours.get('v83AdmissionAllows') or 0),'admissionBlocks':int(ours.get('v83AdmissionBlocks') or 0),'v36FillQty':float(ours.get('v36ActiveFillQty') or 0),'activeExpandFillQty':float(ours.get('v64FillQty') or 0)},'target':{'parentFills':len(rs),'makerParents':maker,'takerParents':taker,'repairComponents':repair_components,'expandComponents':expand_components,'compositeParents':comps,'repairQty':repair_qty,'expandQty':expand_qty,'floor':tf,'best':tb,'pnl':tp},'gaps':{'floorTargetMinusOurs':tf-float(ours.get('floor') or 0),'pnlTargetMinusOurs':tp-float(ours.get('pnlDiagnosticOnly') or 0),'parentFillCountRatioTargetToOurs':len(rs)/max(1,int(ours.get('actualFillEvents') or 0))}})
con.close()
summary={'markets':len(out),'targetParentFills':sum(x['target']['parentFills'] for x in out),'oursFills':sum(x['ours']['fills'] for x in out),'targetCompositeParents':sum(x['target']['compositeParents'] for x in out),'targetRepairComponents':sum(x['target']['repairComponents'] for x in out),'targetExpandComponents':sum(x['target']['expandComponents'] for x in out),'targetFloorSum':sum(x['target']['floor'] for x in out),'oursFloorSum':sum(x['ours']['floor'] for x in out),'targetPnlSum':sum(x['target']['pnl'] for x in out),'oursPnlSum':sum(x['ours']['pnl'] for x in out)}
res={'version':'TARGET_ETH_V83_FRESH4_POSTMARKET_TEACHER_AUDIT','date':'2026-09-03','researchOnly':True,'sourceOur':str(V83.relative_to(ROOT)),'architectureBasis':['TARGET_CROSS_TIMEFRAME_SYSTEM_ARCHITECTURE_SYNTHESIS_V21_20260903.json','TARGET_SYSTEM_REGULARITIES_RESEARCH_HANDOFF_V1_20260903.md','TARGET_ETH_V70_COMPOSITE_PARENT_RELAY_AUDIT_20260903.json'],'summary':summary,'rows':out,'interpretationBoundary':['Target used only after market settlement','No Target future action/timestamp/side/qty is a runtime trigger','Counts are descriptive architecture-gap diagnostics, not action-count tuning']}
OUT.write_text(json.dumps(res,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'summary':summary,'rows':out},ensure_ascii=False))
