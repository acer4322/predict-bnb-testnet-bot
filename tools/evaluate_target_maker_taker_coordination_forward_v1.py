from __future__ import annotations

import importlib.util, json, math, sqlite3, sys, time, zlib, bisect
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, average_precision_score, balanced_accuracy_score, f1_score, log_loss, roc_auc_score

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'train_target_maker_taker_coordination_big_v1.py'
spec=importlib.util.spec_from_file_location('coord_big_v1_eval',P); mod=importlib.util.module_from_spec(spec); assert spec and spec.loader; sys.modules[spec.name]=mod; spec.loader.exec_module(mod)
OUT=mod.OUT; CONTRACT=OUT/'forward_contract_v1.json'; REPORT=OUT/'forward_evaluation_v1.json'
HZ_OUT=OUT/'forward_hazard_states_v1.csv'; TK_OUT=OUT/'forward_taker_states_v1.csv'; HD_OUT=OUT/'forward_handoff_states_v1.csv'


def build_fresh(cutoff:int, finalized_end:int):
    b,t=mod.ro(mod.BOOK_DB),mod.ro(mod.TARGET_DB)
    try:
        meta=mod.load_market_meta(b); markets={m for m,x in meta.items() if cutoff<int(x['window_end_ms'])<=finalized_end}
        events=mod.load_events(t,markets); takers=mod.load_taker_parents(t,markets); makers=mod.load_anchored_maker_parents(b,markets)
        hz=[];tk=[];hd=[]
        for m in sorted(markets,key=lambda x:int(meta[x]['window_end_ms'])):
            if not events.get(m): continue
            mend=int(meta[m]['window_end_ms']); mstart=mend-300000; ev=events[m]; tp=takers.get(m,[]); mp=makers.get(m,[]); tt=[int(p['first_event_ms']) for p in tp]
            candidates=[{'kind':'HAZ','cp':cp} for cp in range(mstart+500,mend-4500,1000)]
            candidates += [{'kind':'TAKER','cp':int(p['first_event_ms'])-1,'p':p} for p in tp if mstart+1000<=int(p['first_event_ms'])<=mend-5000]
            candidates += [{'kind':'HAND','cp':int(p['last_event_ms'])+1,'p':p} for p in tp if mstart+1000<=int(p['last_event_ms'])<=mend-5000]
            candidates.sort(key=lambda x:(x['cp'],x['kind']))
            inv=mod.Inventory();ei=0;ci=0;state={'bids':{},'asks':{}};last=None
            for u in b.execute("select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id",(m,)):
                ut=int(u['source_timestamp_ms'])
                while ci<len(candidates) and int(candidates[ci]['cp'])<ut:
                    c=candidates[ci];cp=int(c['cp'])
                    while ei<len(ev) and int(ev[ei]['event_ms'])<=cp: inv.apply(ev[ei]);ei+=1
                    age=cp-last if last is not None else 10**9
                    if 0<=age<=2000:
                        f=inv.features(cp);cn=float(f.pop('_combined_net'));dom='UP' if cn>1e-9 else 'DOWN' if cn<-1e-9 else None;bf=mod.outcome_book(state,dom)
                        if bf:
                            base={'market_id':m,'market_end_ms':mend,'checkpoint_ms':cp,'book_age_ms':age,'seconds_left':(mend-cp)/1000.0,**f,**bf}
                            if c['kind']=='HAZ':
                                j=bisect.bisect_right(tt,cp);nxt=tt[j] if j<len(tt) else None;base.update({f'label_taker_{h}s':int(nxt is not None and nxt<=cp+h*1000) for h in (1,3,5)});hz.append(base)
                            elif c['kind']=='TAKER':
                                p=c['p'];side=str(p['side']);sh=float(p['shares']);base.update({'parent_id':str(p['parent_id']),'label_side':side,'label_effect':mod.effect_label(cn,side,sh)});tk.append(base)
                            else:
                                p=c['p'];side=str(p['side']);sh=float(p['shares']);px=float(p['average_price']);pg=inv.maker_up+inv.taker_up+inv.maker_down+inv.taker_down;pn=(inv.maker_up+inv.taker_up)-(inv.maker_down+inv.taker_down);pa=abs(pn);base.update({'parent_id':str(p['parent_id']),'intervention_side':side,'intervention_side_is_up':float(side=='UP'),'intervention_shares':sh,'intervention_avg_price':px,'post_taker_combined_abs_net':pa,'post_taker_combined_imbalance_ratio':mod.ratio(pa,pg),'post_taker_combined_paired_coverage':mod.coverage(inv.maker_up+inv.taker_up,inv.maker_down+inv.taker_down),'post_taker_worst_case_floor':min(inv.cash+inv.maker_up+inv.taker_up,inv.cash+inv.maker_down+inv.taker_down),'label_handoff':mod.maker_handoff_label(mp,side,int(p['last_event_ms']),int(p['last_event_ms'])+5000)});hd.append(base)
                    ci+=1
                if int(u['is_checkpoint']):state={'bids':{float(k):float(v) for k,v in (mod.dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (mod.dec(u['native_asks_z']) or {}).items()}}
                else:mod.apply_changes(state,mod.dec(u['changes_z']) or {})
                last=ut
        return pd.DataFrame(hz),pd.DataFrame(tk),pd.DataFrame(hd),sorted(markets)
    finally:b.close();t.close()


def numeric(df,features):return df[features].apply(pd.to_numeric,errors='coerce')
def multi(y,pred):
    return {'n':len(y),'accuracy':float(accuracy_score(y,pred)) if len(y) else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred)) if len(y) else None,'macroF1':float(f1_score(y,pred,average='macro',zero_division=0)) if len(y) else None,'truthDistribution':pd.Series(y).value_counts().to_dict(),'predictedDistribution':pd.Series(pred).value_counts().to_dict()}
