from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import mean_absolute_error, r2_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
R4 = ROOT / "data" / "research" / "r4_v0"
TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
OUR_DB = ROOT / "data" / "strategy_r3s_r31_echtgeld_v1.db"
SEED_MANIFEST = R4 / "r4_seed_from_r3_v113_manifest_v1.json"
VERSION = "R4_HOURLY_PREPARE_AND_TRAIN_V1"
TARGET_FEATURES = [
    "event_index_norm","floor_per_gross","pair_edge","coverage","absnet_ratio","cost_per_gross",
    "avg_up","avg_down","maker_share_frac_15s","taker_share_frac_15s","same_side_share_frac_15s",
    "opp_side_share_frac_15s","shares_15s_per_gross","floor_change_15s_per_gross","pair_edge_change_15s",
]
GAP_FEATURES = ["event_index_norm","floor_per_gross","pair_edge","coverage","absnet_ratio","cost_per_gross"]
SEALED_DATE_TPE = "2026-08-16"
FROZEN_LIVE_MARKETS = (1700601,1700655,1701123,1701140,1701356,1701359,1701523,1701531)
TZ = ZoneInfo("Asia/Taipei")


def ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("pragma query_only=on")
    return con


def finite(x, default=0.0):
    try:
        y=float(x)
        return y if math.isfinite(y) else default
    except Exception:
        return default


def ranks(x: np.ndarray) -> np.ndarray:
    order=np.argsort(x, kind="mergesort")
    r=np.empty(len(x),dtype=float)
    r[order]=np.arange(len(x),dtype=float)
    return r


def spearman(y, pred):
    y=np.asarray(y,float); pred=np.asarray(pred,float)
    if len(y)<3 or np.std(y)<1e-12 or np.std(pred)<1e-12: return None
    return float(np.corrcoef(ranks(y),ranks(pred))[0,1])


def geometry(up, down, cost):
    gross=up+down; absnet=abs(up-down); paired=min(up,down)
    floor=paired-cost
    avg_up=(cost*0) # placeholder; caller supplies side cost averages
    return gross,absnet,paired,floor


