from __future__ import annotations
import argparse,json,os,sys,tempfile,zipfile,shutil
from pathlib import Path
import numpy as np,joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_lane_g_multi_action_exact_fork_v1b as ma
from tools import run_lane_g_keep_vs_ordinary_reexpand_h100_exact_fork_v1 as kv
from tools import train_lane_g_r264_execution_world_v1 as wm
ACTIONS=('KEEP_REPAIR','ORDINARY_REEXPAND'); EPS=1e-9

def payoff(u,d,c):
 b=max(u,d)-c; f=min(u,d)-c; return {'upQty':u,'downQty':d,'cost':c,'floor':f,'best':b,'gap':b-f}
def apply(base,side,price,qty):
 u=float(base['upQty']); d=float(base['downQty']); c=float(base['cost']);
 if side=='UP':u+=qty
 else:d+=qty
 c+=price*qty; return payoff(u,d,c)
def delta(p,b):return {'dFloor':float(p['floor'])-float(b['floor']),'dBest':float(p['best'])-float(b['best']),'dGap':float(p['gap'])-float(b['gap'])}

def predict_carrier(bundle,row):
 x=wm.X([row],True); m=bundle['models']; p=float(m['fillFirst'].predict_proba(x)[0,1]); lag=max(.05,float(np.expm1(m['eventLagLog'].predict(x)[0]))); q=max(0.,min(float(row['qty']),float(m['firstFillQty'].predict(x)[0]))); return {'pFillFirst':p,'eventLagSec':lag,'conditionalFirstFillQty':q,'fillRate':p/lag,'terminalRate':(1.-p)/lag}

