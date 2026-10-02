from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,balanced_accuracy_score,recall_score
from sklearn.preprocessing import label_binarize
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_router_v0_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_handoff_observe_v0.json'
ROWS=ROOT/'data/research/r4_v0/hourly/r4_management_handoff_observe_v0_rows.csv'
PORT=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','placement_readiness_native_5s']
RESP=['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s']
MEM=['current_mode_age_s','events_5s','events_15s','transitions_15s']
CLASSES=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT']
def hgb(seed):return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=20,l2_regularization=1,max_iter=220,random_state=seed)
def metrics(y,p,classes):
 y=np.asarray(y); pred=np.asarray(classes)[np.argmax(p,axis=1)]; Y=label_binarize(y,classes=classes); aps=[]
 for j,c in enumerate(classes): aps.append(float(average_precision_score(Y[:,j],p[:,j])) if Y[:,j].sum()>0 else None)
 try: auc=float(roc_auc_score(y,p,labels=classes,multi_class='ovr',average='macro'))
 except: auc=None
 rec=recall_score(y,pred,labels=classes,average=None,zero_division=0)
 return {'n':int(len(y)),'macroAuc':auc,'macroAp':float(np.mean([x for x in aps if x is not None])),'logLoss':float(log_loss(y,p,labels=classes)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'perClassRecall':{c:float(v) for c,v in zip(classes,rec)},'predRate':{c:float(np.mean(pred==c)) for c in classes},'trueRate':{c:float(np.mean(y==c)) for c in classes}}
def summ(bs,k):
 x=[b[k] for b in bs if k in b];return {'meanMacroAuc':float(np.mean([q['macroAuc'] for q in x])),'worstMacroAuc':float(np.min([q['macroAuc'] for q in x])),'stdMacroAuc':float(np.std([q['macroAuc'] for q in x])),'meanMacroAp':float(np.mean([q['macroAp'] for q in x])),'meanLogLoss':float(np.mean([q['logLoss'] for q in x])),'meanBalancedAccuracy':float(np.mean([q['balancedAccuracy'] for q in x])),'meanHandoffRecall':float(np.mean([q['perClassRecall']['HANDOFF_ALLOW'] for q in x])),'worstHandoffRecall':float(np.min([q['perClassRecall']['HANDOFF_ALLOW'] for q in x]))}
def main():
 a=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan).dropna(subset=PORT+RESP+MEM+['market_id','t','build_now']).copy();a.market_id=a.market_id.astype(int);a=a[(a.seconds_left>=60)&(a.seconds_left<=300)].sort_values(['market_id','t'])
 rows=[]
 for mid,g in a.groupby('market_id'):
  g=g.sort_values('t').reset_index(drop=False);ts=g.t.to_numpy(np.int64);modes=g.build_now.to_numpy(int)
  for j in range(len(g)):
   if modes[j]!=1: continue
   r=g.iloc[j].to_dict()
   if j+1<len(g) and ts[j+1]-ts[j]<=5000: lab='CONTINUE_WEAK' if modes[j+1]==1 else 'HANDOFF_ALLOW'
   else: lab='OBSERVE_NO_EVENT'
   r['management_label_5s']=lab;rows.append(r)
 d=pd.DataFrame(rows).sort_values(['market_id','t']);d.to_csv(ROWS,index=False);ms=d.groupby('market_id').t.min().sort_values().index.astype(int).tolist();initial=18;rem=len(ms)-initial;sizes=[rem//3]*3
 for i in range(rem%3):sizes[i]+=1
 cur=initial;blocks=[]
 specs=[('PORTFOLIO_ONLY',PORT),('PLUS_RESP',PORT+RESP),('PLUS_RESP_MEMORY',PORT+RESP+MEM)]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=d[d.market_id.isin(trm)];te=d[d.market_id.isin(tem)];b={'block':bi,'trainMarkets':len(trm),'testMarkets':tem,'rows':int(len(te)),'classCounts':{c:int((te.management_label_5s==c).sum()) for c in CLASSES}}
  if set(CLASSES).issubset(set(tr.management_label_5s.unique())) and set(CLASSES).issubset(set(te.management_label_5s.unique())):
   for j,(name,feats) in enumerate(specs):
    m=hgb(8000+bi*20+j).fit(tr[feats],tr.management_label_5s); pp=m.predict_proba(te[feats]); order=list(m.classes_);p=np.column_stack([pp[:,order.index(c)] for c in CLASSES]);b[name]=metrics(te.management_label_5s,p,CLASSES)
  blocks.append(b)
 summary={k:summ(blocks,k) for k,_ in specs if any(k in b for b in blocks)}
 if 'PORTFOLIO_ONLY' in summary and 'PLUS_RESP_MEMORY' in summary:
  x=summary['PORTFOLIO_ONLY'];y=summary['PLUS_RESP_MEMORY'];summary['MANAGEMENT_DELTA']={'meanMacroAuc':y['meanMacroAuc']-x['meanMacroAuc'],'worstMacroAuc':y['worstMacroAuc']-x['worstMacroAuc'],'meanMacroAp':y['meanMacroAp']-x['meanMacroAp'],'logLossImprovement':x['meanLogLoss']-y['meanLogLoss'],'meanBalancedAccuracy':y['meanBalancedAccuracy']-x['meanBalancedAccuracy'],'meanHandoffRecall':y['meanHandoffRecall']-x['meanHandoffRecall'],'worstHandoffRecall':y['worstHandoffRecall']-x['worstHandoffRecall']}
 art={'version':'R4_MANAGEMENT_HANDOFF_OBSERVE_V0','researchOnly':True,'actionAuthority':False,'curriculum':'M1_HANDOFF_OBSERVE','question':'Conditional on current BUILD responsibility in 60-300s, is the next management event within 5s CONTINUE_WEAK, HANDOFF_TO_ALLOW, or no new event/OBSERVE?','coverage':{'markets':int(d.market_id.nunique()),'rows':int(len(d)),'classCounts':{c:int((d.management_label_5s==c).sum()) for c in CLASSES}},'features':{'portfolio':PORT,'responsibility':RESP,'memory':MEM},'summary':summary,'blocks':blocks,'rowsArtifact':str(ROWS.relative_to(ROOT)).replace('\\','/'),'guards':['OBSERVE_NO_EVENT is observational inactivity, not proof of private Target termination.','Future next event is teacher label only.','No order price/size/channel action target, no threshold sweep, no winner/settlement.','R3.1 remains information-only; runtime responsibility/memory analogues are read-only inputs to Manager.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'summary':summary,'blocks':blocks},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
