from __future__ import annotations
import argparse,importlib.util,json,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd().parent/'run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate.py'
if _STAGED.exists():
 spec=importlib.util.spec_from_file_location('staged_v3b',_STAGED);v3b=importlib.util.module_from_spec(spec);spec.loader.exec_module(v3b)
else: import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b
EPS=v3b.EPS

class PostActiveSpareShadow(v3b.FifoAggregateResponsibilityLadderV3B):
 def __init__(self,tape):super().__init__(tape);self.post_active_spare=[]
 def _submit_protected_active_qty(self,t,qv):
  L=self.q_ladder;pnd=self.q_pending_active
  before_slots=len(self.slot_key);ok=super()._submit_protected_active_qty(t,qv)
  if ok and L is not None and pnd is not None and str(L.get('role'))=='ECONOMIC_CORE':
   lot=self._lot_by_id(int(L['originResponsibilityId']));mix=(lot or {}).get('sourceRoleMix') or {};support={str(k) for k,v in mix.items() if float(v)>EPS};old=float(L.get('oldestRemainingAtSubmit') or 0);qty=float(L.get('passiveQty') or 0);agg=float(L.get('targetOutstandingAtSubmit') or 0)
   if support=={'PROBE_CORE'} and old+EPS<qty<=agg+EPS:
    self.post_active_spare.append({'t':int(t),'originResponsibilityId':int(L['originResponsibilityId']),'slotsBeforeActive':before_slots,'slotsAfterActive':len(self.slot_key),'spareAfterActive':self.max_slots-len(self.slot_key),'maxSlots':self.max_slots,'activeKey':L.get('activeKey')})
  return ok
 def run_qty(self,winner='__UNSCORED__'):
  r=super().run_qty(winner);r['postActiveSpareCapacityShadow']=self.post_active_spare;return r

def parity(a,b):
 for k in ['submits','fillEvents','fillSideAlternations','upQty','downQty','buyNotional','floor']:
  if abs(float(a[k])-float(b[k]))>1e-10:return False
 return True

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
 with tempfile.TemporaryDirectory(prefix='post_active_spare_') as td:
  root=Path(td)
  with zipfile.ZipFile(a.bundle) as z:
   for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
  for i,mid in enumerate(mids,1):
   tape=root/'tapes'/f'{mid}.json.xz';s=v3b.FifoAggregateResponsibilityLadderV3B(tape)
   try:A=s.run_qty('__UNSCORED__')
   finally:s.close()
   s=PostActiveSpareShadow(tape)
   try:B=s.run_qty('__UNSCORED__')
   finally:s.close()
   row={'marketId':mid,'parity':parity(A,B),'snapshots':B['postActiveSpareCapacityShadow'],'ledger':B['quantityLedgerSummary']['invariantViolations']};rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
 out={'version':'V3B_POST_ACTIVE_SPARE_CAPACITY_SHADOW_V1','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'rows':rows,'allParity':all(r['parity'] for r in rows),'allLedgerPass':all(not r['ledger'] for r in rows),'boundary':['behavior inert','slot_key occupancy after actual Active submit','no PnL/winner/no NEW24-B']};op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
