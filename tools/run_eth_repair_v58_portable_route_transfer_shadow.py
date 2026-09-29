from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v56=sib('eth_v56_for_v58','run_eth_repair_v56_target_book_hazard_parallel_residual_shadow.py')
port=sib('eth_port_v57_for_v58','train_target_eth_repair_taker_portable_normalized_v57.py')
v38=v56.v38;EPS=1e-9

class V58PortableTransfer(v56.V56HazardParallelShadow):
 def __init__(self,*a,portable_art=None,**kw):
  super().__init__(*a,**kw);self.v58Art=portable_art;self.v58Model=portable_art['model'];self.v58Scores=[];self.v58FeasibleScores=[]
 def _shadow_score(self,t):
  n=len(self.v56Rows);super()._shadow_score(t)
  if len(self.v56Rows)<=n:return
  x,vals=self._feature_vector(t)
  if vals is None:return
  z=port.portable_values(vals);xp=np.asarray([[z[k] for k in port.PORTABLE_FEATURES]],np.float32);p=float(self.v58Model.predict_proba(xp)[0,1]);self.v56Rows[-1]['portableHazard']=p;self.v58Scores.append(p)
  if self.v56Rows[-1].get('feasible'):self.v58FeasibleScores.append(p)
 def run_exam_v58(self,models,winner):
  r=super().run_exam_v56(models,winner);r['v58PortableScoreStats']=v56.qstats(self.v58Scores);r['v58PortableFeasibleScoreStats']=v56.qstats(self.v58FeasibleScores);return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','hazard-model','portable-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v58_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V58','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V58_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];haz=joblib.load(a.hazard_model);portable=joblib.load(a.portable_model);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=V58PortableTransfer(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,hazard_art=haz,portable_art=portable)
   try:r=sim.run_exam_v58(models,cr['winner'])
   finally:sim.close()
   rows.append({'marketId':mid,'winner':cr['winner'],'functional':r});print(json.dumps({'marketId':mid,'eligible':r['v56EligibleStates'],'feasible':r['v56FeasibleStates'],'raw':r['v56FeasibleScoreStats'],'portable':r['v58PortableFeasibleScoreStats']},ensure_ascii=False),flush=True)
  out={'version':'ETH_REPAIR_V58_PORTABLE_ROUTE_TRANSFER_SHADOW','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'markets':len(rows),'rows':rows,'boundary':['V52/V53 behavior unchanged','raw frozen hazard and V57 portable score evaluated on identical OUR HFT states','no runtime threshold','V50 feasible states retained for transfer audit','no winner/PnL trigger','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'markets':len(rows),'feasibleStates':sum(x['functional']['v56FeasibleStates'] for x in rows)},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
