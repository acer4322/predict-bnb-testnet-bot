from __future__ import annotations
import json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
FILES={'train':'r2_residual_multicheckpoint_train15_v0.json','validation':'r2_residual_multicheckpoint_validation15_v0.json','forward':'r2_residual_multicheckpoint_forward20_v0.json'}
SIGS=['directionScore','spotReturn1sBps','spotReturn3sBps','spotQueueImbalance','spotTakerImbalance1s','futuresReturn1sBps','futuresReturn3sBps','futuresQueueImbalance','futuresTakerImbalance1s']

def finite(x):
 try:return x is not None and math.isfinite(float(x))
 except:return False

def med(xs):
 xs=sorted(float(x) for x in xs if finite(x));
 if not xs:return None
 n=len(xs); return xs[n//2] if n%2 else (xs[n//2-1]+xs[n//2])/2

def main():
 rep={'version':'R2_RESIDUAL_ALPHA_ALIGNMENT_V0','researchOnly':True,'runtimePolicyAuthority':False,'definition':'All alpha signals are signed toward the currently surplus side. Zero is the preregistered natural sign boundary; no threshold sweep. Outcome is HftBacktest execution-local OPPOSITE_PRIORITY value = -deltaTargetErrorArea. PnL/winner not used.','splits':{}}
 for sp,fn in FILES.items():
  d=json.load(open(D/fn,encoding='utf-8')); rows=[]
  for r in d['rowsData']:
   f=r['features']; ss=-1.0 if float(f.get('recoverySideIsUp') or 0)>0.5 else 1.0
   z={'value':float(r['value']),'improves':int(r['improves']),'marketId':r['marketId'],'delay':r['candidateDelayMs']}
   for s in SIGS:
    z[s]=float(f[s])*ss if finite(f.get(s)) else None
   rows.append(z)
  out={'n':len(rows),'positiveRate':sum(x['improves'] for x in rows)/len(rows),'signals':{}}
  for s in SIGS:
   avail=[x for x in rows if finite(x[s])]
   pos=[x for x in avail if x[s]>0]; non=[x for x in avail if x[s]<=0]
   def g(a):
    return {'n':len(a),'interventionImproveRate':sum(x['improves'] for x in a)/len(a) if a else None,'meanExecutionValue':sum(x['value'] for x in a)/len(a) if a else None,'medianExecutionValue':med([x['value'] for x in a]),'medianAlignedSignal':med([x[s] for x in a])}
   out['signals'][s]={'supportsSurplus':g(pos),'notSupportsSurplus':g(non),'deltaImproveRate':(g(pos)['interventionImproveRate']-g(non)['interventionImproveRate']) if pos and non else None,'deltaMeanValue':(g(pos)['meanExecutionValue']-g(non)['meanExecutionValue']) if pos and non else None}
  # fixed, literature-motivated composite: equal sign vote across public price/flow signals; no fitted weights.
  for x in rows:
   vals=[x[s] for s in SIGS if finite(x[s])]
   x['vote']=sum(1 if v>0 else -1 if v<0 else 0 for v in vals)/len(vals) if vals else None
  vp=[x for x in rows if finite(x['vote']) and x['vote']>0]; vn=[x for x in rows if finite(x['vote']) and x['vote']<=0]
  def gv(a):return {'n':len(a),'interventionImproveRate':sum(x['improves'] for x in a)/len(a) if a else None,'meanExecutionValue':sum(x['value'] for x in a)/len(a) if a else None,'medianExecutionValue':med([x['value'] for x in a]),'medianVote':med([x['vote'] for x in a])}
  out['equalSignVote']={'supportsSurplus':gv(vp),'notSupportsSurplus':gv(vn),'deltaImproveRate':gv(vp)['interventionImproveRate']-gv(vn)['interventionImproveRate'] if vp and vn else None,'deltaMeanValue':gv(vp)['meanExecutionValue']-gv(vn)['meanExecutionValue'] if vp and vn else None}
  rep['splits'][sp]=out
 (D/'r2_residual_alpha_alignment_v0_report.json').write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
