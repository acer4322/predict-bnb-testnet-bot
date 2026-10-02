from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,os,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v17_post_safe_surplus_authority as v17
except ImportError:v17=sib('v17pkgaudit','run_eth_repair_functional_exam_v17_post_safe_surplus_authority.py')
try:
 from tools import run_eth_repair_functional_exam_v15_economic_lane_admission as v15
except ImportError:v15=sib('v15pkgaudit','run_eth_repair_functional_exam_v15_economic_lane_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v16_phase_aware_economic_admission as v16
except ImportError:v16=sib('v16pkgaudit','run_eth_repair_functional_exam_v16_phase_aware_economic_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v13_anchored_wait10_scheduler as v13
except ImportError:v13=sib('v13pkgaudit','run_eth_repair_functional_exam_v13_anchored_wait10_scheduler.py')
try:
 from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
except ImportError:v9=sib('v9pkgaudit','run_eth_repair_functional_exam_v9_conjunctive_parallel_router.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v1 as ex1
EPS=1e-9
class Audit(v17.PostSafeSurplusSim):
 def __init__(self,*a,**kw):super().__init__(*a,**kw);self.thinBalancedTicks=0;self.packageLt1Ticks=0;self.packageSums=[]
 def _surplus_candidates(self,t,qv,qty):
  floor,u,d,cost=self._raw_floor();gross=u+d;absr=abs(u-d)/gross if gross>EPS else 0.
  if 0<=floor<1 and absr<=.02 and self.repairParent is None and self.outstanding_total()<=EPS:
   self.thinBalancedTicks+=1;s=float(qv['UP']['bid'])+float(qv['DOWN']['bid']);self.packageSums.append(s);self.packageLt1Ticks+=int(s<1.-1e-9)
  return super()._surplus_candidates(t,qv,qty)
 def run_exam_v17(self,*a,**kw):
  r=super().run_exam_v17(*a,**kw);r.update({'thinBalancedTicks':self.thinBalancedTicks,'packageLt1Ticks':self.packageLt1Ticks,'packageLt1Rate':self.packageLt1Ticks/self.thinBalancedTicks if self.thinBalancedTicks else 0.,'packageSumMin':min(self.packageSums,default=0.),'packageSumMean':sum(self.packageSums)/len(self.packageSums) if self.packageSums else 0.});return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--economic-model',required=True);ap.add_argument('--price-model',required=True);ap.add_argument('--surplus-model',required=True);ap.add_argument('--market-ids',default='1829435');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_pkg_audit_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');tim=v13.Wait10Runtime(a.timing_model);econ=v15.EconomicLaneValueRuntime(a.economic_model);price=v16.RepairPriceEnvelopeRuntime(a.price_model);sur=v17.SurplusValueRuntime(a.surplus_model);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=Audit(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:r=sim.run_exam_v17(models,cr['winner'])
   finally:sim.close()
   rows.append({'marketId':mid,**{k:r[k] for k in ['thinBalancedTicks','packageLt1Ticks','packageLt1Rate','packageSumMin','packageSumMean','pnlDiagnosticOnly']}});print(json.dumps(rows[-1]),flush=True)
  out={'version':'ETH_HFT_RESERVE_PACKAGE_OPPORTUNITY_V1','rows':rows,'aggregate':{'thinBalancedTicks':sum(r['thinBalancedTicks'] for r in rows),'packageLt1Ticks':sum(r['packageLt1Ticks'] for r in rows)}};op=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.write_text(json.dumps(out,indent=2),encoding='utf-8')
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