def load_target(max_markets: int):
    con=ro(TARGET_DB)
    meta=[]
    for r in con.execute("""
      select m.market_id,m.window_end_ms,m.winner,r.net_pnl_usdt,r.maker_net_pnl_usdt,r.taker_net_pnl_usdt,
             r.up_position_shares,r.down_position_shares,r.buy_notional_usdt
      from target_markets m join target_market_results r on r.market_id=m.market_id
      where m.asset='BTC' and m.window_end_ms is not null and r.fill_count>0
      order by m.window_end_ms desc limit ?
    """, (max_markets*2,)):
        dt=datetime.fromtimestamp(int(r['window_end_ms'])/1000,TZ).date().isoformat()
        if dt==SEALED_DATE_TPE: continue
        meta.append(dict(r))
        if len(meta)>=max_markets: break
    ids=[int(x['market_id']) for x in meta]
    ev=defaultdict(list)
    if ids:
        # One scan over parent-order table is faster and avoids repeated non-indexed wallet-event queries.
        q=','.join('?' for _ in ids)
        sql=f"select market_id,role,side,last_event_ms,average_price,shares from target_parent_orders where market_id in ({q}) and quote_type='BID'"
        for r in con.execute(sql,ids):
            px=finite(r['average_price'],float('nan')); sh=finite(r['shares'],0.0)
            if not math.isfinite(px) or sh<=0: continue
            role=str(r['role'] or '').upper(); side=str(r['side'] or '').upper()
            if role not in {'MAKER','TAKER'} or side not in {'UP','DOWN'}: continue
            ev[int(r['market_id'])].append({'t':int(r['last_event_ms']),'role':role,'side':side,'px':px,'sh':sh})
    con.close()
    rows=[]; by_market={}
    for m in sorted(meta,key=lambda z:int(z['window_end_ms'])):
        mid=int(m['market_id']); end=int(m['window_end_ms']); start=end-300_000
        events=sorted(ev.get(mid,[]),key=lambda z:z['t'])
        i=0; up=down=cu=cd=0.0; hist=[]; traj=[]
        for t in range(start+5_000,end+1,5_000):
            while i<len(events) and events[i]['t']<=t:
                z=events[i]; hist.append(z)
                if z['side']=='UP': up+=z['sh']; cu+=z['sh']*z['px']
                else: down+=z['sh']; cd+=z['sh']*z['px']
                i+=1
            gross=up+down; paired=min(up,down); absnet=abs(up-down); cost=cu+cd; floor=paired-cost
            avg_up=cu/up if up>1e-9 else 0.0; avg_down=cd/down if down>1e-9 else 0.0
            pair_edge=1.0-(avg_up+avg_down) if up>1e-9 and down>1e-9 else 0.0
            recent=[z for z in hist if t-z['t']<=15_000]
            rec_sh=sum(z['sh'] for z in recent)
            maker_sh=sum(z['sh'] for z in recent if z['role']=='MAKER')
            taker_sh=sum(z['sh'] for z in recent if z['role']=='TAKER')
            surplus='UP' if up>down else 'DOWN' if down>up else 'FLAT'
            same=sum(z['sh'] for z in recent if surplus!='FLAT' and z['side']==surplus)
            opp=sum(z['sh'] for z in recent if surplus!='FLAT' and z['side']!=surplus)
            prev=traj[-3] if len(traj)>=3 else None
            row={
                'market_id':mid,'window_end_ms':end,'checkpoint_ms':t,'winner':m.get('winner'),
                'event_index_norm':max(0.0,min(1.0,(t-start)/300_000)),
                'floor':floor,'floor_per_gross':floor/gross if gross>1e-9 else 0.0,
                'pair_edge':pair_edge,'coverage':2*paired/gross if gross>1e-9 else 0.0,
                'absnet_ratio':absnet/gross if gross>1e-9 else 0.0,'cost_per_gross':cost/gross if gross>1e-9 else 0.0,
                'avg_up':avg_up,'avg_down':avg_down,
                'maker_share_frac_15s':maker_sh/rec_sh if rec_sh>1e-9 else 0.0,
                'taker_share_frac_15s':taker_sh/rec_sh if rec_sh>1e-9 else 0.0,
                'same_side_share_frac_15s':same/rec_sh if rec_sh>1e-9 else 0.0,
                'opp_side_share_frac_15s':opp/rec_sh if rec_sh>1e-9 else 0.0,
                'shares_15s_per_gross':rec_sh/gross if gross>1e-9 else 0.0,
                'floor_change_15s_per_gross':(floor-prev['floor'])/gross if prev and gross>1e-9 else 0.0,
                'pair_edge_change_15s':pair_edge-prev['pair_edge'] if prev else 0.0,
                'gross':gross,'absnet':absnet,'cost':cost,'up':up,'down':down,
            }
            traj.append(row)
        # teacher-only future labels; never candidate runtime input.
        for j,row in enumerate(traj):
            f30=traj[j:min(len(traj),j+7)]
            f60=traj[j:min(len(traj),j+13)]
            row['future_peak_floor_per_gross_60s']=max((x['floor_per_gross'] for x in f60),default=row['floor_per_gross'])
            row['future_min_floor_per_gross_30s']=min((x['floor_per_gross'] for x in f30),default=row['floor_per_gross'])
            row['future_pair_edge_30s']=f30[-1]['pair_edge'] if f30 else row['pair_edge']
        by_market[mid]=traj; rows.extend(traj)
    return meta,rows,by_market


def train_regression(rows, label, predicate, version, out_path):
    use=[r for r in rows if r['gross']>1e-9 and predicate(r)]
    markets=sorted(set(int(r['market_id']) for r in use))
    cut=max(1,int(len(markets)*0.8)); trm=set(markets[:cut]); tem=set(markets[cut:])
    train=[r for r in use if int(r['market_id']) in trm]; test=[r for r in use if int(r['market_id']) in tem]
    X=np.asarray([[finite(r.get(f)) for f in TARGET_FEATURES] for r in train],float); y=np.asarray([finite(r[label]) for r in train],float)
    model=HistGradientBoostingRegressor(max_iter=180,max_leaf_nodes=15,l2_regularization=1.0,learning_rate=0.06,random_state=42)
    model.fit(X,y)
    Xt=np.asarray([[finite(r.get(f)) for f in TARGET_FEATURES] for r in test],float); yt=np.asarray([finite(r[label]) for r in test],float)
    pred=model.predict(Xt) if len(test) else np.asarray([])
    report={'version':version,'label':label,'features':TARGET_FEATURES,'trainMarkets':len(trm),'testMarkets':len(tem),'trainRows':len(train),'testRows':len(test),
            'testMAE':float(mean_absolute_error(yt,pred)) if len(test) else None,'testR2':float(r2_score(yt,pred)) if len(test)>1 else None,'testSpearman':spearman(yt,pred) if len(test) else None,
            'chronologicalSplit':True,'teacherFutureLabelsRuntimeForbidden':True}
    joblib.dump({'version':version,'features':TARGET_FEATURES,'label':label,'model':model,'report':report},out_path)
    return report


