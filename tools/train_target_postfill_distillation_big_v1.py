from __future__ import annotations

import argparse
import bisect
import json
import math
import sqlite3
import statistics
import zlib
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score, log_loss, confusion_matrix

ROOT = Path(__file__).resolve().parents[1]
BOOK_DB = ROOT / 'data' / 'wallet_maker_book_inference.db'
TARGET_DB = ROOT / 'data' / 'target_wallet_official_v1.db'
MICRO_DB = ROOT / 'data' / 'microstructure.db'
OUT_DIR = ROOT / 'data' / 'research' / 'target_postfill_distillation_big_v1'
DATASET = OUT_DIR / 'target_postfill_teacher_states_v1.csv'
MANIFEST = OUT_DIR / 'dataset_manifest.json'
COMPARISON = OUT_DIR / 'model_comparison.json'
VERSION = 'TARGET_POSTFILL_DISTILLATION_BIG_V1'
GRID = 0.01
CLASSES = ['CONTINUE_SAME', 'SWITCH_OPPOSITE', 'PAUSE']

PUBLIC_PORTFOLIO = [
    'seconds_left', 'post_gross', 'post_abs_net', 'post_imbalance_ratio', 'post_paired_coverage',
    'worst_case_floor', 'prediction_side_mid', 'oriented_direction_score',
    'spot_queue_oriented', 'spot_taker_1s_oriented', 'futures_queue_oriented', 'futures_taker_1s_oriented',
    'spot_return_1s_oriented_bps', 'spot_return_3s_oriented_bps',
    'futures_return_1s_oriented_bps', 'futures_return_3s_oriented_bps',
    'basis_oriented_bps', 'volatility_alert',
]
LIFECYCLE = [
    'last_same_fill_age_ms', 'last_opp_fill_age_ms', 'same_side_fill_streak',
    'same_fills_5s', 'opp_fills_5s', 'same_fills_10s', 'opp_fills_10s', 'absnet_change_10s',
]
PAIR = ['opp_best_bid_locked_edge']
MARKOUT = ['markout1s_ticks']
FEATURE_SETS = {
    'PUBLIC_PORTFOLIO': PUBLIC_PORTFOLIO,
    'PLUS_LIFECYCLE': PUBLIC_PORTFOLIO + LIFECYCLE,
    'PLUS_PAIR_EDGE': PUBLIC_PORTFOLIO + PAIR,
    'FULL': PUBLIC_PORTFOLIO + LIFECYCLE + PAIR + MARKOUT,
}


def ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f'file:{path.resolve().as_posix()}?mode=ro', uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    return con


def dec(blob: bytes | None) -> Any:
    return json.loads(zlib.decompress(blob).decode('utf-8')) if blob else None


