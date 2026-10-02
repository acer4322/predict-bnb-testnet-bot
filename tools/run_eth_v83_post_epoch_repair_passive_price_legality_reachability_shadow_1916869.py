from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
def sibling(name,path):
 p=Path(path); s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None: raise ImportError(p)
 m=importlib.util.module_from_spec(s); sys.modules[name]=m; s.loader.exec_module(m); return m
base=sibling('v83_child_for_price_reach',Path(__file__).resolve().with_name('run_eth_v83_existing_parent_post_epoch_child_materialization_shadow_1916869.py'))
birth=base.birth; v38=base.v38; v80=base.v80; EPS=1e-9; FIXED=1916869; QTY=2.1649963710093214
class PassivePriceReachabilityShadow(base.PostEpochChildMaterializationShadow):
 def __init__(self,*a,**kw): self.priceRows=[]; super().__init__(*a,**kw)
 def choose_authorized(self,t,end,proposed_side,proposed_qty):
  z=super().choose_authorized(t,end,proposed_side,proposed_qty)
  rp=getattr(self,'repairParent',None)
  if isinstance(rp,dict):
   pid=int(rp.get('id'))
   st=getattr(self,'generationEpochByParent',{}).get(pid)
   if st is not None and getattr(st,'armed',False) and str(rp.get('side'))=='DOWN':
    try:qv=birth.base.v1.quotes(self.book)
    except Exception:qv=None
    if qv and 'DOWN' in qv:
     bid=qv['DOWN'].get('bid'); ask=qv['DOWN'].get('ask')
     if bid is not None and float(bid)>EPS:
      bid=float(bid); ask=float(ask) if ask is not None else None; legal=1.0/bid
      self.priceRows.append({'t':int(t),'bid':bid,'ask':ask,'unchangedQty':QTY,'legalMinQtyAtBid':legal,'unchangedQtyVenueLegal':QTY+EPS>=legal,'nonCrossing':ask is None or bid<ask-EPS})
  return z
 def run_shadow2(self,models,winner):
  rr=super().run_shadow(models,winner); rr['passivePriceReachabilityRows']=self.priceRows[:5000]; return rr

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']: ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
 if a.market_id!=FIXED: raise ValueError(a.market_id)
 tmp=Path(tempfile.mkdtemp(prefix='v83_price_reach_')); stop=threading.Event()
 def hb():
  while not stop.wait(15): print(json.dumps({'heartbeat':'V83_PASSIVE_PRICE_REACH','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start()
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp); cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[FIXED]
  models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a); t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']; tape=tmp/'tapes'/f'{FIXED}.json.xz'
  c=PassivePriceReachabilityShadow(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
  try: rr=c.run_shadow2(models,cr['winner'])
  finally: c.close()
  rows=rr.get('passivePriceReachabilityRows',[]); legal=[x for x in rows if x['unchangedQtyVenueLegal'] and x['nonCrossing']]
  bids=[x['bid'] for x in rows]
  ss=birth.base.front.safety(rr); safe=all(float(v)<=EPS for v in ss.values())
  decision='LEGAL_UNCHANGED_QTY_PRICE_EXISTS_AUDIT_ECONOMIC_CEILING_AND_QUEUE' if legal else 'REJECT_PASSIVE_PRICE_LEGALITY_NO_UNCHANGED_QTY_VENUE_LEGAL_LEVEL'
  out={'version':'ETH_V83_POST_EPOCH_REPAIR_PASSIVE_PRICE_LEGALITY_REACHABILITY_SHADOW_1916869_RESULT','date':'2026-09-04','researchOnly':True,'marketId':FIXED,'decision':decision,'qty':QTY,'requiredBidForVenueLegality':1.0/QTY,'rowCount':len(rows),'bidMin':min(bids) if bids else None,'bidMax':max(bids) if bids else None,'legalNonCrossingCount':len(legal),'firstLegalRows':legal[:20],'safety':ss,'safetyZero':safe,'candidate':birth.slim(rr),'boundary':['shadow only','unchanged qty','no reprice action','no threshold/delay/TTL change','realistic HFT','no dream fill','no 8781']}
  Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,ensure_ascii=False),flush=True)
 finally: stop.set(); shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
