from __future__ import annotations
import json,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,balanced_accuracy_score,recall_score
from sklearn.preprocessing import label_binarize
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_stack_v1.json'
MODEL=ROOT/'data/research/r4_v0/hourly/r4_management_stack_v1.joblib'
PORT=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross']
RESP=['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s']
MEM=['current_mode_age_s','events_5s','events_15s','transitions_15s']
FULL=PORT+RESP+MEM
CLASSES=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT']
def hgb(seed):return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=35,l2_regularization=1,max_iter=220,random_state=seed)
def binmet(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1]))}
def multimet(y,p):
 y=np.asarray(y);pred=np.asarray(CLASSES)[np.argmax(p,axis=1)];Y=label_binarize(y,classes=CLASSES);aps=[average_precision_score(Y[:,j],p[:,j]) for j in range(3)];rec=recall_score(y,pred,labels=CLASSES,average=None,zero_division=0);return {'n':int(len(y)),'macroAuc':float(roc_auc_score(y,p,labels=CLASSES,multi_class='ovr',average='macro')),'macroAp':float(np.mean(aps)),'logLoss':float(log_loss(y,p,labels=CLASSES)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'perClassRecall':{c:float(v) for c,v in zip(CLASSES,rec)},'trueRate':{c:float(np.mean(y==c)) for c in CLASSES},'predRate':{c:float(np.mean(pred==c)) for c in CLASSES}}
def main():
 d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan);d=d[(d.seconds_left>=60)&(d.seconds_left<=300)].dropna(subset=FULL).copy();d.market_id=d.market_id.astype(int);d=d.sort_values(['market_id','t']);ms=d.groupby('market_id').t.min().sort_values().index.astype(int).tolist();cut=min(240,len(ms)-40);trm=ms[:cut];tem=ms[cut:];tr=d[d.market_id.isin(trm)];te=d[d.market_id.isin(tem)]
 tr0=tr[tr.build_now==1].copy();te0=te[te.build_now==1].copy();m0=hgb(16001).fit(tr0[FULL],tr0.continue_weak_5s.astype(int));p0=m0.predict_proba(te0[FULL])[:,1]
 tr1=tr[(tr.build_now==1)&(tr.management_label_5s.notna())].copy();te1=te[(te.build_now==1)&(te.management_label_5s.notna())].copy();tr1['event5']=(tr1.management_label_5s!='OBSERVE_NO_EVENT').astype(int);me=hgb(16002).fit(tr1[FULL],tr1.event5);pe=me.predict_proba(te1[FULL])[:,1];rr=tr1[tr1.event5==1].copy();rr['route_continue']=(rr.management_label_5s=='CONTINUE_WEAK').astype(int);mr=hgb(16003).fit(rr[FULL],rr.route_continue);pc=mr.predict_proba(te1[FULL])[:,1];p1=np.column_stack([pe*pc,pe*(1-pc),1-pe])
 report={'version':'R4_MANAGEMENT_STACK_V1','researchOnly':True,'actionAuthority':False,'coverage':{'markets':len(ms),'trainMarkets':len(trm),'holdoutMarkets':len(tem),'trainMarketIds':trm,'holdoutMarketIds':tem,'phase':'60-300s'},'features':{'portfolio':PORT,'responsibility':RESP,'memory':MEM,'manager':FULL},'M0_CONTINUE_WEAK':{'architecture':'single continuation head; current BUILD only','holdout':binmet(te0.continue_weak_5s,p0)},'M1_HANDOFF_OBSERVE':{'architecture':'hierarchical EVENT-vs-OBSERVE then CONTINUE-vs-HANDOFF; full management state to both heads','holdout':multimet(te1.management_label_5s,p1)},'guards':['Corrected de-duplicated 300-market cache.','2026-08-16 special date excluded upstream.','No raw public signals; every runtime feature has an OUR owner-ledger/R3.1-compatible analogue.','Future Target events are teacher labels only.','No action authority, price/size/channel output, threshold sweep, winner or settlement.']}
 bundle={'version':'R4_MANAGEMENT_STACK_V1','actionAuthority':False,'phase':[60,300],'features':FULL,'M0_continue_weak_model':m0,'M1_event_model':me,'M1_route_continue_model':mr,'M1_classes':CLASSES,'notes':'Research-only management beliefs; R3.1 remains information-only. Runtime use requires separate HFT/PAPER shadow and exact OUR/R3.1 state mapping.'};joblib.dump(bundle,MODEL);report['modelPath']=str(MODEL.relative_to(ROOT)).replace('\\','/');OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
