from __future__ import annotations

import argparse, importlib.util, json, math, sqlite3, statistics, sys, zlib, bisect
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score, log_loss, confusion_matrix

ROOT=Path(__file__).resolve().parents[1]
BASE_PATH=ROOT/'tools'/'train_target_postfill_distillation_big_v1.py'
spec=importlib.util.spec_from_file_location('distill_big_base',BASE_PATH); base=importlib.util.module_from_spec(spec); assert spec and spec.loader; sys.modules[spec.name]=base; spec.loader.exec_module(base)
BOOK_DB=ROOT/'data'/'wallet_maker_book_inference.db'; TARGET_DB=ROOT/'data'/'target_wallet_official_v1.db'
OUT_DIR=ROOT/'data'/'research'/'target_postfill_distillation_book_big_v1'; DATASET=OUT_DIR/'teacher_states_book_v1.csv'; MANIFEST=OUT_DIR/'dataset_manifest.json'; COMPARISON=OUT_DIR/'model_comparison.json'
VERSION='TARGET_POSTFILL_DISTILLATION_BOOK_BIG_V1'; GRID=.01

CORE=['seconds_left','post_gross','post_abs_net','post_imbalance_ratio','post_paired_coverage','worst_case_floor']
BOOK=['current_side_bid','current_side_ask','current_spread_ticks','current_bid_depth','current_ask_depth','current_top3_bid_depth','opposite_bid','opposite_ask','opposite_bid_depth','opposite_top3_bid_depth','book_pair_edge']
LIFE=['current_parent_resting_ms','last_same_fill_age_ms','last_opp_fill_age_ms','same_side_fill_streak','same_fills_5s','opp_fills_5s','same_fills_10s','opp_fills_10s','absnet_change_10s']
PAIR=['current_fill_price','opp_best_bid_locked_edge','opp_minus1_locked_edge','fill_vs_current_bid_ticks']
FEATURE_SETS={'CORE':CORE,'CORE_BOOK':CORE+BOOK,'CORE_BOOK_LIFECYCLE':CORE+BOOK+LIFE,'FULL':CORE+BOOK+LIFE+PAIR}


def ro(p:Path):
    c=sqlite3.connect(f'file:{p.resolve().as_posix()}?mode=ro',uri=True,timeout=30); c.row_factory=sqlite3.Row; c.execute('pragma query_only=on'); return c

def dec(v): return json.loads(zlib.decompress(v).decode('utf-8')) if v else None

def apply_changes(book,changes):
    if not isinstance(changes,dict): return
    for key in ('bids','asks'):
        for ch in changes.get(key,[]) or []:
            p=float(ch['price']); a=float(ch['after'])
            if a<=1e-12: book[key].pop(p,None)
            else: book[key][p]=a

def top3(side:dict[float,float], reverse:bool)->float:
    return sum(v for _,v in sorted(side.items(),key=lambda kv:kv[0],reverse=reverse)[:3])

def book_features(state:dict[str,dict[float,float]], side:str, fill_px:float)->dict[str,float]|None:
    bids=state['bids']; asks=state['asks']
    if not bids or not asks:return None
    bb=max(bids); ba=min(asks); bbd=bids[bb]; bad=asks[ba]
    if side=='UP':
        cb,ca=bb,ba; cbd,cad=bbd,bad; c3=top3(bids,True)
        ob,oa=1.0-ba,1.0-bb; obd=bad; o3=top3(asks,False)
    else:
        cb,ca=1.0-ba,1.0-bb; cbd,cad=bad,bbd; c3=top3(asks,False)
        ob,oa=bb,ba; obd=bbd; o3=top3(bids,True)
    edge=1.0-fill_px-ob
    return {'current_side_bid':cb,'current_side_ask':ca,'current_spread_ticks':(ca-cb)/GRID,'current_bid_depth':cbd,'current_ask_depth':cad,'current_top3_bid_depth':c3,'opposite_bid':ob,'opposite_ask':oa,'opposite_bid_depth':obd,'opposite_top3_bid_depth':o3,'book_pair_edge':1.0-cb-ob,'current_fill_price':fill_px,'opp_best_bid_locked_edge':edge,'opp_minus1_locked_edge':edge+GRID,'fill_vs_current_bid_ticks':(cb-fill_px)/GRID}

def reaction_label(parents,current,start,end):
    pid=str(current['parent_id']); c=[]
    for p in parents:
        if str(p['parent_id'])==pid: continue
        t=int(p['placement_first_ms'])
        if t<=start: continue
        if t>end: break
        c.append((t,str(p['target_side'])))
    if not c:return 'PAUSE'
    ft=min(t for t,_ in c); sides={s for t,s in c if t==ft}
    if len(sides)!=1:return None
    return 'CONTINUE_SAME' if next(iter(sides))==str(current['target_side']) else 'SWITCH_OPPOSITE'

