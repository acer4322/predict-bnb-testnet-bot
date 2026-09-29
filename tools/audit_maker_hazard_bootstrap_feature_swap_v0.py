from pathlib import Path
import importlib.util,json,math,sys
import joblib,numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
OUR=OUT/'target_blind_maker_lifecycle_bootstrap_v0_states.csv';TGT=OUT/'target_general_maker_side_hazard_v1.csv';REPORT=OUT/'maker_hazard_bootstrap_feature_swap_v0_report.json'
UP=joblib.load(OUT/'target_general_maker_up_hazard_v1.joblib');DN=joblib.load(OUT/'target_general_maker_down_hazard_v1.joblib')
P=ROOT/'tools'/'train_target_general_maker_side_hazard_v1.py';sp=importlib.util.spec_from_file_location('mh_swap',P);m=importlib.util.module_from_spec(sp);assert sp and sp.loader;sys.modules[sp.name]=m;sp.loader.exec_module(m)
PORT=[x for x in m.CORE if x!='seconds_left'];BOOK=list(m.BOOK);LIFE=list(m.LIFE);ECON=list(m.ECON);PLACE=list(m.PLACE);ALL=list(dict.fromkeys(PORT+BOOK+LIFE+ECON+PLACE))
VAR={'OUR_ALL':[],'TARGET_PORTFOLIO':PORT,'TARGET_LIFECYCLE':LIFE,'TARGET_PLACE':PLACE,'TARGET_LIFE_PLACE':LIFE+PLACE,'TARGET_PORT_LIFE_PLACE':PORT+LIFE+PLACE,'TARGET_PORT_LIFE_ECON_PLACE':PORT+LIFE+ECON+PLACE,'TARGET_ALL':ALL}

def numeric(df,fs):return df.reindex(columns=fs).apply(pd.to_numeric,errors='coerce')
def bm(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);both=len(set(y.tolist()))==2
 return {'n':len(y),'positives':int(y.sum()),'positiveRate':float(y.mean()),'rocAuc':float(roc_auc_score(y,p)) if both else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum() else None}
def main():
 our=pd.read_csv(OUR) if OUR.exists() else pd.concat([pd.read_csv(OUT/f'target_blind_maker_lifecycle_bootstrap_v0_c{i}_states.csv') for i in range(6)],ignore_index=True); mids=set(our.target_market_id.astype(int).unique())
 use=list(dict.fromkeys(['market_id','market_end_ms','checkpoint_ms','label_up_next1s','label_down_next1s','seconds_left']+ALL))
 chunks=[]
 for c in pd.read_csv(TGT,usecols=use,chunksize=20000):
  q=c[c.market_id.astype(int).isin(mids)]
  if len(q):chunks.append(q)
 tgt=pd.concat(chunks,ignore_index=True).sort_values(['market_id','checkpoint_ms'])
 # nearest strict-past target state within 1.25s, per mapped target market
 parts=[]
 for tm,g in our.groupby('target_market_id'):
  t=tgt[tgt.market_id.astype(int)==int(tm)]
  if t.empty:continue
  a=g.sort_values('decision_ms').copy(); b=t.sort_values('checkpoint_ms').copy()
  z=pd.merge_asof(a,b,left_on='decision_ms',right_on='checkpoint_ms',direction='backward',tolerance=1250,suffixes=('_our','_target'))
  z=z[z.checkpoint_ms.notna()];parts.append(z)
 j=pd.concat(parts,ignore_index=True)
 rep={'reportVersion':'MAKER_HAZARD_BOOTSTRAP_FEATURE_SWAP_V0','researchOnly':True,'teacherOnlyOracle':True,'coverage':{'rows':len(j),'markets':int(j.target_market_id.nunique())},'variants':{}}
 for name,grp in VAR.items():
  raw=pd.DataFrame(index=j.index)
  for f in set(UP['features'])|set(DN['features']):
   oc=f'{f}_our';tc=f'{f}_target'
   if f in grp and tc in j:raw[f]=j[tc]
   elif oc in j:raw[f]=j[oc]
   elif f in j:raw[f]=j[f]
   elif tc in j:raw[f]=j[tc]
   else:raw[f]=math.nan
  pu=UP['model'].predict_proba(numeric(raw,list(UP['features'])))[:,1];pdn=DN['model'].predict_proba(numeric(raw,list(DN['features'])))[:,1]
  yu=pd.to_numeric(j.label_up_next1s,errors='coerce').fillna(0).astype(int);yd=pd.to_numeric(j.label_down_next1s,errors='coerce').fillna(0).astype(int)
  rep['variants'][name]={'swappedGroups':grp,'meanPUp':float(np.mean(pu)),'meanPDown':float(np.mean(pdn)),'meanIntentPerSecond':float(np.mean(pu+pdn)),'expectedPlacementsPer300s':float(np.mean(pu+pdn)*300),'UP':bm(yu,pu),'DOWN':bm(yd,pdn)}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
