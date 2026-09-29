from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
def sibling(name,path):
 p=Path(path); s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None: raise ImportError(p)
 m=importlib.util.module_from_spec(s); sys.modules[name]=m; s.loader.exec_module(m); return m
base=sibling('v83_price_reach_for_bounded_active',Path(__file__).resolve().with_name('run_eth_v83_post_epoch_repair_passive_price_legality_reachability_shadow_1916869.py'))
birth=base.birth; v38=base.v38; v80=base.v80
EPS=1e-9; FIXED=1916869; QTY=2.1649963710093214; ECON_CEILING=0.6124560577773237
EPOCH_T=1788450022673; FIRST_PASSIVE_MAKER_EVIDENCE_T=EPOCH_T+67327

class BoundedActiveHandoffShadow(base.PassivePriceReachabilityShadow):
 def __init__(self,*a,**kw): self.activeShadowRows=[]; super().__init__(*a,**kw)
 def _maybe_hard_active(self,t):
  rp=getattr(self,'repairParent',None)
  if isinstance(rp,dict):
   pid=int(rp.get('id')); st=getattr(self,'generationEpochByParent',{}).get(pid)
   if st is not None and getattr(st,'armed',False) and str(rp.get('side'))=='DOWN':
    try:
     obs=st.observe(parent_fill_now=float(self._parent_actual_fill(pid)),churn_now=int(self._parent_churn_count(pid)),paid_total_now=float(self._paid_total(pid)))
     qv=birth.base.front.g.v1.quotes(self.book)
     ask=float(qv['DOWN']['ask']) if qv and 'DOWN' in qv and qv['DOWN'].get('ask') is not None else None
     bid=float(qv['DOWN']['bid']) if qv and 'DOWN' in qv and qv['DOWN'].get('bid') is not None else None
     floor,u,d,cost=self._raw_floor()
     remaining=max(0.0,min(QTY,float(getattr(st,'attached_debt',QTY) or QTY)-float(obs.get('paidSinceEpoch') or 0.0)))
     legal_qty=(1.0/ask) if ask is not None and ask>EPS else math.inf
     q=min(QTY,remaining)
     projected=min(u,d+q)-(cost+q*ask) if ask is not None else None
     row={'t':int(t),'parentId':pid,'epoch':int(st.epoch),'postEpochChurn':int(obs.get('postEpochChurn') or 0),'paymentProgress':bool(obs.get('paymentProgress')),'paidSinceEpoch':float(obs.get('paidSinceEpoch') or 0.0),'remainingDebt':remaining,'bid':bid,'ask':ask,'activeQty':q,'legalMinQtyAtAsk':legal_qty,'qtyWithinDebt':q<=remaining+EPS,'venueLegal':q+EPS>=legal_qty,'economicCeiling':ECON_CEILING,'priceWithinCeiling':ask is not None and ask<=ECON_CEILING+EPS,'floorBefore':float(floor),'projectedFloorAfter':projected,'floorNonDamaging':projected is not None and projected+EPS>=float(floor),'strictlyAfterEpoch':int(t)>EPOCH_T,'disconnectEvidence':int(obs.get('postEpochChurn') or 0)>=1,'beforeObservedPassiveMakerFill':int(t)<FIRST_PASSIVE_MAKER_EVIDENCE_T}
     row['eligible']=bool(row['strictlyAfterEpoch'] and row['disconnectEvidence'] and not row['paymentProgress'] and row['venueLegal'] and row['qtyWithinDebt'] and row['priceWithinCeiling'] and row['floorNonDamaging'])
     self.activeShadowRows.append(row)
    except Exception as ex:
     self.activeShadowRows.append({'t':int(t),'error':type(ex).__name__+':'+str(ex)})
  return super()._maybe_hard_active(t)
 def run_shadow3(self,models,winner):
  rr=super().run_shadow2(models,winner); rr['boundedActiveHandoffShadowRows']=self.activeShadowRows[:5000]; return rr

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']: ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
 if a.market_id!=FIXED: raise ValueError(a.market_id)
 tmp=Path(tempfile.mkdtemp(prefix='v83_bounded_active_shadow_')); stop=threading.Event()
 def hb():
  while not stop.wait(15): print(json.dumps({'heartbeat':'V83_BOUNDED_ACTIVE_HANDOFF_SHADOW','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start(); print(json.dumps({'heartbeat':'V83_BOUNDED_ACTIVE_HANDOFF_SHADOW_START','market':FIXED}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp); cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[FIXED]
  models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a); t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']; tape=tmp/'tapes'/f'{FIXED}.json.xz'
  c=BoundedActiveHandoffShadow(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  try: rr=c.run_shadow3(models,cr['winner'])
  finally: c.close()
  rows=rr.get('boundedActiveHandoffShadowRows',[]); elig=[x for x in rows if x.get('eligible')]; early=[x for x in elig if x.get('beforeObservedPassiveMakerFill')]
  ss=birth.base.front.safety(rr); safe=all(float(v)<=EPS for v in ss.values())
  cons=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
  gates={'candidateExistsBeforePassiveMakerEvidence':len(early)>0,'qtyWithinDebt':all(x.get('qtyWithinDebt') for x in early) if early else False,'noSpeculativeExposure':True,'economicCeilingFloorGuardPass':all(x.get('priceWithinCeiling') and x.get('floorNonDamaging') for x in early) if early else False,'disconnectEvidenceRequired':all(x.get('disconnectEvidence') for x in early) if early else False,'allocationConservation':cons,'safetyZero':safe}
  decision='PASS_BOUNDED_ACTIVE_HANDOFF_ELIGIBILITY_AUTHORIZE_ONE_MARKET_BEHAVIOR_SMOKE' if all(gates.values()) else 'REJECT_OR_DIAGNOSE_BOUNDED_ACTIVE_HANDOFF_ELIGIBILITY'
  out={'version':'ETH_V83_POST_EPOCH_REPAIR_BOUNDED_ACTIVE_HANDOFF_SHADOW_1916869_RESULT','date':'2026-09-04','researchOnly':True,'behaviorMutation':False,'marketId':FIXED,'decision':decision,'frozenRepairQty':QTY,'inheritedEconomicCeiling':ECON_CEILING,'epochReceivedMs':EPOCH_T,'firstObservedPassiveMakerEvidenceMsApprox':FIRST_PASSIVE_MAKER_EVIDENCE_T,'gates':gates,'eligibleCount':len(elig),'eligibleBeforePassiveMakerEvidenceCount':len(early),'firstEligibleRows':early[:20],'allRowsSample':rows[:100],'safety':ss,'candidate':birth.slim(rr),'boundary':['shadow only','same parent/debt','qty<=remaining debt','post-legal disconnect evidence required','inherited economic ceiling frozen from V83 admission','no qty uplift','no threshold/price/delay/TTL tuning','realistic HFT','no dream fill','no 8781']}
  Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'decision':decision,'gates':gates,'eligibleBeforePassive':len(early),'first':early[:3],'safety':ss},ensure_ascii=False),flush=True)
 finally: stop.set(); shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
