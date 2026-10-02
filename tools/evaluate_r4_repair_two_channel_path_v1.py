from __future__ import annotations
import json,math,sys
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,recall_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.evaluate_r4_repair_exact_pathstate_ab_v1 import load_teacher,load_path,enriched_values,BASE_FEATURES,fnum,impute
P=ROOT/'data/research/r4_v0/p0_provenance_v1';OUT=P/'r4_repair_two_channel_path_v1.json'
CH=[ROOT/'data/research/lan_worker_returns/r4-repair-quote-challenge10-b1-v1/teacher.json',ROOT/'data/research/lan_worker_returns/r4-repair-quote-challenge10-b2-v1/teacher.json']
GROUPS={
 'TOPOLOGY':['hasRepairPath','hasDominantPath','repairRemainingUnits','dominantRemainingUnits'],
 'QUOTE':['repairBestQuoteOffsetTicks','dominantBestQuoteOffsetTicks'],
 'AGE':['repairMeanOrderAgeS','dominantMeanOrderAgeS'],
 'DEPLETION':['logRepairDepletion','logDominantDepletion']}

def channel_feats(s):
 ra=fnum(s.get('repairSideActiveCount'));da=fnum(s.get('dominantSideActiveCount'));rr=fnum(s.get('repairSideRemainingQty'));dr=fnum(s.get('dominantSideRemainingQty'));ro=fnum(s.get('repairBestQuoteOffsetTicks'));do=fnum(s.get('dominantBestQuoteOffsetTicks'));rage=fnum(s.get('repairMeanOrderAgeMs'));dage=fnum(s.get('dominantMeanOrderAgeMs'));rd=fnum(s.get('repairMeanDepletionRatio'));dd=fnum(s.get('dominantMeanDepletionRatio'))
 return {'hasRepairPath':float(ra>0) if math.isfinite(ra) else math.nan,'hasDominantPath':float(da>0) if math.isfinite(da) else math.nan,'repairRemainingUnits':rr/18 if math.isfinite(rr) else math.nan,'dominantRemainingUnits':dr/18 if math.isfinite(dr) else math.nan,'repairBestQuoteOffsetTicks':ro,'dominantBestQuoteOffsetTicks':do,'repairMeanOrderAgeS':rage/1000 if math.isfinite(rage) else math.nan,'dominantMeanOrderAgeS':dage/1000 if math.isfinite(dage) else math.nan,'logRepairDepletion':math.log1p(max(0,rd)) if math.isfinite(rd) else math.nan,'logDominantDepletion':math.log1p(max(0,dd)) if math.isfinite(dd) else math.nan}

def rows():
 out=[];t=load_teacher();p=load_path()
 for mid in sorted(set(t)&set(p)):
  c=str(t[mid].get('branchClass'))
  if c not in {'PARETO_BENEFICIAL','PARETO_HARMFUL','TRADEOFF','NO_EFFECT'}:continue
  base=enriched_values(t[mid],p[mid],'EXACT_STATE')
  s=((p[mid].get('pathState') or {}).get('summary') or {});v={**base,**channel_feats(s)};out.append({'marketId':mid,'class':c,'v':v,'source':'DEV39'})
 for fp in CH:
  o=json.loads(fp.read_text(encoding='utf-8'))
  for r in o.get('rows',[]):
   c=str(r.get('branchClass'))
   if c not in {'PARETO_BENEFICIAL','PARETO_HARMFUL','TRADEOFF','NO_EFFECT'}:continue
   forced=((r.get('counterfactual') or {}).get('forced') or []);s=((forced[0].get('activeOrderPathState') or {}).get('summary') or {}) if forced else {};base=enriched_values({'candidate':r.get('candidate') or {}},{'pathState':{'summary':s}},'EXACT_STATE');v={**base,**channel_feats(s)};out.append({'marketId':int(r['marketId']),'class':c,'v':v,'source':'CHALLENGE10_CONSUMED'})
 return out

def loo(rows,features):
 X=np.asarray([[r['v'].get(f,math.nan) for f in features] for r in rows],float);classes=[r['class'] for r in rows];y=np.asarray([1 if c=='PARETO_BENEFICIAL' else 0 for c in classes],int);pr=np.full(len(rows),np.nan)
 for i in range(len(rows)):
  tridx=[j for j,c in enumerate(classes) if j!=i and c in {'PARETO_BENEFICIAL','PARETO_HARMFUL'}] if classes[i] in {'PARETO_BENEFICIAL','PARETO_HARMFUL'} else [j for j,c in enumerate(classes) if c in {'PARETO_BENEFICIAL','PARETO_HARMFUL'}]
  tr,te=impute(X[tridx],X[i:i+1]);m=make_pipeline(StandardScaler(),LogisticRegression(C=.5,class_weight='balanced',max_iter=2000,random_state=20260830));m.fit(tr,y[tridx]);pr[i]=m.predict_proba(te)[0,1]
 return y,classes,pr

def met(y,c,p):
 bh=np.asarray([x in {'PARETO_BENEFICIAL','PARETO_HARMFUL'} for x in c]);pred=(p>=.5).astype(int);app=p>=.65;veto=p<=.35;benef=np.asarray([x=='PARETO_BENEFICIAL' for x in c]);harm=np.asarray([x=='PARETO_HARMFUL' for x in c]);other=~bh
 def rate(n,d):return float(n/d) if d else math.nan
 return {'bhN':int(bh.sum()),'auc':float(roc_auc_score(y[bh],p[bh])),'balancedAccuracyAt050':float(balanced_accuracy_score(y[bh],pred[bh])),'beneficialRecallAt050':float(recall_score(y[bh],pred[bh],pos_label=1)),'harmfulRecallAt050':float(recall_score(y[bh],pred[bh],pos_label=0)),'beneficialOpportunityRetention':rate(int((app&benef).sum()),int(benef.sum())),'harmfulVetoRecall':rate(int((veto&harm).sum()),int(harm.sum())),'approveHarmfulRate':rate(int((app&harm).sum()),int(app.sum())),'harmfulFalseApproveCount':int((app&harm).sum()),'beneficialFalseVetoCount':int((veto&benef).sum()),'otherExtremeDecisionRate':rate(int(((app|veto)&other).sum()),int(other.sum()))}
def main():
 R=rows();sets={'EXACT_STATE':BASE_FEATURES};cur=list(BASE_FEATURES)
 for name in ['TOPOLOGY','QUOTE','AGE','DEPLETION']:
  cur=cur+GROUPS[name];sets['EXACT_STATE_PLUS_'+'_'.join([x for x in ['TOPOLOGY','QUOTE','AGE','DEPLETION'] if all(f in cur for f in GROUPS[x])])]=list(cur)
 res={}
 for name,f in sets.items():
  y,c,p=loo(R,f);res[name]={'featureNames':f,'metrics':met(y,c,p)}
 rep={'version':'R4_REPAIR_TWO_CHANNEL_PATH_V1','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'marketCount':len(R),'classCounts':dict(Counter(r['class'] for r in R)),'sourceCounts':dict(Counter(r['source'] for r in R)),'representations':res,'interpretationBoundary':'Challenge10 is now consumed development. Sequential group additions are representation anatomy only, not promotion or threshold tuning.'};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'marketCount':rep['marketCount'],'classCounts':rep['classCounts'],'representations':res},indent=2))
if __name__=='__main__':main()
