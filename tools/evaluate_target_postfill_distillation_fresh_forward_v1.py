from __future__ import annotations

import json, sqlite3, importlib.util, sys
from collections import defaultdict
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score, confusion_matrix, log_loss

ROOT = Path(__file__).resolve().parents[1]
TRAIN_PATH = ROOT / 'tools' / 'train_target_postfill_distillation_book_big_v1.py'
spec = importlib.util.spec_from_file_location('book_distill_train', TRAIN_PATH)
train = importlib.util.module_from_spec(spec); assert spec and spec.loader; sys.modules[spec.name] = train; spec.loader.exec_module(train)
base = train.base
BOOK_DB = ROOT/'data'/'wallet_maker_book_inference.db'
TARGET_DB = ROOT/'data'/'target_wallet_official_v1.db'
OUT_DIR = ROOT/'data'/'research'/'target_postfill_distillation_book_big_v1'
CONTRACT = OUT_DIR/'forward_contract_v1.json'
ARTIFACT = OUT_DIR/'model_full.joblib'  # frozen chronological-train artifact; do not retrain
OUT_CSV = OUT_DIR/'fresh_forward_states_v1.csv'
OUT_JSON = OUT_DIR/'fresh_forward_report_v1.json'
VERSION = 'TARGET_POSTFILL_DISTILLATION_FRESH_FORWARD_V1'


def ro(p: Path):
    c = sqlite3.connect(f'file:{p.resolve().as_posix()}?mode=ro', uri=True, timeout=30)
    c.row_factory = sqlite3.Row; c.execute('pragma query_only=on'); return c


def ece_binary(y, p, bins=10):
    y=np.asarray(y,float); p=np.asarray(p,float)
    if len(y)==0: return None
    edges=np.linspace(0,1,bins+1); out=0.0
    for i in range(bins):
        lo,hi=edges[i],edges[i+1]; mask=(p>=lo)&((p<hi) if i<bins-1 else (p<=hi))
        if mask.any(): out += mask.mean()*abs(y[mask].mean()-p[mask].mean())
    return float(out)


