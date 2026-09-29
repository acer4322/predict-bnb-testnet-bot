from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,os,importlib.util,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

def sib(name,filename):
 p=Path(__file__).resolve().with_name(filename);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None: raise ImportError(p)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

v34b=sib('eth_v34b_for_v35','run_eth_repair_v34b_parent_ledger_readiness_shadow.py')
v30=v34b.v30
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9

class InitialStructuralShadow(v34b.V34ReadinessShadow):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v35InitialAudit=[]
 def _project_initial_repair(self,qv):
  side=self._signal_side(qv);p=float(qv[side]['bid']);q=1.0/p if p>EPS else 1e99
  if q<=EPS or q>12.+EPS:return {'feasible':False,'reason':'FIRST_LEG_QTY_INVALID','side':side,'firstPrice':p,'firstQty':q}
  floor,u,d,cost=self._raw_floor();hu=float(u)+(q if side=='UP' else 0.0);hd=float(d)+(q if side=='DOWN' else 0.0);hc=float(cost)+p*q;hfloor=min(hu,hd)-hc
  repair='DOWN' if side=='UP' else 'UP';ceiling=1.0-p-0.01;rbid=float(qv[repair]['bid']);rp=min(ceiling,rbid)
  gap=abs(hu-hd);deficit=max(0.0,-hfloor)
  base={'side':side,'firstPrice':p,'firstQty':q,'floorBefore':float(floor),'hypFloor':hfloor,'deficit':deficit,'room':gap,'repairSide':repair,'repairPrice':rp,'repairCeiling':ceiling,'repairBid':rbid}
  if deficit<=EPS:return {**base,'feasible':True,'reason':'NO_DEFICIT','needQty':0.0,'legalQty':0.0,'requiredQty':0.0}
  if rp<=EPS or rp>=1.0-EPS:return {**base,'feasible':False,'reason':'NO_ADMISSIBLE_REPAIR_PRICE'}
  need=deficit/(1.0-rp);legal=1.0/rp;req=max(need,legal);feasible=req<=gap+EPS
  return {**base,'feasible':bool(feasible),'reason':'PASS' if feasible else 'LEGAL_OR_PAYOFF_QTY_EXCEEDS_ROOM','needQty':need,'legalQty':legal,'requiredQty':req}
 def _start_reserve_builder(self,t,qv):
  initial=self.thesis is None
  a=self._project_initial_repair(qv) if initial else None
  ok=super()._start_reserve_builder(t,qv)
  if initial and a is not None:
   self.v35InitialAudit.append({'t':int(t),**a,'submitAccepted':bool(ok)})
  return ok
 def run_exam_v35(self,models,winner):
  r=super().run_exam_v34(models,winner)
  r['v35InitialStructuralAudit']=self.v35InitialAudit[:100]
  r['v35AcceptedInitialStructuralAudit']=[x for x in self.v35InitialAudit if x.get('submitAccepted')][:20]
  return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v35_initial_structural_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=v30.load_runtime(a);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=InitialStructuralShadow(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:r=sim.run_exam_v35(models,cr['winner'])
   finally:sim.close()
   accepted=r.get('v35AcceptedInitialStructuralAudit') or []
   rows.append({'marketId':mid,'functional':r})
   print(json.dumps({'marketId':mid,'acceptedInitial':accepted[:2],'firstFills':r.get('thesisFirstActualFills'),'parentLedger':r.get('v34ParentLedger')},ensure_ascii=False),flush=True)
  out={'version':'ETH_REPAIR_V35_INITIAL_STRUCTURAL_RECOVERABILITY_SHADOW','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'selectedMarketIds':[x['marketId'] for x in rows],'rows':rows,'boundary':['fixed consumed realistic-HFT','same V30 behavior','V31 geometry applied as initial-open shadow only','future parent completion scoring only','no winner/PnL selection','no threshold sweep','no 8781']}
  Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