def load_live_rows():
    # Echtgeld is a frozen execution-environment calibration cohort, not an expanding training source.
    # New live markets are ignored unless the user explicitly reopens R4 live-data collection.
    con=ro(OUR_DB); raw=con.execute('select market_id,decision_ms,seconds_left,portfolio_state_json,payload_json from our_decisions order by decision_ms').fetchall(); con.close()
    grouped=defaultdict(list)
    for r in raw:
        try: payload=json.loads(r['payload_json'] or '{}')
        except Exception: payload={}
        if not bool(payload.get('liveOrdersAffected')): continue
        ver=str(payload.get('version') or '')
        if not ('R3S_R31_V1_1_2' in ver or 'R3S_R31_V1_1_3' in ver): continue
        mid=int(r['market_id'])
        if mid not in FROZEN_LIVE_MARKETS: continue
        sec=finite(r['seconds_left'],300.0)
        try:p=json.loads(r['portfolio_state_json'] or '{}')
        except Exception:p={}
        gross=finite(p.get('combined_gross')); absnet=finite(p.get('combined_abs_net')); floor=finite(p.get('worst_case_floor'))
        paired=max(0.0,(gross-absnet)/2); cost=max(0.0,paired-floor)
        pair_edge=finite(p.get('combined_avg_pair_edge'),0.0)
        grouped[mid].append({'market_id':mid,'checkpoint_ms':int(r['decision_ms']),'event_index_norm':max(0,min(1,1-sec/300)),
            'floor':floor,'gross':gross,'floor_per_gross':floor/gross if gross>1e-9 else 0.0,'pair_edge':pair_edge,
            'coverage':finite(p.get('combined_paired_coverage'),2*paired/gross if gross>1e-9 else 0.0),
            'absnet_ratio':absnet/gross if gross>1e-9 else 0.0,'cost_per_gross':cost/gross if gross>1e-9 else 0.0,
            'absnet':absnet,'version':ver})
    # Remove sparse/no-order pseudo-live fragments and known invalid pre-full-stack incident.
    return {m:v for m,v in grouped.items() if len(v)>=50 and m!=1698252}


def train_gap(live, target_by):
    mids=sorted(set(live)&set(target_by)); rows=[]
    for mid in mids:
        # downsample OUR to ~5s to make source balance less dependent on decision frequency
        lv=live[mid]; last=-10**18
        for r in lv:
            if r['checkpoint_ms']-last<4500: continue
            rows.append((mid,0,r)); last=r['checkpoint_ms']
        for r in target_by[mid]: rows.append((mid,1,r))
    X=np.asarray([[finite(r.get(f)) for f in GAP_FEATURES] for _,_,r in rows],float); y=np.asarray([lab for _,lab,_ in rows],int); groups=np.asarray([m for m,_,_ in rows])
    oof=np.full(len(rows),np.nan)
    for mid in mids:
        tr=groups!=mid; te=groups==mid
        if len(set(y[tr]))<2 or not np.any(te): continue
        model=make_pipeline(StandardScaler(),LogisticRegression(max_iter=1000,class_weight='balanced',random_state=42))
        model.fit(X[tr],y[tr]); oof[te]=model.predict_proba(X[te])[:,1]
    good=np.isfinite(oof)
    auc=float(roc_auc_score(y[good],oof[good])) if good.sum()>10 and len(set(y[good]))==2 else None
    full=make_pipeline(StandardScaler(),LogisticRegression(max_iter=1000,class_weight='balanced',random_state=42)); full.fit(X,y)
    coef=full.named_steps['logisticregression'].coef_[0]
    coeffs=sorted([{'feature':f,'targetLikeCoefficient':float(c),'abs':abs(float(c))} for f,c in zip(GAP_FEATURES,coef)],key=lambda z:z['abs'],reverse=True)
    med={}
    for f in GAP_FEATURES:
        med[f]={'ourMedian':float(np.median([finite(r.get(f)) for _,lab,r in rows if lab==0])),'targetMedian':float(np.median([finite(r.get(f)) for _,lab,r in rows if lab==1]))}
    version='R4_TARGET_OUR_MATCHED_GAP_DISCRIMINATOR_V1'
    out=R4/'r4_target_our_gap_discriminator_v1.joblib'; joblib.dump({'version':version,'features':GAP_FEATURES,'model':full,'matchedMarkets':mids},out)
    return {'version':version,'matchedMarkets':mids,'marketCount':len(mids),'rows':len(rows),'leaveOneMarketOutAUC':auc,'coefficients':coeffs,'medians':med,'interpretation':'DIAGNOSTIC_GAP_ONLY_NOT_ACTION_AUTHORITY'}


