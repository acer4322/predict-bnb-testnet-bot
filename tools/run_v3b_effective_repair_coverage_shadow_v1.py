from __future__ import annotations
import argparse,importlib.util,json,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd().parent/'run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate.py'
if _STAGED.exists():
 spec=importlib.util.spec_from_file_location('staged_v3b',_STAGED);v3b=importlib.util.module_from_spec(spec);spec.loader.exec_module(v3b)
else:
 import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b
EPS=v3b.EPS;TICK=v3b.TICK;REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}

class EffectiveRepairCoverageShadow(v3b.FifoAggregateResponsibilityLadderV3B):
 def __init__(self,tape):
  super().__init__(tape);self.effective_coverage_shadow=[]
 def _origin_pure_probe_crosslot_core(self):
  L=self.q_ladder
  if L is None or str(L.get('role'))!='ECONOMIC_CORE':return False
  old=float(L.get('oldestRemainingAtSubmit') or 0);qty=float(L.get('passiveQty') or 0);agg=float(L.get('targetOutstandingAtSubmit') or 0)
  if not(old+EPS<qty<=agg+EPS):return False
  lot=self._lot_by_id(int(L['originResponsibilityId']))
  if lot is None:return False
  mix=lot.get('sourceRoleMix') or {};support={str(k) for k,v in mix.items() if float(v)>EPS}
  return support=={'PROBE_CORE'}
 def _live_repair_rows(self,t,side,bid,ask):
  out=[]
  for sid,key in list(self.slot_key.items()):
   o=self.orders.get(key)
   if o is None or str(o.get('side'))!=str(side):continue
   role=str(self.key_role.get(key) or '')
   if role not in REPAIR_ROLES:continue
   qty=float(o.get('qty') or 0);cum=float(o.get('cum') or 0);rem=max(0.0,qty-cum)
   if rem<=EPS:continue
   px=float(o.get('price') or 0);cancel=bool(o.get('cancelRequested'));at_touch=px+EPS>=bid;priority_lost=bid>px+EPS
   out.append({'slotId':int(sid),'key':str(key),'role':role,'price':px,'qty':qty,'cum':cum,'remainingQty':rem,'placedAt':int(o.get('placed') or t),'ageMs':int(t)-int(o.get('placed') or t),'cancelRequested':cancel,'bestBid':bid,'bestAsk':ask,'bidMinusOwnTicks':(bid-px)/TICK,'askMinusOwnTicks':(ask-px)/TICK,'atOrAbovePublicBid':bool(at_touch),'priorityLostNow':bool(priority_lost),'effectiveTouchCoverage':bool(at_touch and not cancel)})
  return out
 def _submit_protected_active_qty(self,t,qv):
  pnd=self.q_pending_active;L=self.q_ladder
  if pnd is not None and L is not None and self._origin_pure_probe_crosslot_core():
   side=str(pnd['side']);bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);rows=self._live_repair_rows(t,side,bid,ask);nom=sum(x['remainingQty'] for x in rows);touch=sum(x['remainingQty'] for x in rows if x['effectiveTouchCoverage']);noncancel=sum(x['remainingQty'] for x in rows if not x['cancelRequested']);lot=self._lot_by_id(int(L['originResponsibilityId']));origin_px=float(lot['price']) if lot else None
   snap={'t':int(t),'originResponsibilityId':int(L['originResponsibilityId']),'targetExpandSide':str(L['targetExpandSide']),'repairSide':side,'bestBid':bid,'bestAsk':ask,'originPrice':origin_px,'decisionActiveLimit':round(min(.99,ask+TICK),10),'activePairSum':origin_px+round(min(.99,ask+TICK),10) if origin_px is not None else None,'nominalCoverageQty':nom,'nonCancelCoverageQty':noncancel,'touchCoverageQty':touch,'nominalCoverageCount':len(rows),'touchCoverageCount':sum(x['effectiveTouchCoverage'] for x in rows),'cancelPendingCount':sum(x['cancelRequested'] for x in rows),'coverageRows':rows,'targetAggregateRemaining':self._aggregate_outstanding_expand_side(L['targetExpandSide'])}
   self.effective_coverage_shadow.append(snap)
  return super()._submit_protected_active_qty(t,qv)
 def run_qty(self,winner='__UNSCORED__'):
  r=super().run_qty(winner);r['effectiveRepairCoverageShadow']=self.effective_coverage_shadow;r['quantityLadderVersion']='V3B_EFFECTIVE_REPAIR_COVERAGE_SHADOW';return r

def parity(a,b):
 keys=['submits','fillEvents','fillSideAlternations','upQty','downQty','buyNotional','floor']
 return all(abs(float(a[k])-float(b[k]))<1e-10 for k in keys)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
 with tempfile.TemporaryDirectory(prefix='eff_cov_shadow_') as td:
  root=Path(td)
  with zipfile.ZipFile(a.bundle) as z:
   for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
  for i,mid in enumerate(mids,1):
   tape=root/'tapes'/f'{mid}.json.xz';s1=v3b.FifoAggregateResponsibilityLadderV3B(tape)
   try:A=s1.run_qty('__UNSCORED__')
   finally:s1.close()
   s2=EffectiveRepairCoverageShadow(tape)
   try:B=s2.run_qty('__UNSCORED__')
   finally:s2.close()
   pr=parity(A,B);row={'marketId':mid,'parity':pr,'snapshots':B['effectiveRepairCoverageShadow'],'ledgerInvariantViolations':B['quantityLedgerSummary']['invariantViolations'],'activity':{'submits':B['submits'],'fills':B['fillEvents'],'alternations':B['fillSideAlternations'],'twoSided':B.get('twoSidedMaterialized')}};rows.append(row)
   print(json.dumps({'progress':i,'marketId':mid,'parity':pr,'snapshots':[{'touchQty':round(x['touchCoverageQty'],6),'nomQty':round(x['nominalCoverageQty'],6),'cancelPending':x['cancelPendingCount'],'bid':x['bestBid'],'ask':x['bestAsk'],'activePair':x['activePairSum']} for x in row['snapshots']]},ensure_ascii=False),flush=True)
 out={'version':'V3B_EFFECTIVE_REPAIR_COVERAGE_SHADOW_V1','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'allParity':all(x['parity'] for x in rows),'allLedgerPass':all(not x['ledgerInvariantViolations'] for x in rows),'boundary':['behavior-inert V3B shadow','strict-past current public bid/ask + own live repair order state','touch coverage = not cancel-pending and own price >= current public best bid','no action mutation/no winner/no PnL/no NEW24-B']}
 op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allParity':out['allParity'],'allLedgerPass':out['allLedgerPass']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
