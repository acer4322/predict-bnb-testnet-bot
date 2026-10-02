from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(file)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v36=sib('eth_v36_for_v41','run_eth_repair_v36_event_confirmed_shared_active_child.py');v1=v36.v1;EPS=1e-9

class V41Shadow(v36.V36EventConfirmedActive):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v41Checks=0;self.v41Eligible=0;self.v41BlockedNoRoom=0;self.v41BlockedNoPrice=0;self.v41Rows=[];self._lastSig=None
 def _live_expand_child(self):
  for key,e,rem in getattr(self,'carrierLedger',{}).values() if False else []:pass
  for key,e in getattr(self,'carrierLedger',{}).items():
   if e.get('objectiveRole')!='EXPAND':continue
   if float(e.get('submittedQty') or 0)-float(e.get('actualFilled') or 0)<=EPS:continue
   o=self.orders.get(key);live=False
   try:live=bool(o and v1.live(self.snap(o).get('status')))
   except Exception:pass
   if live:return True
  return False
 def _shadow(self,t):
  rp=self.repairParent;th=self.thesis
  if rp is None or th is None or int(self.capEnd)-int(t)<=180000 or self._live_expand_child():return
  side=th.get('side');repair='DOWN' if side=='UP' else 'UP' if side=='DOWN' else None
  if repair is None or rp.get('side')!=repair:return
  qv=v1.quotes(self.book)
  if not qv or qv.get(side,{}).get('bid') is None or qv.get(repair,{}).get('bid') is None:return
  ep=float(qv[side]['bid']);rbid=float(qv[repair]['bid'])
  if not(EPS<ep<1-EPS and EPS<rbid<1-EPS):return
  eq=1.0/ep
  floor,u,d,cost=self._raw_floor();hu=float(u)+(eq if side=='UP' else 0.0);hd=float(d)+(eq if side=='DOWN' else 0.0);hc=float(cost)+eq*ep;hfloor=min(hu,hd)-hc
  weakQty=hd if repair=='DOWN' else hu;strongQty=hu if repair=='DOWN' else hd;hgap=max(0.0,strongQty-weakQty)
  rows=self.lane_unresolved('REPAIR');owned=0.0;reservedGain=0.0
  for key,e,rem in rows:
   if e.get('side')!=repair:continue
   q=float(rem);owned+=q;op=float(self.orders.get(key,{}).get('price') or e.get('price') or 0.0)
   if EPS<op<1-EPS:reservedGain+=q*(1.0-op)
  projAfterOwned=hfloor+reservedGain;room=max(0.0,hgap-owned)
  ceiling=(strongQty-hc)/hgap if hgap>EPS else None
  rpprice=min(rbid,float(ceiling)) if ceiling is not None else None
  need=legal=req=None;feasible=False;reason=''
  if projAfterOwned>=-EPS:
   feasible=True;reason='EXISTING_REPAIR_RESERVATION_COVERS_PROJECTED_FLOOR'
  elif rpprice is None or not(EPS<rpprice<1-EPS):
   reason='NO_ADMISSIBLE_FUTURE_REPAIR_PRICE';self.v41BlockedNoPrice+=1
  else:
   need=max(0.0,-projAfterOwned)/(1.0-rpprice);legal=1.0/rpprice;req=max(need,legal);feasible=req<=room+EPS;reason='PASS' if feasible else 'FUTURE_REPAIR_QTY_EXCEEDS_ROOM'
   if not feasible:self.v41BlockedNoRoom+=1
  self.v41Checks+=1
  sig=(int(rp.get('id')),round(ep,4),round(rbid,4),round(owned,6),round(room,6),bool(feasible))
  if feasible:self.v41Eligible+=1
  if sig!=self._lastSig and len(self.v41Rows)<300:
   self.v41Rows.append({'t':int(t),'parentId':int(rp.get('id')),'thesisSide':side,'expandPrice':ep,'expandQty':eq,'repairSide':repair,'repairBid':rbid,'hypFloorAfterExpand':hfloor,'ownedRepairQty':owned,'ownedRepairGain':reservedGain,'projectedFloorAfterOwnedRepair':projAfterOwned,'repairRoomAfterExpand':room,'economicRepairCeiling':ceiling,'admissibleFutureRepairPrice':rpprice,'futureNeedQty':need,'futureLegalQty':legal,'futureRequiredQty':req,'recoverable':bool(feasible),'reason':reason})
   self._lastSig=sig
 def process(self,t):super().process(t);self._shadow(t)
 def cancel_expired(self,t):super().cancel_expired(t);self._shadow(t)
 def run_exam_v41(self,models,winner):
  r=super().run_exam_v36(models,winner);r.update({'v41Checks':self.v41Checks,'v41Eligible':self.v41Eligible,'v41BlockedNoRoom':self.v41BlockedNoRoom,'v41BlockedNoPrice':self.v41BlockedNoPrice,'v41Rows':self.v41Rows});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v41_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V41_RUN','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V41_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=v36.v34.v30.load_runtime(a);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];s=V41Shadow(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:r=s.run_exam_v41(models,cr['winner'])
   finally:s.close()
   rows.append({'marketId':mid,'functional':r});print(json.dumps({'marketId':mid,'checks':r['v41Checks'],'eligible':r['v41Eligible'],'floor':r['floor'],'absNet':r['absNet'],'parents':r.get('repairParentBirths')},ensure_ascii=False),flush=True)
  sm=lambda k:sum(float(x['functional'].get(k) or 0) for x in rows);eligibleMarkets=sum(1 for x in rows if int(x['functional'].get('v41Eligible') or 0)>0)
  agg={'markets':len(rows),'eligibleMarkets':eligibleMarkets,'checks':int(sm('v41Checks')),'eligibleChecks':int(sm('v41Eligible')),'blockedNoRoom':int(sm('v41BlockedNoRoom')),'blockedNoPrice':int(sm('v41BlockedNoPrice')),'truthMismatch':sm('authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('overOwnedSubmitViolations'),'repairDrift':sm('repairToExpandAtFirstFill')}
  out={'version':'ETH_REPAIR_V41_LIVE_PARENT_PARALLEL_EXPAND_RECOVERABILITY_SHADOW','researchOnly':True,'behaviorChange':False,'aggregate':agg,'supportAdequate':eligibleMarkets>=3,'rows':rows,'boundary':['shadow only; no submit','one venue-minimum same-thesis hypothetical child','existing Repair reservations remain authoritative','future extra Repair uses exact payoff/legal room geometry','no fixed cycle count','no winner/PnL','realistic HFT','no 8781']};Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg,'supportAdequate':out['supportAdequate']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