def build():
    OUT_DIR.mkdir(parents=True,exist_ok=True); b=ro(BOOK_DB); t=ro(TARGET_DB)
    try:
        meta=base.load_market_meta(b); um={int(r[0]) for r in b.execute('select distinct market_id from maker_book_inference_updates')}; tm={int(r[0]) for r in t.execute("select distinct market_id from wallet_shadow_target_events where asset='BTC' and role='MAKER' and quote_type='BID'")}; markets=set(meta)&um&tm
        parents=base.load_parents(b,markets); events=base.load_events(t,markets); valid=[m for m in markets if parents.get(m) and events.get(m)]
        rows=[]; dropped=defaultdict(int); ages=[]
        for mi,m in enumerate(sorted(valid,key=lambda x:int(meta[x]['window_end_ms'])),1):
            ps=parents[m]; ev=events[m]; mend=int(meta[m]['window_end_ms']); candidates=[]
            for p in ps:
                ft=int(p['last_target_ms']); cp=ft+1000; sec=(mend-cp)/1000.0
                if sec<5 or sec>299.5: dropped['seconds_left']+=1; continue
                inv=base.inventory_at(ev,ft)
                if inv['gross']<=0 or inv['abs_net']<18-1e-9: dropped['inventory']+=1; continue
                side=str(p['target_side']); dom='UP' if inv['net']>0 else 'DOWN'
                if side!=dom: dropped['not_dominant_fill']+=1; continue
                lab=reaction_label(ps,p,ft+1000,ft+5000)
                if lab is None: dropped['ambiguous']+=1; continue
                candidates.append({'p':p,'ft':ft,'cp':cp,'sec':sec,'inv':inv,'side':side,'label':lab})
            if not candidates: continue
            candidates.sort(key=lambda x:x['cp']); ci=0; state={'bids':{},'asks':{}}; last_update=None
            for u in b.execute("select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id",(m,)):
                ut=int(u['source_timestamp_ms'])
                while ci<len(candidates) and candidates[ci]['cp']<ut:
                    c=candidates[ci]; age=(c['cp']-last_update) if last_update is not None else 10**9
                    if age<=2000:
                        bf=book_features(state,c['side'],float(c['p']['target_price']))
                        if bf:
                            lf=base.lifecycle_features(ev,c['cp'],c['side']); inv=c['inv']
                            rows.append({'market_id':m,'market_end_ms':mend,'fill_ms':c['ft'],'checkpoint_ms':c['cp'],'filled_side':c['side'],'label':c['label'],'book_age_ms':age,'seconds_left':c['sec'],'post_gross':inv['gross'],'post_abs_net':inv['abs_net'],'post_imbalance_ratio':inv['ratio'],'post_paired_coverage':inv['coverage'],'worst_case_floor':inv['floor'],'current_parent_resting_ms':float(c['p']['last_target_ms'])-float(c['p']['placement_first_ms']),**lf,**bf}); ages.append(age)
                        else:dropped['empty_book']+=1
                    else:dropped['book_stale']+=1
                    ci+=1
                if int(u['is_checkpoint']): state={'bids':{float(k):float(v) for k,v in (dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(u['native_asks_z']) or {}).items()}}
                else: apply_changes(state,dec(u['changes_z']) or {})
                last_update=ut
            while ci<len(candidates):
                c=candidates[ci]; age=(c['cp']-last_update) if last_update is not None else 10**9
                if 0<=age<=2000:
                    bf=book_features(state,c['side'],float(c['p']['target_price']))
                    if bf:
                        lf=base.lifecycle_features(ev,c['cp'],c['side']); inv=c['inv']; rows.append({'market_id':m,'market_end_ms':mend,'fill_ms':c['ft'],'checkpoint_ms':c['cp'],'filled_side':c['side'],'label':c['label'],'book_age_ms':age,'seconds_left':c['sec'],'post_gross':inv['gross'],'post_abs_net':inv['abs_net'],'post_imbalance_ratio':inv['ratio'],'post_paired_coverage':inv['coverage'],'worst_case_floor':inv['floor'],'current_parent_resting_ms':float(c['p']['last_target_ms'])-float(c['p']['placement_first_ms']),**lf,**bf}); ages.append(age)
                else:dropped['book_stale']+=1
                ci+=1
            if mi%50==0: print(json.dumps({'progressMarkets':mi,'totalMarkets':len(valid),'rows':len(rows)}),flush=True)
        df=pd.DataFrame(rows).sort_values(['market_end_ms','checkpoint_ms','market_id']).reset_index(drop=True); df.to_csv(DATASET,index=False)
        man={'reportVersion':VERSION,'researchOnly':True,'runtimeTargetDataAllowed':False,'coverage':{'candidateMarkets':len(valid),'rows':len(df),'markets':int(df.market_id.nunique()) if len(df) else 0,'labels':df.label.value_counts().to_dict() if len(df) else {},'dropped':dict(dropped),'bookAgeMs':base.stats(ages)},'dataset':str(DATASET),'featureSets':FEATURE_SETS,'teacherLabel':'Target anchored post-fill reaction training label only; never runtime input.','studentFeatureRule':'Public Predict book + equivalent own portfolio/lifecycle/current fill only.'}; MANIFEST.write_text(json.dumps(man,ensure_ascii=False,indent=2),encoding='utf-8'); return man
    finally:b.close();t.close()

