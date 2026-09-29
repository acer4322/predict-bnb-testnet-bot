from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_78_persistent_intent_thesis_cycle_capital as r278
EPS=1e-9
class Audit(r278.PersistentIntentThesisCycleCapitalSim):
 def __init__(self,*a,**kw):super().__init__(*a,**kw);self.rows=[]
 def process(self,t):
  s0=len(self.splitEvents);super().process(t)
  for ev in self.splitEvents[s0:]:
   if ev.get('event')!='ROLE_FILL_SPLIT':continue
   k=str(ev.get('key'));m=self.riskTrancheMeta.get(k,{})
   if m.get('kind')!='R278_PERSISTENT_INTENT_THESIS_REARM' or float(ev.get('fillInc') or 0)<=EPS:continue
   rs='DOWN' if str(ev.get('side'))=='UP' else 'UP';levels=[]
   for rank,raw in enumerate(self._live_price_levels(rs),1):
    p=float(r278.v2.kprice(raw));q=1/p if p>EPS else 1e99
    if q<=EPS or q>12+EPS:continue
    ps=p+float(ev.get('price') or 0)
    if ps<=1+EPS:levels.append({'rank':rank,'price':p,'qty':q,'pairSum':ps})
   debt=float(self._scope_debt_qty());reserved=float(self._reserved_repair_quota(rs));unres=max(0.0,debt-reserved);b=levels[0] if levels else None
   self.rows.append({'t':int(t),'riskKey':k,'riskSide':ev.get('side'),'riskPrice':float(ev.get('price') or 0),'riskFillQty':float(ev.get('fillInc') or 0),
     'repairSide':rs,'nativeDebtAfterFill':debt,'reservedRepairAfterFill':reserved,'fixedUnreservedAfterFill':unres,
     'bestFavorableRepair':b,'fitsFixedUnreserved':bool(b and b['qty']<=unres+EPS),'fitsSharedParentDebt':bool(b and b['qty']<=debt+EPS)})
 def run_a(self,w):r=super().run_r278(w);r['r281Rows']=self.rows;return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r281_'))
 try:
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
   for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
  rows=[]
  for m in mids:
   s=Audit(tmp/f'{m}.json.xz',1,4)
   try:r=s.run_a(co[m]['winner'])
   finally:s.close()
   x={'marketId':m,'rows':r['r281Rows']};rows.append(x);print(json.dumps(x,ensure_ascii=False),flush=True)
  agg={'fillStates':sum(len(x['rows']) for x in rows),'fixedReachable':sum(y['fitsFixedUnreserved'] for x in rows for y in x['rows']),'sharedReachable':sum(y['fitsSharedParentDebt'] for x in rows for y in x['rows'])}
  Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps({'version':'MS4_R2_81_REARM_FILL_POSTMATERIALIZATION_REPAIR_CAPACITY_V1','researchOnly':True,'behaviorChange':False,'aggregate':agg,'markets':rows},indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg}))
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
