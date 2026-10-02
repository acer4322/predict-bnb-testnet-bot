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
v83=sib('eth_v83_for_v85c','run_eth_repair_v83_clean_modular_candidate_smoke.py');v80=v83.v80;v38=v83.v38;v1=v83.v1

class Shadow(v83.V83CleanModularCandidate):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.rawRepair=[];self._rawAt={}
 def choose_authorized(self,t,end,proposed_side,proposed_qty):
  ai=self.auth_inv();gap=abs(float(ai['UP'])-float(ai['DOWN']))
  z=super().choose_authorized(t,end,proposed_side,proposed_qty)
  if z is not None and str(z[3])=='REPAIR':
   self._rawAt[int(t)]={'t':int(t),'proposedSide':proposed_side,'rawProposedQty':float(proposed_qty),'authorizedQty':float(z[1]),'gap':gap,'role':str(z[3]),'side':str(z[0])}
  return z
 def _submit_authorized(self,t,qv,z,roles_this_tick):
  if z is not None and str(z[3])=='REPAIR':
   row=dict(self._rawAt.get(int(t),{'t':int(t),'rawProposedQty':None,'authorizedQty':float(z[1]),'gap':None,'side':str(z[0])}))
   side=str(z[0]);p=float(qv[side]['bid']);ai=self.auth_inv();gap=abs(float(ai['UP'])-float(ai['DOWN']));raw=float(row.get('rawProposedQty') if row.get('rawProposedQty') is not None else z[1]);legal=1.0/p if p>EPS else math.inf;floor,u,d,cost=self._raw_floor();floor=float(floor)
   maxq=gap/p if p>EPS else 0.0;safe=min(raw,maxq);overflow=max(0.0,safe-gap);vu=max(legal,gap) if math.isfinite(legal) else gap;v84overflow=max(0.0,vu-gap)
   hu=float(u)+(safe if side=='UP' else 0);hd=float(d)+(safe if side=='DOWN' else 0);hc=float(cost)+safe*p;hf=min(hu,hd)-hc
   row.update({'price':p,'gap':gap,'venueMinQty':legal,'rawExceedsGap':raw>gap+EPS,'floorBefore':floor,'floorPreservingMaxQty':maxq,'safeCandidateQty':safe,'safeRepairAllocation':min(safe,gap),'safeOverflowAllocation':overflow,'safeHypFloorAfter':hf,'safeHypFloorDelta':hf-floor,'v84VenueMinCandidateQty':vu,'v84VenueMinOverflow':v84overflow,'safeOverflowVsV84Ratio':overflow/v84overflow if v84overflow>EPS else None,'pre180':int(self.capEnd)-int(t)>180000})
   self.rawRepair.append(row)
  return super()._submit_authorized(t,qv,z,roles_this_tick)
 def run_shadow(self,models,winner):
  r=super().run_exam_v83(models,winner);r['v85cRawRepairRows']=self.rawRepair;return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 if a.market_id!=MID:raise ValueError(a.market_id)
 tmp=Path(tempfile.mkdtemp(prefix='eth_v85c_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V85C_PRECAP_BUDGET','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V85C_PRECAP_BUDGET_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];cr={int(r['marketId']):r for r in co}[MID];models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
  c=Shadow(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  try:r=c.run_shadow(models,cr['winner'])
  finally:c.close()
  rows=[x for x in r['v85cRawRepairRows'] if x.get('pre180')];cross=[x for x in rows if x.get('rawExceedsGap') and float(x.get('safeOverflowAllocation') or 0)>EPS];best=max(cross,key=lambda x:float(x.get('safeOverflowVsV84Ratio') or 0),default=None)
  keep=bool(best is not None and float(best.get('safeOverflowAllocation') or 0)>float(best.get('v84VenueMinOverflow') or 0)*1.25)
  out={'version':'ETH_REPAIR_V85C_PRECAP_CARRIER_BUDGET_SHADOW','date':'2026-09-03','researchOnly':True,'marketId':MID,'decision':'KEEP_PRECAP_CARRIER_BUDGET_FOR_FUNCTIONAL_MICROWORLD' if keep else 'REQUIRE_NEW_CARRIER_BUDGET_HEAD','summary':{'repairProposalRows':len(rows),'rawExceedsGapRows':sum(x.get('rawExceedsGap') for x in rows),'safeCompositeRows':len(cross),'maxSafeOverflow':max([float(x['safeOverflowAllocation']) for x in cross]+[0.0]),'maxV84VenueMinOverflow':max([float(x['v84VenueMinOverflow']) for x in rows]+[0.0]),'bestRatio':None if best is None else best.get('safeOverflowVsV84Ratio')},'bestExample':best,'rows':rows,'behaviorParity':{'floor':r.get('floor'),'pnl':r.get('pnlDiagnosticOnly'),'fills':r.get('actualFillEvents')},'boundary':['shadow only','no behavior change','no Target qty runtime input','floor-preserving capacity descriptive safety bound','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'summary':out['summary'],'bestExample':best},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
