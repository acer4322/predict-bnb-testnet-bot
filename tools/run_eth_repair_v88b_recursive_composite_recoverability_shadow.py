from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9;MID=1912961

def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None:raise ImportError(p)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v84=sib('eth_v84_for_v88b','run_eth_repair_v84b_composite_repair_behavior_1912961.py');v80=v84.v80;v38=v84.v38

class V88B(v84.V84BCompositeRepair):
 def __init__(self,*a,**kw):
  self.v88OverflowBirthClocks=set();self.v88Rows=[]
  super().__init__(*a,**kw)
 def _maybe_hard_active(self,t):return False
 def _scan_v84(self,t):
  before={k:m.get('overflowBornAt') for k,m in getattr(self,'v84Composite',{}).items()}
  out=super()._scan_v84(t)
  for k,m in getattr(self,'v84Composite',{}).items():
   b=m.get('overflowBornAt')
   if b is not None and before.get(k) is None:self.v88OverflowBirthClocks.add(int(b))
  return out
 def _recover(self,t,side,qv,q,px):
  floor,u,d,cost=self._raw_floor();hu=float(u)+(q if side=='UP' else 0.0);hd=float(d)+(q if side=='DOWN' else 0.0);hc=float(cost)+q*px;hfloor=min(hu,hd)-hc
  repair='DOWN' if side=='UP' else 'UP';weak_qty=hd if repair=='DOWN' else hu;strong_qty=hu if repair=='DOWN' else hd;hgap=max(0.0,strong_qty-weak_qty)
  owned=0.0;gain=0.0
  for key,e,rem in self.lane_unresolved('REPAIR'):
   if e.get('side')!=repair:continue
   rq=float(rem);op=float(self.orders.get(key,{}).get('price') or e.get('price') or 0.0);owned+=rq;gain+=rq*(1.0-op) if EPS<op<1-EPS else 0.0
  projected=hfloor+gain;room=max(0.0,hgap-owned);ceiling=(strong_qty-hc)/hgap if hgap>EPS else None;rbid=float(qv[repair]['bid']) if qv.get(repair,{}).get('bid') is not None else None;adm=min(rbid,float(ceiling)) if rbid is not None and ceiling is not None else None;need=legal=req=None;ok=False;reason=''
  if projected>=-EPS:ok=True;reason='EXISTING_REPAIR_RESERVATION_COVERS_PROJECTED_FLOOR'
  elif adm is None or not(EPS<adm<1-EPS):reason='NO_ADMISSIBLE_FUTURE_REPAIR_PRICE'
  else:
   need=max(0.0,-projected)/(1.0-adm);legal=1.0/adm;req=max(need,legal);ok=req<=room+EPS;reason='PASS' if ok else 'FUTURE_REPAIR_QTY_EXCEEDS_ROOM'
  return {'floorBefore':float(floor),'hypFloorAfter':float(hfloor),'immediateFloorDelta':float(hfloor-floor),'futureRepairSide':repair,'futureRepairGap':hgap,'ownedFutureRepairQty':owned,'ownedFutureRepairGain':gain,'projectedFloorAfterOwnedRepair':projected,'futureRepairRoom':room,'economicRepairCeiling':ceiling,'futureRepairBid':rbid,'admissibleFutureRepairPrice':adm,'futureNeedQty':need,'futureLegalQty':legal,'futureRequiredQty':req,'recoverable':bool(ok),'reason':reason}
 def _submit_authorized(self,t,qv,z,roles_this_tick):
  if z is not None:
   side,qty,oldp,role,oid=z;rp=getattr(self,'repairParent',None);born=int(rp.get('bornAt') or -1) if isinstance(rp,dict) else -1
   if role=='REPAIR' and born in self.v88OverflowBirthClocks:
    ai=self.auth_inv();gap=abs(float(ai['UP'])-float(ai['DOWN']));p=float(qv[side]['bid']);legal=1.0/p if p>EPS else math.inf
    if math.isfinite(legal) and legal>gap+EPS and legal<=12.0+EPS and gap>EPS and int(self.capEnd)-int(t)>180000:
     row={'t':int(t),'parentId':int(rp.get('id')),'parentBornAt':born,'side':side,'price':p,'gap':gap,'legalQty':legal,'overflowIfFilled':legal-gap};row.update(self._recover(t,side,qv,legal,p));self.v88Rows.append(row)
  return super()._submit_authorized(t,qv,z,roles_this_tick)
 def run_v88b(self,models,winner):
  r=self.run_exam_v84b(models,winner);r.update({'v88OverflowBirthClocks':sorted(self.v88OverflowBirthClocks),'v88RecursiveShadowRows':self.v88Rows[:300]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 if a.market_id!=MID:raise ValueError(a.market_id)
 tmp=Path(tempfile.mkdtemp(prefix='eth_v88b_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V88B_RECURSIVE_SHADOW','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V88B_START','market':MID}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
  c=V88B(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  try:r=c.run_v88b(models,cr['winner'])
  finally:c.close()
  rows=r.get('v88RecursiveShadowRows',[]);cross=len(rows);neg=sum(float(x.get('immediateFloorDelta') or 0)<-EPS for x in rows);rec=sum(bool(x.get('recoverable')) for x in rows);negrec=sum(float(x.get('immediateFloorDelta') or 0)<-EPS and bool(x.get('recoverable')) for x in rows)
  parity=abs(float(r.get('floor') or 0)-(-0.33333333333333326))<1e-7 and abs(float(r.get('v84OverflowAllocatedQty') or 0)-0.46099290780141833)<1e-7
  decision='KEEP_RECOVERABILITY_PLUS_EXECUTION_PIPELINE_FOR_V88C' if cross>0 and negrec>0 and parity else 'REJECT_OR_DIAGNOSE_RECURSIVE_COMPOSITE_RECOVERABILITY'
  out={'version':'ETH_REPAIR_V88B_RECURSIVE_COMPOSITE_RECOVERABILITY_SHADOW','date':'2026-09-03','researchOnly':True,'marketId':MID,'decision':decision,'summary':{'rows':cross,'negativeImmediateFloorRows':neg,'recoverableRows':rec,'negativeButRecoverableRows':negrec,'behaviorParity':parity},'rows':rows,'behavior':{'floor':r.get('floor'),'pnl':r.get('pnlDiagnosticOnly'),'overflowAllocated':r.get('v84OverflowAllocatedQty'),'overflowPaid':r.get('v84OverflowPaidQty')},'boundary':['shadow only','recursive overflow-born Repair parent only','minimum-legal carrier only','Active disabled','no Target runtime input','no tuning','realistic HFT only','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'summary':out['summary'],'sample':rows[:8]},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