def live_summary(live):
    out=[]
    for mid,rows in sorted(live.items()):
        floors=[r['floor'] for r in rows]; pos=[r for r in rows if r['floor']>0]
        out.append({'marketId':mid,'version':rows[-1]['version'],'checkpoints':len(rows),'maxFloor':max(floors),'finalFloor':floors[-1],
                    'everPositive':bool(pos),'positiveCheckpointShare':len(pos)/len(rows),'finalPairEdge':rows[-1]['pair_edge'],'finalAbsNet':rows[-1]['absnet'],'finalCoverage':rows[-1]['coverage'],
                    'failureClass':'BASE_PRESERVATION_FAILURE' if pos and floors[-1]<0 else 'BASE_ACQUISITION_FAILURE' if not pos and floors[-1]<0 else 'BASE_HELD'})
    return out


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--target-markets',type=int,default=500); args=ap.parse_args()
    R4.mkdir(parents=True,exist_ok=True); (R4/'hourly').mkdir(exist_ok=True)
    seed=json.loads(SEED_MANIFEST.read_text(encoding='utf-8'))
    meta,target_rows,target_by=load_target(args.target_markets)
    acq=train_regression(target_rows,'future_peak_floor_per_gross_60s',lambda r:r['floor']<=0,'R4_BASE_ACQUISITION_TEACHER_V1',R4/'r4_base_acquisition_teacher_v1.joblib')
    pre=train_regression(target_rows,'future_min_floor_per_gross_30s',lambda r:r['floor']>0,'R4_BASE_PRESERVATION_TEACHER_V1',R4/'r4_base_preservation_teacher_v1.joblib')
    live=load_live_rows(); gap=train_gap(live,target_by); ls=live_summary(live)
    now=datetime.now(TZ); stamp=now.strftime('%Y%m%d_%H%M%S')
    report={'version':VERSION,'createdAt':now.isoformat(),'seedVersion':seed['sourceLiveVersion'],'target':{'markets':len(meta),'checkpoints':len(target_rows),'sealedDateExcluded':SEALED_DATE_TPE},
            'acquisitionTeacher':acq,'preservationTeacher':pre,'liveCalibration':{'markets':ls,'marketCount':len(ls),'acquisitionFailures':sum(x['failureClass']=='BASE_ACQUISITION_FAILURE' for x in ls),'preservationFailures':sum(x['failureClass']=='BASE_PRESERVATION_FAILURE' for x in ls),'baseHeld':sum(x['failureClass']=='BASE_HELD' for x in ls)},
            'matchedGap':gap,'executionEnvironmentCalibration':{'mode':'FROZEN_EXISTING_ECHTGELD_ONLY','marketIds':list(FROZEN_LIVE_MARKETS),'purpose':'ANALYZE_REAL_VENUE_VS_RESEARCH_EXECUTION_MISMATCH_NOT_ACTION_TUNING','autoExpand':False},'guards':{'r4LiveAuthority':False,'collectNewEchtgeldForR4':False,'autoIngestNewEchtgeld':False,'winnerOrFutureAtRuntime':False,'targetFutureLabelsTeacherOnly':True,'dreamFillOfficialEvidence':False,'special20260816Sealed':True}}
    latest=R4/'hourly/r4_hourly_training_snapshot_latest.json'; hist=R4/f'hourly/r4_hourly_training_{stamp}.json'
    txt=json.dumps(report,indent=2,allow_nan=False); latest.write_text(txt,encoding='utf-8'); hist.write_text(txt,encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(hist.relative_to(ROOT)).replace('\\','/'),'latest':str(latest.relative_to(ROOT)).replace('\\','/'),'targetMarkets':len(meta),'liveMarkets':len(ls),'matchedMarkets':gap['marketCount'],'acqSpearman':acq['testSpearman'],'presSpearman':pre['testSpearman'],'gapAUC':gap['leaveOneMarketOutAUC']},ensure_ascii=False))

if __name__=='__main__': main()