class CaptureSim(ma.MultiActionExactForkSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.involvedRows={};self._end_ms=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
 def _row(self,t,key,source):
  o=self.orders.get(str(key));
  if not o:return None
  side=str(o['side']); role=str(self.key_role.get(str(key)) or 'SATELLITE_REPAIR'); price=float(o['price']); qty=float(self._remaining(str(key))); return wm.TraceSim._state_row(self,int(t),side,role,price,qty,'PASSIVE',str(key),source)
 def _arm_fork(self,t,branch_key=None,pre_state=None):
  super()._arm_fork(t,branch_key,pre_state); sk=str(self.spec['siblingKey']); r=self._row(t,sk,'EXACT_KEEP_REPAIR_SIBLING');
  if r:self.involvedRows[sk]=r
  if branch_key:
   r=self._row(t,str(branch_key),'EXACT_ORDINARY_REEXPAND');
   if r:self.involvedRows[str(branch_key)]=r

def engine(base,rows,bundle,branch_key):
 comps=[]; total=0.
 for key,row in rows.items():
  pr=predict_carrier(bundle,row)
  for kind,rate in [('FILL',pr['fillRate']),('TERMINAL',pr['terminalRate'])]:
   rate=max(0.,float(rate)); total+=rate; comps.append([key,kind,rate,row,pr])
 if total<=EPS:return None
 mix=[]; exp=np.zeros(3,float); class_prob={'REEXPAND_FIRST_FILL':0.,'REPAIR_FIRST_FILL':0.,'REEXPAND_TERMINAL_FIRST':0.,'REPAIR_TERMINAL_FIRST':0.}
 for key,kind,rate,row,pr in comps:
  prob=rate/total
  if kind=='FILL':pd=delta(apply(base,str(row['side']),float(row['price']),float(pr['conditionalFirstFillQty'])),base)
  else:pd={'dFloor':0.,'dBest':0.,'dGap':0.}
  exp+=prob*np.asarray([pd['dFloor'],pd['dBest'],pd['dGap']],float)
  isbranch=bool(branch_key and str(key)==str(branch_key)); cls=('REEXPAND_' if isbranch else 'REPAIR_')+('FIRST_FILL' if kind=='FILL' else 'TERMINAL_FIRST'); class_prob[cls]=class_prob.get(cls,0.)+prob
  mix.append({'key':key,'kind':kind,'probability':prob,'localDelta':pd,'pred':pr})
 return {'expected':{'dFloor':float(exp[0]),'dBest':float(exp[1]),'dGap':float(exp[2])},'classProb':class_prob,'predictedClass':max(class_prob,key=class_prob.get),'mixture':mix,'predictedEventLagSec':1./total}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--spec',required=True);ap.add_argument('--model',required=True);ap.add_argument('--market-ids',default='');ap.add_argument('--validation-ids',default='');ap.add_argument('--output',required=True);a=ap.parse_args()
 sd=json.loads(Path(a.spec).read_text(encoding='utf-8')); specs={int(k):v for k,v in sd['markets'].items()}; mids=[int(x) for x in a.market_ids.split(',') if x.strip()] if a.market_ids else sorted(specs); vals=set(int(x) for x in a.validation_ids.split(',') if x.strip());
 for m in mids:ma.FROZEN[m]=specs[m]
 bundle=joblib.load(a.model); tmp=Path(tempfile.mkdtemp(prefix='lane_g_keep_ord_world_'))
 try:
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']};
   for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
  rows=[]
  for m in mids:
   for action in ACTIONS:
    s=CaptureSim(tmp/f'{m}.json.xz',m,action,1,4)
    try:r=s.run_exact(co[m]['winner']); wr=dict(s.involvedRows)
    finally:s.close()
    base=(r.get('trigger') or {}).get('payoff'); eng=engine(base,wr,bundle,r.get('branchKey')) if base and wr else None; actual=kv.classify(r); rec={'marketId':m,'split':'VALIDATION' if m in vals else 'TRAIN','action':action,'actualClass':actual,'prediction':eng,'rawCorrect':bool(r['correct']),'triggerParityErrors':r['triggerParityErrors']}; rows.append(rec); print(json.dumps({'marketId':m,'split':rec['split'],'action':action,'actual':actual,'pred':(eng or {}).get('predictedClass'),'pReexpandFill':((eng or {}).get('classProb') or {}).get('REEXPAND_FIRST_FILL'),'expected':(eng or {}).get('expected')},ensure_ascii=False),flush=True)
  def summarize(sub):
   rr=[x for x in rows if x['split']==sub and x['action']=='ORDINARY_REEXPAND'];
   if not rr:return None
   y=np.array([1. if x['actualClass']=='REEXPAND_FIRST_FILL' else 0. for x in rr]); p=np.array([float(x['prediction']['classProb'].get('REEXPAND_FIRST_FILL',0.)) for x in rr]); pred=[x['prediction']['predictedClass'] for x in rr]; act=[x['actualClass'] for x in rr]; return {'n':len(rr),'reexpandFirstRate':float(y.mean()),'brierReexpandFirst':float(np.mean((p-y)**2)),'topClassAccuracy':float(np.mean([a==b for a,b in zip(act,pred)])),'rows':[{'marketId':x['marketId'],'actual':x['actualClass'],'predicted':x['prediction']['predictedClass'],'pReexpandFirst':x['prediction']['classProb'].get('REEXPAND_FIRST_FILL')} for x in rr]}
  out={'version':'LANE_G_KEEP_VS_ORDINARY_EXECUTION_WORLD_V1_20260907','researchOnly':True,'runtimeAuthority':False,'rows':rows,'trainSummary':summarize('TRAIN'),'validationSummary':summarize('VALIDATION'),'gates':{'allCorrect':all(x['rawCorrect'] for x in rows),'triggerParityClean':all(not x['triggerParityErrors'] for x in rows),'allPredictionsPresent':all(x['prediction'] is not None for x in rows)},'boundary':['existing H100 execution-world model only','no refit','validation ids frozen from chronological 70/30 model split','exact post-submit state','structural-event prediction only','terminal PnL not used for prediction','consumed only/no fresh/no 8781']}; op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'trainSummary':out['trainSummary'],'validationSummary':out['validationSummary'],'gates':out['gates']},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