def binary(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float);both=len(set(y.tolist()))==2
    return {'n':len(y),'positives':int(y.sum()),'positiveRate':float(y.mean()) if len(y) else None,'rocAuc':float(roc_auc_score(y,p)) if both else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1])) if len(y) else None}

def main()->int:
    c=json.loads(CONTRACT.read_text(encoding='utf-8')); cutoff=int(c['frozenAtMarketEndMs']); now=int(time.time()*1000)
    b=sqlite3.connect(f"file:{mod.BOOK_DB.resolve().as_posix()}?mode=ro",uri=True); latest=int(b.execute('select coalesce(max(source_timestamp_ms),0) from maker_book_inference_updates').fetchone()[0]);b.close(); finalized=min(now-20000,latest-15000)
    hz,tk,hd,markets=build_fresh(cutoff,finalized)
    if len(hz): hz.to_csv(HZ_OUT,index=False)
    if len(tk): tk.to_csv(TK_OUT,index=False)
    if len(hd): hd.to_csv(HD_OUT,index=False)
    rep={'reportVersion':'TARGET_MAKER_TAKER_COORDINATION_FORWARD_V1','freezeCutoffMarketEndMs':cutoff,'evaluatedThroughMarketEndMs':max([cutoff]+[int(x) for x in (hz.market_end_ms.unique().tolist() if len(hz) else [])]),'markets':markets,'coverage':{'hazardRows':len(hz),'takerRows':len(tk),'handoffRows':len(hd)},'hazard':{},'side':None,'effect':None,'handoff':None,'firstDivergence':{}}
    for h in (1,3,5):
        if not len(hz):continue
        art=joblib.load(c['artifacts'][f'hazard_{h}s']);m=art['model'];fs=art['features'];p=m.predict_proba(numeric(hz,fs))[:,1];rep['hazard'][str(h)]=binary(hz[f'label_taker_{h}s'],p)
    for key,df,label in [('side',tk,'label_side'),('effect',tk,'label_effect'),('handoff',hd,'label_handoff')]:
        if not len(df):continue
        art=joblib.load(c['artifacts'][key]);m=art['model'];fs=art['features'];pred=m.predict(numeric(df,fs));rep[key]=multi(df[label].astype(str).tolist(),pred)
        bad=np.where(np.asarray(pred)!=df[label].astype(str).to_numpy())[0]
        if len(bad):
            r=df.iloc[int(bad[0])];rep['firstDivergence'][key]={'marketId':int(r.market_id),'checkpointMs':int(r.checkpoint_ms),'truth':str(r[label]),'predicted':str(pred[int(bad[0])])}
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