def splits(df):
    x=df[['market_id','market_end_ms']].drop_duplicates().sort_values('market_end_ms'); ms=[int(v) for v in x.market_id]; n=len(ms); a=int(n*.70); bb=int(n*.85); return {'train':set(ms[:a]),'validation':set(ms[a:bb]),'test':set(ms[bb:])}
def model(features): return ExplainableBoostingClassifier(feature_names=features,max_bins=96,max_interaction_bins=48,interactions=6,outer_bags=6,learning_rate=.035,max_rounds=2200,early_stopping_rounds=100,min_samples_leaf=10,n_jobs=-2,random_state=20260819)
def evaluate(m,df,features):
    y=df.label.astype(str).tolist(); pred=m.predict(df[features]); prob=m.predict_proba(df[features]); labs=list(m.classes_); cm=confusion_matrix(y,pred,labels=labs).tolist(); return {'n':len(y),'truthDistribution':{c:y.count(c) for c in labs},'predictedDistribution':{c:int(sum(1 for x in pred if x==c)) for c in labs},'accuracy':float(accuracy_score(y,pred)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'macroF1':float(f1_score(y,pred,labels=labs,average='macro',zero_division=0)),'logLoss':float(log_loss(y,prob,labels=labs)),'perClassRecall':{c:(cm[i][i]/sum(cm[i]) if sum(cm[i]) else None) for i,c in enumerate(labs)},'confusionMatrix':{'labels':labs,'matrix':cm}}
def top_terms(m,n=15):
    imp=list(m.term_importances()); names=list(m.term_names_); ix=sorted(range(len(imp)),key=lambda i:float(imp[i]),reverse=True)[:n]; return [{'term':str(names[i]),'importance':float(imp[i])} for i in ix]
def train(name):
    df=pd.read_csv(DATASET); sp=splits(df); feats=FEATURE_SETS[name]; parts={k:df[df.market_id.astype(int).isin(v)].copy() for k,v in sp.items()}; m=model(feats); m.fit(parts['train'][feats],parts['train'].label.astype(str).tolist()); art=OUT_DIR/f'model_{name.lower()}.joblib'; joblib.dump({'version':VERSION,'researchOnly':True,'runtimeTargetDataAllowed':False,'featureSet':name,'features':feats,'classes':list(m.classes_),'model':m},art); rep={'reportVersion':VERSION,'featureSet':name,'features':feats,'splitMarkets':{k:len(v) for k,v in sp.items()},'train':evaluate(m,parts['train'],feats),'validation':evaluate(m,parts['validation'],feats),'test':evaluate(m,parts['test'],feats),'topTerms':top_terms(m),'artifact':str(art)}; (OUT_DIR/f'report_{name.lower()}.json').write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); return rep
def consolidate():
    rs={};
    for n in FEATURE_SETS:
        p=OUT_DIR/f'report_{n.lower()}.json'
        if p.exists():rs[n]=json.loads(p.read_text(encoding='utf-8'))
    out={'reportVersion':VERSION,'models':{n:{'validation':r['validation'],'test':r['test'],'topTerms':r['topTerms']} for n,r in rs.items()},'guards':['Chronological market holdout.','Target action only teacher label.','Runtime artifact accepts no Target event/action/future/winner.','Identical EBM capacity across feature-set ablations; no hyperparameter sweep.']}; COMPARISON.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); return out
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--mode',choices=['build','train','consolidate'],required=True); ap.add_argument('--feature-set',choices=list(FEATURE_SETS)); a=ap.parse_args(); OUT_DIR.mkdir(parents=True,exist_ok=True)
    if a.mode=='build':print(json.dumps(build(),ensure_ascii=False,indent=2)); return
    if a.mode=='train':print(json.dumps(train(a.feature_set),ensure_ascii=False,indent=2)); return
    print(json.dumps(consolidate(),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
