from __future__ import annotations

import importlib.util, json, sys
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'train_target_maker_taker_coordination_big_v1.py'
spec=importlib.util.spec_from_file_location('coord_big_v1',P); mod=importlib.util.module_from_spec(spec); assert spec and spec.loader; sys.modules[spec.name]=mod; spec.loader.exec_module(mod)
OUT=mod.OUT
CONTRACT=OUT/'forward_contract_v1.json'


def numeric(df,features): return df[features].apply(pd.to_numeric,errors='coerce')

def main()->int:
    OUT.mkdir(parents=True,exist_ok=True)
    hz=pd.read_csv(mod.HAZARD_CSV); tk=pd.read_csv(mod.TAKER_CSV); hd=pd.read_csv(mod.HANDOFF_CSV)
    artifacts={}
    feats=mod.FEATURE_SETS['FULL']
    for h in (1,3,5):
        label=f'label_taker_{h}s'; m=HistGradientBoostingClassifier(max_iter=350,learning_rate=.06,max_leaf_nodes=31,l2_regularization=1.0,early_stopping=True,validation_fraction=.12,n_iter_no_change=30,random_state=20260819+h)
        m.fit(numeric(hz,feats),hz[label].astype(int)); p=OUT/f'frozen_hazard_{h}s_full.joblib'; joblib.dump({'version':mod.VERSION,'frozen':True,'task':f'hazard_{h}s','featureSet':'FULL','features':feats,'model':m},p); artifacts[f'hazard_{h}s']=str(p)
    for task,df,label,fsets in [('SIDE',tk,'label_side',mod.FEATURE_SETS),('EFFECT',tk,'label_effect',mod.FEATURE_SETS),('HANDOFF',hd,'label_handoff',mod.HANDOFF_FEATURE_SETS)]:
        fs=fsets['FULL']; m=mod.ebm(fs); m.fit(numeric(df,fs),df[label].astype(str).tolist()); p=OUT/f'frozen_{task.lower()}_full.joblib'; joblib.dump({'version':mod.VERSION,'frozen':True,'task':task,'featureSet':'FULL','features':fs,'classes':[str(x) for x in m.classes_],'model':m},p); artifacts[task.lower()]=str(p)
    reports={}
    for name in ['hazard_core','hazard_full','effect_core','effect_full','side_core','side_full','handoff_core','handoff_full']:
        p=OUT/f'report_{name}.json'
        if p.exists(): reports[name]=json.loads(p.read_text(encoding='utf-8'))
    cutoff=max(int(hz.market_end_ms.max()),int(tk.market_end_ms.max()),int(hd.market_end_ms.max()))
    checkpoint=max(int(hz.checkpoint_ms.max()),int(tk.checkpoint_ms.max()),int(hd.checkpoint_ms.max()))
    contract={'reportVersion':mod.VERSION,'researchOnly':True,'frozenAtMarketEndMs':cutoff,'frozenAtCheckpointMs':checkpoint,'runtimeTargetDataAllowed':False,'artifacts':artifacts,'trainingRows':{'hazard':len(hz),'taker':len(tk),'handoff':len(hd)},'trainingMarkets':{'hazard':int(hz.market_id.nunique()),'taker':int(tk.market_id.nunique()),'handoff':int(hd.market_id.nunique())},'historicalReports':{k:{'task':v.get('task'),'featureSet':v.get('featureSet'),'validation':v.get('validation') or v.get('horizons'),'test':v.get('test')} for k,v in reports.items()},'forwardRule':'Only markets with market_end_ms strictly greater than frozenAtMarketEndMs are prospective fresh-forward. Never retrain/tune these artifacts from a market before reporting its forward result.','teacherBoundary':'Historical Target actions/effects are labels only. Runtime inference must use public book plus equivalent own portfolio/lifecycle/current intervention quantities.'}
    CONTRACT.write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(contract,ensure_ascii=False,indent=2)); return 0

if __name__=='__main__': raise SystemExit(main())