def evaluate(model, df, features, classes):
    y=df.label.astype(str).to_numpy(); pred=model.predict(df[features]); prob=model.predict_proba(df[features])
    cm=confusion_matrix(y,pred,labels=classes)
    truth={c:int(np.sum(y==c)) for c in classes}; pdist={c:int(np.sum(pred==c)) for c in classes}
    mean_prob={c:float(prob[:,i].mean()) for i,c in enumerate(classes)}
    recalls={c:(float(cm[i,i]/cm[i].sum()) if cm[i].sum() else None) for i,c in enumerate(classes)}
    ece={c:ece_binary((y==c).astype(float),prob[:,i]) for i,c in enumerate(classes)}
    brier=float(np.mean(np.sum((prob-np.eye(len(classes))[[classes.index(v) for v in y]])**2,axis=1)))
    first=[]
    tmp=df.copy(); tmp['pred']=pred; tmp['confidence']=prob.max(axis=1)
    for mid,g in tmp.sort_values(['market_end_ms','checkpoint_ms']).groupby('market_id',sort=False):
        bad=g[g.label!=g.pred]
        if len(bad):
            r=bad.iloc[0]; first.append({'market_id':int(mid),'checkpoint_ms':int(r.checkpoint_ms),'seconds_left':float(r.seconds_left),'truth':str(r.label),'pred':str(r.pred),'confidence':float(r.confidence),'post_abs_net':float(r.post_abs_net),'opp_best_bid_locked_edge':float(r.opp_best_bid_locked_edge),'same_side_fill_streak':float(r.same_side_fill_streak),'last_opp_fill_age_ms':float(r.last_opp_fill_age_ms)})
    return {'rows':len(df),'markets':int(df.market_id.nunique()),'accuracy':float(accuracy_score(y,pred)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'macroF1':float(f1_score(y,pred,labels=classes,average='macro',zero_division=0)),'logLoss':float(log_loss(y,prob,labels=classes)),'multiclassBrier':brier,'truthDistribution':truth,'predictedDistribution':pdist,'meanPredictedProbability':mean_prob,'perClassRecall':recalls,'perClassECE10':ece,'confusionMatrix':{'labels':classes,'matrix':cm.tolist()},'firstDivergenceByMarket':first}


def build_fresh(cutoff):
    b=ro(BOOK_DB); t=ro(TARGET_DB)
    try:
        meta=base.load_market_meta(b)
        post={m for m,r in meta.items() if int(r['window_end_ms'])>cutoff}
        um={int(r[0]) for r in b.execute('select distinct market_id from maker_book_inference_updates') if int(r[0]) in post}
        tm={int(r[0]) for r in t.execute("select distinct market_id from wallet_shadow_target_events where asset='BTC' and role='MAKER' and quote_type='BID'") if int(r[0]) in post}
        markets=post & um & tm
        parents=base.load_parents(b,markets); events=base.load_events(t,markets)
        valid=[m for m in markets if parents.get(m) and events.get(m)]
        rows=[]; dropped=defaultdict(int); ages=[]
        for m in sorted(valid,key=lambda x:int(meta[x]['window_end_ms'])):
            ps=parents[m]; ev=events[m]; mend=int(meta[m]['window_end_ms']); candidates=[]
            for p in ps:
                ft=int(p['last_target_ms']); cp=ft+1000; sec=(mend-cp)/1000.0
                if cp<=cutoff: dropped['checkpoint_not_post_cutoff']+=1; continue
                if sec<5 or sec>299.5: dropped['seconds_left']+=1; continue
                inv=base.inventory_at(ev,ft)
                if inv['gross']<=0 or inv['abs_net']<18-1e-9: dropped['inventory']+=1; continue
                side=str(p['target_side']); dom='UP' if inv['net']>0 else 'DOWN'
                if side!=dom: dropped['not_dominant_fill']+=1; continue
                lab=train.reaction_label(ps,p,ft+1000,ft+5000)
                if lab is None: dropped['ambiguous']+=1; continue
                candidates.append({'p':p,'ft':ft,'cp':cp,'sec':sec,'inv':inv,'side':side,'label':lab})
            if not candidates: continue
            candidates.sort(key=lambda x:x['cp']); ci=0; state={'bids':{},'asks':{}}; last_update=None
            q="select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id"
            for u in b.execute(q,(m,)):
                ut=int(u['source_timestamp_ms'])
                while ci<len(candidates) and candidates[ci]['cp']<ut:
                    c=candidates[ci]; age=(c['cp']-last_update) if last_update is not None else 10**9
                    if 0<=age<=2000:
                        bf=train.book_features(state,c['side'],float(c['p']['target_price']))
                        if bf:
                            lf=base.lifecycle_features(ev,c['cp'],c['side']); inv=c['inv']
                            rows.append({'market_id':m,'market_end_ms':mend,'fill_ms':c['ft'],'checkpoint_ms':c['cp'],'filled_side':c['side'],'label':c['label'],'book_age_ms':age,'seconds_left':c['sec'],'post_gross':inv['gross'],'post_abs_net':inv['abs_net'],'post_imbalance_ratio':inv['ratio'],'post_paired_coverage':inv['coverage'],'worst_case_floor':inv['floor'],'current_parent_resting_ms':float(c['p']['last_target_ms'])-float(c['p']['placement_first_ms']),**lf,**bf}); ages.append(age)
                        else: dropped['empty_book']+=1
                    else: dropped['book_stale']+=1
                    ci+=1
                if int(u['is_checkpoint']): state={'bids':{float(k):float(v) for k,v in (train.dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (train.dec(u['native_asks_z']) or {}).items()}}
                else: train.apply_changes(state,train.dec(u['changes_z']) or {})
                last_update=ut
            while ci<len(candidates):
                c=candidates[ci]; age=(c['cp']-last_update) if last_update is not None else 10**9
                if 0<=age<=2000:
                    bf=train.book_features(state,c['side'],float(c['p']['target_price']))
                    if bf:
                        lf=base.lifecycle_features(ev,c['cp'],c['side']); inv=c['inv']
                        rows.append({'market_id':m,'market_end_ms':mend,'fill_ms':c['ft'],'checkpoint_ms':c['cp'],'filled_side':c['side'],'label':c['label'],'book_age_ms':age,'seconds_left':c['sec'],'post_gross':inv['gross'],'post_abs_net':inv['abs_net'],'post_imbalance_ratio':inv['ratio'],'post_paired_coverage':inv['coverage'],'worst_case_floor':inv['floor'],'current_parent_resting_ms':float(c['p']['last_target_ms'])-float(c['p']['placement_first_ms']),**lf,**bf}); ages.append(age)
                    else: dropped['empty_book']+=1
                else: dropped['book_stale']+=1
                ci+=1
        df=pd.DataFrame(rows)
        if len(df): df=df.sort_values(['market_end_ms','checkpoint_ms','market_id']).reset_index(drop=True)
        return df, {'postCutoffMetaMarkets':len(post),'bookUpdateMarkets':len(um),'targetMakerEventMarkets':len(tm),'eligibleOverlapMarkets':len(valid),'dropped':dict(dropped),'bookAgeMs':base.stats(ages)}
    finally: b.close(); t.close()


def main():
    contract=json.loads(CONTRACT.read_text(encoding='utf-8')); cutoff=int(contract['freezeCutoffMarketEndMs'])
    art=joblib.load(ARTIFACT); model=art['model']; features=list(art['features']); classes=list(art['classes'])
    df,cov=build_fresh(cutoff)
    report={'reportVersion':VERSION,'researchOnly':True,'prospective':True,'freezeCutoffMarketEndMs':cutoff,'artifactUsed':str(ARTIFACT),'artifactGuard':'Frozen chronological train-only model_full.joblib; no retraining/tuning from fresh outcomes.','coverage':cov}
    if len(df):
        df.to_csv(OUT_CSV,index=False); report['metrics']=evaluate(model,df,features,classes); report['freshMarketEnds']=[int(x) for x in sorted(df.market_end_ms.unique())]
    else:
        report['metrics']=None; report['freshMarketEnds']=[]
    OUT_JSON.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
