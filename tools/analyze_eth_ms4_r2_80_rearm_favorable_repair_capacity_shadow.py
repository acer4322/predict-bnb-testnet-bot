from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_78_persistent_intent_thesis_cycle_capital as r278
EPS=1e-9
class Audit(r278.PersistentIntentThesisCycleCapitalSim):
 def __init__(self,*a,**kw):super().__init__(*a,**kw);self.rows=[]
 def _try_cycle_rearm(self,t,end):
  n=int(self.r278Stats.get('REARM_SUBMIT',0));ok=super()._try_cycle_rearm(t,end)
  if ok and int(self.r278Stats.get('REARM_SUBMIT',0))>n:
   ev=next(x for x in reversed(self.r278Events) if x.get('event')=='R278_INTENT_THESIS_CYCLE_REARM_SUBMIT')
   rs='DOWN' if ev['thesisSide']=='UP' else 'UP';levels=[]
   for rank,raw in enumerate(self._live_price_levels(rs),1):
    p=float(r278.v2.kprice(raw));q=1/p if p>EPS else 1e99
    if q<=EPS or q>12+EPS:continue
    if p+float(ev['price'])<=1+EPS:levels.append({'rank':rank,'price':p,'qty':q,'pairSum':p+float(ev['price'])})
   debt=float(self._scope_debt_qty());reserved=float(self._reserved_repair_quota(rs));unres=max(0.0,debt-reserved)
   b=levels[0] if levels else None
   self.rows.append({'t':int(t),'riskKey':ev['key'],'riskSide':ev['thesisSide'],'riskPrice':float(ev['price']),'repairSide':rs,
     'nativeDebt':debt,'reservedRepair':reserved,'fixedUnreserved':unres,'bestFavorableRepair':b,
     'fitsFixedUnreserved':bool(b and b['qty']<=unres+EPS),'fitsSharedParentDebt':bool(b and b['qty']<=debt+EPS)})
  return ok
 def run_a(self,w):r=super().run_r278(w);r['r280Rows']=self.rows;return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r280_'))
 try:
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
   for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
  out=[]
  for m in mids:
   s=Audit(tmp/f'{m}.json.xz',1,4)
   try:r=s.run_a(co[m]['winner'])
   finally:s.close()
   x={'marketId':m,'rows':r['r280Rows']};out.append(x);print(json.dumps(x,ensure_ascii=False),flush=True)
  agg={'rearmStates':sum(len(x['rows']) for x in out),'fixedReachable':sum(y['fitsFixedUnreserved'] for x in out for y in x['rows']),'sharedReachable':sum(y['fitsSharedParentDebt'] for x in out for y in x['rows'])}
  Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps({'version':'MS4_R2_80_REARM_FAVORABLE_REPAIR_CAPACITY_SHADOW_V1','researchOnly':True,'behaviorChange':False,'aggregate':agg,'markets':out},indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg}))
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
