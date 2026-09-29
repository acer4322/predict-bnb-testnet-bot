from pathlib import Path
import importlib.util,json,sys
import joblib,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1';DATA=OUT/'target_general_maker_side_hazard_v1.csv';REPORT=OUT/'target_general_maker_core_book_v1_report.json'
P=ROOT/'tools'/'train_target_general_maker_side_hazard_v1.py';sp=importlib.util.spec_from_file_location('gmcb',P);m=importlib.util.module_from_spec(sp);assert sp and sp.loader;sys.modules[sp.name]=m;sp.loader.exec_module(m)
FS=list(m.CORE)+list(m.BOOK)

def num(d):return d[FS].apply(pd.to_numeric,errors='coerce')
def metric(y,p):
 return {'n':len(y),'positives':int(y.sum()),'positiveRate':float(y.mean()),'rocAuc':float(roc_auc_score(y,p)),'averagePrecision':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def main():
 use=list(dict.fromkeys(['market_id','market_end_ms','label_up_next1s','label_down_next1s']+FS));d=pd.read_csv(DATA,usecols=use);s=m.coord.split_markets(d);rep={'reportVersion':'TARGET_GENERAL_MAKER_CORE_BOOK_V1','featureSet':'CORE_BOOK','splitMarkets':{k:len(v) for k,v in s.items()},'features':FS,'sides':{}}
 for side,label,seed in [('UP','label_up_next1s',20260830),('DOWN','label_down_next1s',20260831)]:
  tr=d.market_id.astype(int).isin(s['train']);va=d.market_id.astype(int).isin(s['validation']);te=d.market_id.astype(int).isin(s['test']);model=HistGradientBoostingClassifier(max_iter=350,learning_rate=.06,max_leaf_nodes=31,l2_regularization=1.0,early_stopping=True,validation_fraction=.12,n_iter_no_change=30,random_state=seed);model.fit(num(d.loc[tr]),d.loc[tr,label].astype(int));art=OUT/f'target_general_maker_{side.lower()}_core_book_v1.joblib';joblib.dump({'version':'TARGET_GENERAL_MAKER_CORE_BOOK_V1','side':side,'features':FS,'model':model},art);rep['sides'][side]={'validation':metric(d.loc[va,label].astype(int),model.predict_proba(num(d.loc[va]))[:,1]),'test':metric(d.loc[te,label].astype(int),model.predict_proba(num(d.loc[te]))[:,1]),'artifact':str(art)}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
