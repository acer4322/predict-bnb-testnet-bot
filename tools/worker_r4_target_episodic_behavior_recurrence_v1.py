from pathlib import Path
import argparse,json
import numpy as np,pandas as pd

def signed(x,eps=1e-6):return 'POS' if x>eps else 'NEG' if x<-eps else 'FLAT'
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--ledger1-dir',required=True);ap.add_argument('--ledger2-dir',required=True);ap.add_argument('--out',required=True);args=ap.parse_args()
 d1=Path(args.ledger1_dir);d2=Path(args.ledger2_dir);out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
 key=['head','phase','target_label','floor_state','ownership_context']
 def load(d):
  x=pd.read_csv(d/'behavior_context_summary.csv');x=x[x.n>=10].copy();return x
 a,b=load(d1),load(d2)
 # one row per context with guard/final side by side in each stream
 def pivot(x,prefix):
  z=x.pivot_table(index=key,columns='split',values=['n','meanTargetProbDelta','netThresholdCorrections'],aggfunc='first').reset_index();z.columns=['__'.join([str(q) for q in c if str(q)!='']) if isinstance(c,tuple) else str(c) for c in z.columns];
  ren={c:(c if c in key else prefix+'__'+c) for c in z.columns};return z.rename(columns=ren)
 p1,p2=pivot(a,'S1'),pivot(b,'S2');m=p1.merge(p2,on=key,how='inner')
 req=['S1__meanTargetProbDelta__GUARD20','S1__meanTargetProbDelta__FINAL20','S2__meanTargetProbDelta__GUARD20','S2__meanTargetProbDelta__FINAL20']
 for c in req:
  if c not in m:m[c]=np.nan
 m=m.dropna(subset=req).copy()
 m['S1_guard_sign']=m[req[0]].map(signed);m['S1_final_sign']=m[req[1]].map(signed);m['S2_guard_sign']=m[req[2]].map(signed);m['S2_final_sign']=m[req[3]].map(signed)
 def cls(r):
  s=[r['S1_guard_sign'],r['S1_final_sign'],r['S2_guard_sign'],r['S2_final_sign']]
  if all(x=='POS' for x in s):return 'ROBUST_IMPROVEMENT'
  if all(x=='NEG' for x in s):return 'ROBUST_WORSENING'
  if r['S1_guard_sign']==r['S1_final_sign'] and r['S2_guard_sign']==r['S2_final_sign'] and r['S1_guard_sign']!=r['S2_guard_sign'] and 'FLAT' not in s:return 'STREAM_DIRECTION_REVERSAL'
  return 'MIXED'
 m['recurrenceClass']=m.apply(cls,axis=1);m['meanDelta4']=m[req].mean(axis=1);m['worstDelta4']=m[req].min(axis=1);m['bestDelta4']=m[req].max(axis=1)
 m.to_csv(out.with_suffix('.csv'),index=False)
 # phase-label recurrence includes hazard heads too.
 def loadp(d):return pd.read_csv(d/'behavior_phase_label_summary.csv')
 x1,x2=loadp(d1),loadp(d2);k2=['head','phase','target_label']
 def pv(x,prefix):
  z=x.pivot_table(index=k2,columns='split',values=['n','meanTargetProbDelta','netThresholdCorrections'],aggfunc='first').reset_index();z.columns=['__'.join([str(q) for q in c if str(q)!='']) if isinstance(c,tuple) else str(c) for c in z.columns];return z.rename(columns={c:(c if c in k2 else prefix+'__'+c) for c in z.columns})
 q=pv(x1,'S1').merge(pv(x2,'S2'),on=k2,how='inner');rq=['S1__meanTargetProbDelta__GUARD20','S1__meanTargetProbDelta__FINAL20','S2__meanTargetProbDelta__GUARD20','S2__meanTargetProbDelta__FINAL20'];q=q.dropna(subset=rq).copy()
 q['recurrenceClass']=q.apply(lambda r:'ROBUST_IMPROVEMENT' if all(r[c]>1e-6 for c in rq) else 'ROBUST_WORSENING' if all(r[c]<-1e-6 for c in rq) else 'STREAM_DIRECTION_REVERSAL' if ((r[rq[0]]>0 and r[rq[1]]>0 and r[rq[2]]<0 and r[rq[3]]<0) or (r[rq[0]]<0 and r[rq[1]]<0 and r[rq[2]]>0 and r[rq[3]]>0)) else 'MIXED',axis=1);q['meanDelta4']=q[rq].mean(axis=1);q.to_csv(out.with_name(out.stem+'_phase_label.csv'),index=False)
 def records(df,c,n=20,asc=False):return df[df.recurrenceClass==c].sort_values('meanDelta4',ascending=asc).head(n).replace({np.nan:None}).to_dict('records')
 rep={'version':'R4_TARGET_EPISODIC_BEHAVIOR_RECURRENCE_V1','researchOnly':True,'actionAuthority':False,'contextRows':int(len(m)),'phaseLabelRows':int(len(q)),'contextClassCounts':m.recurrenceClass.value_counts().to_dict(),'phaseLabelClassCounts':q.recurrenceClass.value_counts().to_dict(),'robustImprovingContexts':records(m,'ROBUST_IMPROVEMENT',20,False),'robustWorseningContexts':records(m,'ROBUST_WORSENING',20,True),'directionReversals':records(m,'STREAM_DIRECTION_REVERSAL',20,False),'robustImprovingPhaseLabels':records(q,'ROBUST_IMPROVEMENT',20,False),'robustWorseningPhaseLabels':records(q,'ROBUST_WORSENING',20,True),'phaseLabelDirectionReversals':records(q,'STREAM_DIRECTION_REVERSAL',20,False),'interpretationGuard':'Recurring sign across guard+final in two chronology streams is a stronger hypothesis signal, not causal proof. Contexts absent or low-support in either stream are excluded.'}
 out.write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'contextClassCounts':rep['contextClassCounts'],'phaseLabelClassCounts':rep['phaseLabelClassCounts'],'robustImprovingContexts':rep['robustImprovingContexts'][:8],'robustWorseningContexts':rep['robustWorseningContexts'][:8],'directionReversals':rep['directionReversals'][:8],'phaseRobustWorsening':rep['robustWorseningPhaseLabels'][:8]},indent=2,ensure_ascii=False),flush=True)
if __name__=='__main__':main()