def finite(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def apply_changes(book: dict[str, dict[float, float]], changes: Any) -> None:
    if not isinstance(changes, dict):
        return
    for key in ('bids', 'asks'):
        for ch in changes.get(key, []) or []:
            p = float(ch['price']); after = float(ch['after'])
            if after <= 1e-12:
                book[key].pop(p, None)
            else:
                book[key][p] = after


def load_micro(con: sqlite3.Connection, start_ms: int, end_ms: int) -> tuple[list[int], list[dict[str, Any]]]:
    cols = [
        'timestamp_ns','spot_price','spot_queue_imbalance','spot_taker_imbalance_1s',
        'futures_price','futures_queue_imbalance','futures_taker_imbalance_1s',
        'perp_spot_basis_bps','prediction_up_mid','volatility_alert','direction_score'
    ]
    q = f"SELECT {','.join(cols)} FROM microstructure_snapshots INDEXED BY micro_snapshots_time_idx WHERE timestamp_ns>=? AND timestamp_ns<=? ORDER BY timestamp_ns"
    rows: list[dict[str, Any]] = []
    times: list[int] = []
    for r in con.execute(q, (int(start_ms)*1_000_000, int(end_ms)*1_000_000)):
        d = dict(r); ms = int(d.pop('timestamp_ns')) // 1_000_000
        times.append(ms); rows.append(d)
    return times, rows


def at_before(times: list[int], rows: list[dict[str, Any]], ms: int, max_age_ms: int = 2500) -> tuple[int, dict[str, Any]] | None:
    i = bisect.bisect_right(times, ms) - 1
    if i < 0 or ms - times[i] > max_age_ms:
        return None
    return times[i], rows[i]


def at_after(times: list[int], rows: list[dict[str, Any]], ms: int, max_delay_ms: int = 2500) -> tuple[int, dict[str, Any]] | None:
    i = bisect.bisect_left(times, ms)
    if i >= len(times) or times[i] - ms > max_delay_ms:
        return None
    return times[i], rows[i]


def pct(xs: list[float], p: float) -> float | None:
    if not xs: return None
    ys = sorted(xs); pos=(len(ys)-1)*p; lo=int(math.floor(pos)); hi=int(math.ceil(pos)); w=pos-lo
    return ys[lo]*(1-w)+ys[hi]*w


def stats(xs: list[float]) -> dict[str, Any]:
    ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return {'n':len(ys),'min':min(ys) if ys else None,'max':max(ys) if ys else None,'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'p25':pct(ys,.25),'p75':pct(ys,.75),'p90':pct(ys,.9)}


def side_mid(row: dict[str, Any], side: str) -> float | None:
    up = finite(row.get('prediction_up_mid'))
    if up is None: return None
    return up if side == 'UP' else 1.0-up


def oriented(v: Any, side: str) -> float | None:
    x=finite(v)
    if x is None: return None
    return x if side=='UP' else -x


def ret_bps(now: float | None, prev: float | None, side: str) -> float | None:
    if now is None or prev is None or prev == 0: return None
    r=(now/prev-1.0)*10000.0
    return r if side=='UP' else -r


def load_market_meta(book: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    return {int(r['market_id']):dict(r) for r in book.execute("SELECT market_id,first_seen_ms,window_end_ms,status FROM maker_book_inference_markets WHERE window_end_ms IS NOT NULL")}


def load_parents(book: sqlite3.Connection, markets: set[int]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int,list[dict[str,Any]]] = defaultdict(list)
    ids=sorted(markets)
    for k in range(0,len(ids),250):
        batch=ids[k:k+250]; qs=','.join('?' for _ in batch)
        for r in book.execute(f"""
            SELECT parent_id,market_id,target_side,target_price,native_price,native_book_side,
                   placement_first_ms,last_target_ms,placement_coverage,fill_allocation_coverage,confidence
            FROM maker_book_inference_v21_parent_lifecycles
            WHERE market_id IN ({qs}) AND placement_first_ms IS NOT NULL AND last_target_ms IS NOT NULL
              AND placement_supports_18=1 AND placement_coverage>=0.85
              AND fill_allocation_coverage>=0.70 AND confidence>=0.75
            ORDER BY market_id,placement_first_ms,last_target_ms
        """, batch): out[int(r['market_id'])].append(dict(r))
    return out


def load_events(target: sqlite3.Connection, markets: set[int]) -> dict[int,list[dict[str,Any]]]:
    out: dict[int,list[dict[str,Any]]] = defaultdict(list)
    ids=sorted(markets)
    for k in range(0,len(ids),250):
        batch=ids[k:k+250]; qs=','.join('?' for _ in batch)
        for r in target.execute(f"""
          SELECT market_id,event_ms,side,price,shares FROM wallet_shadow_target_events
          WHERE market_id IN ({qs}) AND asset='BTC' AND role='MAKER' AND quote_type='BID' AND side IN ('UP','DOWN')
          ORDER BY market_id,event_ms,id
        """, batch): out[int(r['market_id'])].append(dict(r))
    return out


def inventory_at(events: list[dict[str,Any]], t: int) -> dict[str,float]:
    up=down=up_cost=down_cost=0.0
    for e in events:
        if int(e['event_ms']) > t: break
        sh=float(e['shares']); px=float(e['price'])
        if str(e['side'])=='UP': up+=sh; up_cost+=sh*px
        else: down+=sh; down_cost+=sh*px
    gross=up+down; net=up-down; cost=up_cost+down_cost
    return {
        'up':up,'down':down,'gross':gross,'net':net,'abs_net':abs(net),
        'ratio':abs(net)/gross if gross>1e-9 else 0.0,
        'coverage':2.0*min(up,down)/gross if gross>1e-9 else 0.0,
        'floor':min(up-cost,down-cost), 'cost':cost,
    }


def lifecycle_features(events: list[dict[str,Any]], t: int, side: str) -> dict[str,float | None]:
    ts=[int(e['event_ms']) for e in events]
    i=bisect.bisect_right(ts,t)-1
    if i<0:
        return {'last_same_fill_age_ms':None,'last_opp_fill_age_ms':None,'same_side_fill_streak':0.0,'same_fills_5s':0.0,'opp_fills_5s':0.0,'same_fills_10s':0.0,'opp_fills_10s':0.0,'absnet_change_10s':0.0}
    same_last=opp_last=None; streak=0
    for j in range(i,-1,-1):
        es=str(events[j]['side']); et=int(events[j]['event_ms'])
        if es==side and same_last is None: same_last=et
        if es!=side and opp_last is None: opp_last=et
        if j==i or (streak>0 and es==side):
            if es==side: streak+=1
            elif j==i: streak=0
            else: break
        if same_last is not None and opp_last is not None and j < i-100: break
    def counts(window:int) -> tuple[int,int]:
        lo=t-window; a=b=0
        j=bisect.bisect_left(ts,lo)
        while j<=i:
            if str(events[j]['side'])==side:a+=1
            else:b+=1
            j+=1
        return a,b
    s5,o5=counts(5000); s10,o10=counts(10000)
    now=inventory_at(events,t); past=inventory_at(events,t-10000)
    return {
        'last_same_fill_age_ms': float(t-same_last) if same_last is not None else None,
        'last_opp_fill_age_ms': float(t-opp_last) if opp_last is not None else None,
        'same_side_fill_streak':float(streak), 'same_fills_5s':float(s5),'opp_fills_5s':float(o5),
        'same_fills_10s':float(s10),'opp_fills_10s':float(o10),
        'absnet_change_10s':float(now['abs_net']-past['abs_net']),
    }


def reaction_label(parents: list[dict[str,Any]], current: dict[str,Any], start_ms: int, end_ms: int) -> str | None:
    candidates=[]
    pid=str(current['parent_id'])
    for p in parents:
        if str(p['parent_id'])==pid: continue
        t=int(p['placement_first_ms'])
        if t <= start_ms: continue
        if t > end_ms: break
        candidates.append((t,str(p['target_side'])))
    if not candidates: return 'PAUSE'
    first_t=min(t for t,_ in candidates)
    sides={s for t,s in candidates if t==first_t}
    if len(sides)>1: return None
    return 'CONTINUE_SAME' if next(iter(sides))==str(current['target_side']) else 'SWITCH_OPPOSITE'


def book_edge_series(book: sqlite3.Connection, market: int, checkpoints: list[tuple[int,str,float]]) -> dict[int,float | None]:
    # checkpoints: (ms,current_side,current_target_fill_price)
    if not checkpoints: return {}
    cps=sorted(checkpoints); out:dict[int,float|None]={}; ci=0; state={'bids':{},'asks':{}}
    for u in book.execute("SELECT source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z FROM maker_book_inference_updates WHERE market_id=? ORDER BY source_timestamp_ms,id",(market,)):
        ut=int(u['source_timestamp_ms'])
        while ci<len(cps) and cps[ci][0] < ut:
            ms,side,fill_px=cps[ci]
            if side=='UP': opp=(1.0-min(state['asks'])) if state['asks'] else None
            else: opp=max(state['bids']) if state['bids'] else None
            out[ms]=(1.0-fill_px-opp) if opp is not None else None
            ci+=1
        if int(u['is_checkpoint']):
            state={'bids':{float(k):float(v) for k,v in (dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(u['native_asks_z']) or {}).items()}}
        else: apply_changes(state,dec(u['changes_z']) or {})
    while ci<len(cps):
        ms,side,fill_px=cps[ci]
        if side=='UP': opp=(1.0-min(state['asks'])) if state['asks'] else None
        else: opp=max(state['bids']) if state['bids'] else None
        out[ms]=(1.0-fill_px-opp) if opp is not None else None
        ci+=1
    return out


def build_dataset() -> dict[str,Any]:
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    book=ro(BOOK_DB); target=ro(TARGET_DB); micro=ro(MICRO_DB)
    try:
        meta=load_market_meta(book)
        update_markets={int(r[0]) for r in book.execute('SELECT DISTINCT market_id FROM maker_book_inference_updates')}
        target_markets={int(r[0]) for r in target.execute("SELECT DISTINCT market_id FROM wallet_shadow_target_events WHERE asset='BTC' AND role='MAKER' AND quote_type='BID'")}
        markets=set(meta)&update_markets&target_markets
        parents=load_parents(book,markets); events=load_events(target,markets)
        valid=[m for m in markets if parents.get(m) and events.get(m)]
        start=min(int(p['last_target_ms']) for m in valid for p in parents[m])-5000
        end=max(int(p['last_target_ms']) for m in valid for p in parents[m])+6000
        mtimes,mrows=load_micro(micro,start,end)
        rows=[]; dropped=defaultdict(int)
        for ix,m in enumerate(sorted(valid,key=lambda x:int(meta[x]['window_end_ms'])),1):
            ps=parents[m]; ev=events[m]; mend=int(meta[m]['window_end_ms'])
            prepared=[]
            for p in ps:
                fill_t=int(p['last_target_ms']); side=str(p['target_side'])
                cp=at_after(mtimes,mrows,fill_t+1000,2500); pre=at_before(mtimes,mrows,fill_t,2500)
                if cp is None or pre is None: dropped['micro_alignment']+=1; continue
                cp_t,cp_row=cp; pre_t,pre_row=pre
                sec=(mend-cp_t)/1000.0
                if sec < 5 or sec > 299.5: dropped['seconds_left']+=1; continue
                inv=inventory_at(ev,fill_t)
                if inv['gross']<=0 or inv['abs_net']<18-1e-9: dropped['inventory']+=1; continue
                dom='UP' if inv['net']>0 else 'DOWN'
                if side!=dom: dropped['not_dominant_fill']+=1; continue
                label=reaction_label(ps,p,fill_t+1000,fill_t+5000)
                if label is None: dropped['ambiguous_reaction']+=1; continue
                p1=at_before(mtimes,mrows,cp_t-1000,2500); p3=at_before(mtimes,mrows,cp_t-3000,3500)
                spot=finite(cp_row.get('spot_price')); fut=finite(cp_row.get('futures_price'))
                spot1=finite(p1[1].get('spot_price')) if p1 else None; fut1=finite(p1[1].get('futures_price')) if p1 else None
                spot3=finite(p3[1].get('spot_price')) if p3 else None; fut3=finite(p3[1].get('futures_price')) if p3 else None
                sm0=side_mid(pre_row,side); sm1=side_mid(cp_row,side)
                mark=((sm1-sm0)/GRID) if sm0 is not None and sm1 is not None else None
                prepared.append((cp_t,side,float(p['target_price'])))
                rec={
                    'market_id':m,'market_end_ms':mend,'fill_ms':fill_t,'checkpoint_ms':cp_t,'filled_side':side,'label':label,
                    'seconds_left':sec,'post_gross':inv['gross'],'post_abs_net':inv['abs_net'],'post_imbalance_ratio':inv['ratio'],
                    'post_paired_coverage':inv['coverage'],'worst_case_floor':inv['floor'],
                    'prediction_side_mid':side_mid(cp_row,side),'oriented_direction_score':oriented(cp_row.get('direction_score'),side),
                    'spot_queue_oriented':oriented(cp_row.get('spot_queue_imbalance'),side),'spot_taker_1s_oriented':oriented(cp_row.get('spot_taker_imbalance_1s'),side),
                    'futures_queue_oriented':oriented(cp_row.get('futures_queue_imbalance'),side),'futures_taker_1s_oriented':oriented(cp_row.get('futures_taker_imbalance_1s'),side),
                    'spot_return_1s_oriented_bps':ret_bps(spot,spot1,side),'spot_return_3s_oriented_bps':ret_bps(spot,spot3,side),
                    'futures_return_1s_oriented_bps':ret_bps(fut,fut1,side),'futures_return_3s_oriented_bps':ret_bps(fut,fut3,side),
                    'basis_oriented_bps':oriented(cp_row.get('perp_spot_basis_bps'),side),'volatility_alert':finite(cp_row.get('volatility_alert')),
                    'markout1s_ticks':mark,
                    **lifecycle_features(ev,cp_t,side),
                    'current_fill_price':float(p['target_price']),
                }
                rows.append(rec)
            edges=book_edge_series(book,m,prepared)
            # Fill edge into rows from this market using checkpoint time.
            for r in rows:
                if int(r['market_id'])==m and r.get('opp_best_bid_locked_edge') is None:
                    r['opp_best_bid_locked_edge']=edges.get(int(r['checkpoint_ms']))
            if ix%50==0: print(json.dumps({'progressMarkets':ix,'totalMarkets':len(valid),'rows':len(rows)}),flush=True)
        df=pd.DataFrame(rows).sort_values(['market_end_ms','checkpoint_ms','market_id']).reset_index(drop=True)
        df.to_csv(DATASET,index=False)
        counts=df['label'].value_counts().to_dict() if len(df) else {}
        manifest={
            'reportVersion':VERSION,'researchOnly':True,'runtimeTargetDataAllowed':False,
            'coverage':{'candidateMarkets':len(valid),'rows':len(df),'markets':int(df.market_id.nunique()) if len(df) else 0,'labels':counts,'dropped':dict(dropped),'microSnapshotsLoaded':len(mtimes)},
            'timeRange':{'minCheckpointMs':int(df.checkpoint_ms.min()) if len(df) else None,'maxCheckpointMs':int(df.checkpoint_ms.max()) if len(df) else None},
            'teacherLabel':'Target future anchored parent reaction in (fill+1s,fill+5s]: CONTINUE_SAME / SWITCH_OPPOSITE / PAUSE. Label is training-only.',
            'studentFeatureRule':'Only runtime-deployable public microstructure + equivalent own portfolio/lifecycle + own current fill price/book-derived complementary pair edge. Target identity/action is forbidden at inference.',
            'dataset':str(DATASET), 'featureSets':FEATURE_SETS,
        }
        MANIFEST.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        return manifest
    finally:
        book.close(); target.close(); micro.close()


def split_markets(df: pd.DataFrame) -> dict[str,set[int]]:
    x=df[['market_id','market_end_ms']].drop_duplicates().sort_values('market_end_ms')
    ms=[int(v) for v in x.market_id.tolist()]; n=len(ms); a=max(1,int(n*.70)); b=max(a+1,int(n*.85)); b=min(b,n)
    return {'train':set(ms[:a]),'validation':set(ms[a:b]),'test':set(ms[b:])}


def model_for(features:list[str]) -> ExplainableBoostingClassifier:
    return ExplainableBoostingClassifier(
        feature_names=features,max_bins=96,max_interaction_bins=48,interactions=6,
        outer_bags=6,learning_rate=.035,max_rounds=2200,early_stopping_rounds=100,
        min_samples_leaf=8,n_jobs=-2,random_state=20260819,
    )


def eval_model(model:ExplainableBoostingClassifier, part:pd.DataFrame, features:list[str]) -> dict[str,Any]:
    if part.empty:return {'n':0}
    y=part['label'].astype(str).tolist(); X=part[features]
    pred=model.predict(X); proba=model.predict_proba(X); labels=list(model.classes_)
    dist={c:int(sum(1 for z in pred if z==c)) for c in labels}; truth={c:int(sum(1 for z in y if z==c)) for c in labels}
    cm=confusion_matrix(y,pred,labels=labels).tolist()
    return {
        'n':len(y),'truthDistribution':truth,'predictedDistribution':dist,
        'accuracy':float(accuracy_score(y,pred)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),
        'macroF1':float(f1_score(y,pred,labels=labels,average='macro',zero_division=0)),
        'logLoss':float(log_loss(y,proba,labels=labels)),
        'confusionMatrix':{'labels':labels,'matrix':cm},
        'perClassRecall':{c:(cm[i][i]/sum(cm[i]) if sum(cm[i]) else None) for i,c in enumerate(labels)},
    }


def top_terms(model:ExplainableBoostingClassifier,n:int=20)->list[dict[str,Any]]:
    imp=list(model.term_importances()); names=list(model.term_names_); idx=sorted(range(len(imp)),key=lambda i:float(imp[i]),reverse=True)[:n]
    return [{'term':str(names[i]),'importance':float(imp[i])} for i in idx]


def train_one(name:str)->dict[str,Any]:
    if name not in FEATURE_SETS: raise SystemExit(f'unknown feature set {name}')
    df=pd.read_csv(DATASET); splits=split_markets(df); features=FEATURE_SETS[name]
    parts={k:df[df.market_id.astype(int).isin(v)].copy() for k,v in splits.items()}
    model=model_for(features); model.fit(parts['train'][features],parts['train']['label'].astype(str).tolist())
    art=OUT_DIR/f'model_{name.lower()}.joblib'
    joblib.dump({'version':VERSION,'researchOnly':True,'runtimeTargetDataAllowed':False,'featureSet':name,'features':features,'classes':list(model.classes_),'model':model},art)
    rep={'reportVersion':VERSION,'featureSet':name,'features':features,'splitMarkets':{k:len(v) for k,v in splits.items()},'train':eval_model(model,parts['train'],features),'validation':eval_model(model,parts['validation'],features),'test':eval_model(model,parts['test'],features),'topTerms':top_terms(model),'artifact':str(art)}
    rp=OUT_DIR/f'report_{name.lower()}.json'; rp.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    return rep


def consolidate()->dict[str,Any]:
    reports={}
    for name in FEATURE_SETS:
        p=OUT_DIR/f'report_{name.lower()}.json'
        if p.exists(): reports[name]=json.loads(p.read_text(encoding='utf-8'))
    out={'reportVersion':VERSION,'models':{k:{'validation':v['validation'],'test':v['test'],'topTerms':v['topTerms'][:12]} for k,v in reports.items()},'guard':['Teacher Target action appears only in historical training label.','Inference artifacts accept no Target identifier, future Target event, winner, or Target action input.','Chronological market split; no same-market leakage.','No hyperparameter sweep across feature sets; identical EBM capacity.']}
    COMPARISON.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); return out


def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--mode',choices=['build','train','consolidate'],required=True); ap.add_argument('--feature-set',choices=list(FEATURE_SETS)); args=ap.parse_args()
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    if args.mode=='build': print(json.dumps(build_dataset(),ensure_ascii=False,indent=2)); return 0
    if args.mode=='train':
        if not args.feature_set: raise SystemExit('--feature-set required')
        print(json.dumps(train_one(args.feature_set),ensure_ascii=False,indent=2)); return 0
    print(json.dumps(consolidate(),ensure_ascii=False,indent=2)); return 0

if __name__=='__main__': raise SystemExit(main())
