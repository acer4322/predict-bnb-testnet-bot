from __future__ import annotations
import argparse,json,os,tempfile,zipfile
from pathlib import Path
from tools import run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b
import sys
_STAGING=Path(__file__).resolve().parent
if str(_STAGING) not in sys.path: sys.path.insert(0,str(_STAGING))
from run_management_mainline_v3b_closed_loop_role_manager_v1 import ClosedLoopManager,metrics
EPS=v3b.EPS
class FSHighwaterManager(ClosedLoopManager):
 def __init__(self,tape):
  super().__init__(tape,'FS_HIGHWATER');self.fs_highwater={}
 def _open_one_option(self,t,qv,end):
  e=self._eligible(t,qv,end)
  if e is None:return v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
  lot=self._oldest_for_repair_side(e['weakSide'])
  if lot is None:return v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
  rid=int(lot['id']);u=float(self.inv['UP']);d=float(self.inv['DOWN']);c=float(self.cost);floor=min(u,d)-c;best=max(u,d)-c;fs=best+min(floor,0.0)
  hw=self.fs_highwater.get(rid)
  if hw is None:hw=fs;self.fs_highwater[rid]=fs
  choice='REPAIR' if fs < hw-EPS else 'REEXPAND'
  self.mgmt['ELIGIBLE']+=1;ok,side,role,cand=self._force_one(t,qv,choice)
  if ok:
   self.mgmt['FORCED_'+choice]+=1;self.last_managed_class=choice;self.fs_highwater[rid]=max(float(hw),float(fs))
   self.mgmt_events.append({'t':int(t),'responsibilityId':rid,'choice':choice,'fs':float(fs),'fsHighwater':float(hw),'floor':float(floor),'best':float(best),'repairProgressFrac':float(e['repairProgressFrac']),'remainingDebtQty':float(e['remainingDebtQty']),'candidate':cand})
   return None
  self.mgmt['FORCE_FAILED_FALLBACK_NATIVE']+=1
  return v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
 with tempfile.TemporaryDirectory(prefix='mgmt_fs_hw_') as td:
  root=Path(td)
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
   for m in mids:z.extract(f'tapes/{m}.json.xz',root)
  for i,m in enumerate(mids,1):
   tape=root/'tapes'/f'{m}.json.xz';w=co[m]['winner']
   b=ClosedLoopManager(tape,'NATIVE')
   try:rb=b.run_managed()
   finally:b.close()
   s=FSHighwaterManager(tape)
   try:rc=s.run_managed()
   finally:s.close()
   bm=metrics(rb,w);cm=metrics(rc,w);checks={'ledgerClean':not cm['ledgerViolations'],'max4':cm['maxSlots']<=4}
   rows.append({'marketId':m,'winnerPostHocOnly':w,'native':bm,'candidate':cm,'managementCounters':rc.get('managementCounters') or {},'managementEvents':rc.get('managementEvents') or [],'checks':checks})
   print(json.dumps({'progress':i,'of':len(mids),'marketId':m,'nativePnl':round(bm['pnl'],6),'candidatePnl':round(cm['pnl'],6),'delta':round(cm['pnl']-bm['pnl'],6),'fills':[bm['fills'],cm['fills']],'alternations':[bm['alternations'],cm['alternations']],'mgmt':rc.get('managementCounters') or {},'checks':checks},ensure_ascii=False),flush=True)
 out={'version':'MANAGEMENT_MAINLINE_V3B_FS_HIGHWATER_MANAGER_V1_20260907','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'allSafetyPass':all(all(r['checks'].values()) for r in rows),'boundary':['current V3B exact-HFT/exact FIFO','candidate only intervenes at legal partial-progress Repair/ReExpand seam','per-responsibility realized favorable-structure highwater','FS = Best + min(Floor,0), no tuned weight/threshold','at highwater choose ReExpand; below highwater choose Repair until recovered','pending Active/noneligible states remain native','same current V3B candidate/slot/responsibility authority','winner post-hoc scoring only','no Target/future/fixed time window','max4/no dream fill/no NEW24-B/no 8781']};op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allSafetyPass':out['allSafetyPass'],'markets':len(rows)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
