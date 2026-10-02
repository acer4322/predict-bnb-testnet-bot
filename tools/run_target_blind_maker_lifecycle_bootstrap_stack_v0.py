from __future__ import annotations

import bisect
import importlib.util
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'research' / 'target_maker_taker_coordination_big_v1'
SUFFIX = os.environ.get('BOOT_SUFFIX','')
STACK_MODE = os.environ.get('BOOT_STACK_MODE','NONE').upper()
STACK_GUARD = os.environ.get('BOOT_STACK_GUARD','NONE').upper()
REPORT = OUT / f'target_blind_maker_lifecycle_bootstrap_stack_v0{SUFFIX}_report.json'
STATES = OUT / f'target_blind_maker_lifecycle_bootstrap_stack_v0{SUFFIX}_states.csv'
MARKETS = OUT / f'target_blind_maker_lifecycle_bootstrap_stack_v0{SUFFIX}_markets.csv'
EVENT_EVAL = OUT / f'target_blind_maker_lifecycle_bootstrap_stack_v0{SUFFIX}_target_event_eval.csv'
GEN_PLACES = OUT / f'target_blind_maker_lifecycle_bootstrap_stack_v0{SUFFIX}_placements.csv'
GEN_FILLS = OUT / f'target_blind_maker_lifecycle_bootstrap_stack_v0{SUFFIX}_fills.csv'
GENERAL_DATA = OUT / 'target_general_maker_side_hazard_v1.csv'
UP_ART = Path(os.environ.get('BOOT_UP_ART', str(OUT / 'target_general_maker_up_hazard_v1.joblib')))
DOWN_ART = Path(os.environ.get('BOOT_DOWN_ART', str(OUT / 'target_general_maker_down_hazard_v1.joblib')))
TAKER_CONTRACT = OUT / 'forward_contract_v1.json'
HIST_T = OUT / 'taker_event_states_v1.csv'
FRESH_T = OUT / 'forward_taker_states_v1.csv'
BOOK_DB = ROOT / 'data' / 'wallet_maker_book_inference.db'
TARGET_DB = ROOT / 'data' / 'target_wallet_official_v1.db'
SHARES = 18.0
RNG_SEED = 20260820
EPS = 1e-9

P = ROOT / 'tools' / 'bridge_target_reentry_hazard_to_our_open_counterfactual_v0.py'
spec = importlib.util.spec_from_file_location('maker_boot_bridge', P)
br = importlib.util.module_from_spec(spec); assert spec and spec.loader
sys.modules[spec.name] = br; spec.loader.exec_module(br)
base = br.base; mod = br.mod; coord = br.coord


def ro(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f'file:{path.resolve().as_posix()}?mode=ro', uri=True, timeout=30)
    c.row_factory = sqlite3.Row; c.execute('pragma query_only=on'); return c


def fast_prob(artifact: dict[str, Any]):
    model = artifact['model']; features = list(artifact['features'])
    kb, fm = model._bin_mapper.make_known_categories_bitsets()
    trees = [it[0] for it in model._predictors]
    b0 = float(model._baseline_prediction[0, 0])
    def pred(raw: dict[str, Any]) -> float:
        x = np.asarray([[float(raw.get(f, math.nan)) if raw.get(f) is not None else math.nan for f in features]], dtype=float)
        z = b0
        for tree in trees:
            z += float(tree.predict(x, known_cat_bitsets=kb, f_idx_map=fm, n_threads=1)[0])
        if z >= 0: return 1.0 / (1.0 + math.exp(-z))
        ez = math.exp(z); return ez / (1.0 + ez)
    return pred, features


def numeric_row(raw: dict[str, Any], fs: list[str]) -> np.ndarray:
    return np.asarray([[float(raw.get(f, math.nan)) if raw.get(f) is not None else math.nan for f in fs]], dtype=float)


def endmap(book: sqlite3.Connection) -> dict[int, int]:
    out = {}
    for r in book.execute('select market_id,window_end_ms from maker_book_inference_markets where window_end_ms is not null order by market_id'):
        out.setdefault(int(r['window_end_ms']), int(r['market_id']))
    return out


def teacher_events() -> pd.DataFrame:
    parts = []
    for p in (HIST_T, FRESH_T):
        if p.exists(): parts.append(pd.read_csv(p))
    return pd.concat(parts, ignore_index=True, sort=False).drop_duplicates('parent_id', keep='last') if parts else pd.DataFrame()


def placement_features(places: list[dict[str, Any]], cp: int) -> dict[str, float]:
    age_keys = ('last_place_age_ms', 'last_up_place_age_ms', 'last_down_place_age_ms')
    if not places:
        return {**{k: math.nan for k in age_keys}, 'placements_1s':0.0,'placements_5s':0.0,'placements_10s':0.0,
                'up_placements_5s':0.0,'down_placements_5s':0.0,'up_placements_10s':0.0,'down_placements_10s':0.0,
                'placement_side_balance_5s':0.0,'placement_side_balance_10s':0.0,'placement_side_streak':0.0}
    times = [int(x['at_ms']) for x in places]
    i = bisect.bisect_right(times, cp) - 1
    if i < 0: return placement_features([], cp)
    last = places[i]; lu = ld = None; streak = 0; ls = str(last['side'])
    for j in range(i, -1, -1):
        x = places[j]; s = str(x['side']); t = int(x['at_ms'])
        if s == 'UP' and lu is None: lu = t
        if s == 'DOWN' and ld is None: ld = t
        if s == ls: streak += 1
        elif j < i: break
    def cnt(w: int) -> tuple[int,int,int]:
        lo = bisect.bisect_right(times, cp-w); xs = places[lo:i+1]
        u = sum(str(x['side']) == 'UP' for x in xs); d = len(xs)-u; return len(xs),u,d
    n1,u1,d1 = cnt(1000); n5,u5,d5 = cnt(5000); n10,u10,d10 = cnt(10000)
    def bal(u,d): return (u-d)/(u+d) if u+d else 0.0
    return {'last_place_age_ms':float(cp-int(last['at_ms'])),'last_up_place_age_ms':float(cp-lu) if lu is not None else math.nan,
            'last_down_place_age_ms':float(cp-ld) if ld is not None else math.nan,'placements_1s':float(n1),'placements_5s':float(n5),
            'placements_10s':float(n10),'up_placements_5s':float(u5),'down_placements_5s':float(d5),'up_placements_10s':float(u10),
            'down_placements_10s':float(d10),'placement_side_balance_5s':bal(u5,d5),'placement_side_balance_10s':bal(u10,d10),
            'placement_side_streak':float(streak)}


def quote_row(snapshot: dict[str, Any], side: str, active_opp: Any | None) -> dict[str, Any] | None:
    tick = mod.maker_ebm._quote_tick(snapshot, side, 1)
    if tick is None: return None
    tick = int(tick); price = round(tick * mod.maker_ebm.GRID, 2)
    if active_opp is not None:
        while price + float(active_opp.price) > mod.maker_ebm.MAX_PAIR_PRICE_SUM + 1e-9:
            tick -= 1
            if tick < int(round(mod.maker_ebm.MIN_PRICE / mod.maker_ebm.GRID)): return None
            price = round(tick * mod.maker_ebm.GRID, 2)
    return {'side':side,'priceTick':tick,'price':price,'shares':SHARES,'offsetTicks':1,'origin':'TARGET_BLIND_MAKER_LIFECYCLE_BOOTSTRAP_V0'}


def place_if_absent(sim: Any, snapshot: dict[str, Any], side: str, ns: int, now: int, place_log: list[dict[str, Any]], market_id: int, p: float) -> bool:
    same = [o for o in sim.orders.values() if o.side == side]
    opp = 'DOWN' if side == 'UP' else 'UP'
    opp_orders = [o for o in sim.orders.values() if o.side == opp]
    occupied_before = bool(same)
    if same:
        if STACK_MODE == 'NONE' or len(same) >= 2:
            return False
        latest = max(same, key=lambda o:int(o.placed_at_ms))
        latest_age = now - int(latest.placed_at_ms)
        sec = mod.snapshot_value(snapshot, 'seconds_left', 'secondsLeft')
        vol = str(snapshot.get('volatilityAlert') or snapshot.get('volatility_alert') or 'NORMAL').upper()
        if STACK_MODE == 'CONTEXT':
            context_on = latest_age < 1500 or (sec is not None and 15.0 < float(sec) <= 60.0) or vol in ('WATCH','HIGH')
            if not context_on:
                return False
        elif STACK_MODE != 'ALWAYS':
            return False
        if STACK_GUARD in ('PAIR80','PAIR80_ESCAPE','PAIR80_HEADWIND'):
            maker_up = sum(float(f['shares']) for f in sim.maker_fills if str(f['side']) == 'UP')
            maker_dn = sum(float(f['shares']) for f in sim.maker_fills if str(f['side']) == 'DOWN')
            gross = maker_up + maker_dn
            net = maker_up - maker_dn
            dom = 'UP' if net > EPS else 'DOWN' if net < -EPS else None
            paired = 2.0 * min(maker_up, maker_dn) / gross if gross > EPS else 1.0
            if dom == side and paired < 0.80:
                escape = False
                if STACK_GUARD == 'PAIR80_ESCAPE':
                    bid = mod.snapshot_value(snapshot, 'predict_up_bid' if side=='UP' else 'predict_down_bid', 'predictUpBid' if side=='UP' else 'predictDownBid')
                    ahead_inside = bid is not None and float(latest.price) >= float(bid) + mod.maker_ebm.GRID - 1e-9
                    escape = latest_age < 500 or ahead_inside
                elif STACK_GUARD == 'PAIR80_HEADWIND':
                    bias = str(snapshot.get('directionBias') or snapshot.get('direction_bias') or 'NEUTRAL').upper()
                    escape = bias in ('UP','DOWN') and bias != side
                if not escape:
                    return False
        elif STACK_GUARD == 'OPP_LIVE':
            if not opp_orders:
                return False
        elif STACK_GUARD != 'NONE':
            raise ValueError(f'unsupported STACK_GUARD={STACK_GUARD}')
    row = quote_row(snapshot, side, max(opp_orders,key=lambda o:float(o.price)) if opp_orders else None)
    if row is None: return False
    tick = int(row['priceTick']); price=float(row['price'])
    # Multi-level proxy: never overwrite an existing same-side price level. Walk deeper until free.
    while (side,tick) in sim.orders:
        tick -= 1
        if tick < int(round(mod.maker_ebm.MIN_PRICE/mod.maker_ebm.GRID)):
            return False
        price = round(tick*mod.maker_ebm.GRID,2)
    if opp_orders:
        max_opp=max(float(o.price) for o in opp_orders)
        while price + max_opp > mod.maker_ebm.MAX_PAIR_PRICE_SUM + 1e-9:
            tick -= 1
            if tick < int(round(mod.maker_ebm.MIN_PRICE/mod.maker_ebm.GRID)):
                return False
            price=round(tick*mod.maker_ebm.GRID,2)
    key=(side,tick)
    if now - int(sim.last_closed.get(key, 0)) < mod.maker_ebm.REFILL_COOLDOWN_MS: return False
    order = mod.SimOrder(key=key, side=side, price_tick=tick, price=price, shares=SHARES, placed_at_ms=now, placed_snapshot_ns=ns)
    sim.orders[key]=order; sim.placements+=1
    place_log.append({'market_id':market_id,'at_ms':now,'side':side,'price':price,'shares':SHARES,'p_hazard':p,'occupied_before':int(occupied_before),'stack_mode':STACK_MODE,'active_same_before':len(same)})
    return True


def multi(y: list[str], p: list[str]) -> dict[str, Any]:
    if not y: return {'n':0}
    return {'n':len(y),'accuracy':float(accuracy_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,p)),
            'macroF1':float(f1_score(y,p,average='macro',zero_division=0)),
            'truthDistribution':pd.Series(y).value_counts().to_dict(),'predictedDistribution':pd.Series(p).value_counts().to_dict()}


def stats(xs: list[float]) -> dict[str, Any]:
    s = pd.Series([float(x) for x in xs if x is not None and math.isfinite(float(x))], dtype=float)
    if s.empty: return {'n':0}
    return {'n':int(len(s)),'mean':float(s.mean()),'median':float(s.median()),'p25':float(s.quantile(.25)),'p75':float(s.quantile(.75)),
            'min':float(s.min()),'max':float(s.max())}


def main() -> int:
    up_art = joblib.load(UP_ART); down_art = joblib.load(DOWN_ART)
    p_up, up_fs = fast_prob(up_art); p_down, down_fs = fast_prob(down_art)
    g = pd.read_csv(GENERAL_DATA, usecols=['market_id','market_end_ms'])
    split = coord.split_markets(g); train_ids = set(map(int, split['train']))
    train_max_end = int(g[g.market_id.astype(int).isin(train_ids)].market_end_ms.max())
    contract = json.loads(TAKER_CONTRACT.read_text(encoding='utf-8'))
    taker_h = joblib.load(contract['artifacts']['hazard_1s']); taker_s = joblib.load(contract['artifacts']['side']); taker_e = joblib.load(contract['artifacts']['effect'])
    thfs=list(taker_h['features']); tsfs=list(taker_s['features']); tefs=list(taker_e['features'])
    fast_taker, _ = fast_prob(taker_h)

    our = mod.ro(mod.DEFAULT_OUR_DB); book = ro(BOOK_DB); target = ro(TARGET_DB)
    rng = np.random.default_rng(RNG_SEED)
    try:
        snaps = mod.load_snapshots(our); seeds = mod.load_seeds(our); models = mod.maker_ebm.load_models(); emap = endmap(book)
        rows=[]; market_rows=[]; places=[]; fills=[]; drop=defaultdict(int)
        target_maker_counts={int(r['market_id']):int(r['n']) for r in target.execute("select market_id,count(*) n from target_parent_orders where asset='BTC' and role='MAKER' and quote_type='BID' group by market_id")}
        eligible=[]
        for om in sorted(snaps):
            items0=snaps[om]
            if not items0: continue
            ws0=int(mod.snapshot_value(dict(items0[0]['snapshot']),'window_end_ms','windowEndMs') or 0)
            if ws0>train_max_end and emap.get(ws0) is not None: eligible.append(om)
        start=int(os.environ.get('BOOT_START','0') or 0); count=int(os.environ.get('BOOT_COUNT','0') or 0)
        chosen=eligible[start:(start+count if count>0 else None)]
        for mi, om in enumerate(chosen,1):
            items=snaps[om]
            ws=int(mod.snapshot_value(dict(items[0]['snapshot']),'window_end_ms','windowEndMs') or 0)
            tm=emap.get(ws)
            if tm is None: continue
            ups=list(book.execute('select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(tm,)))
            ui=0; state={'bids':{},'asks':{}}; last=None; sim=mod.Simulator(models,'CUSTOM'); inv=coord.Inventory(); sd=list(seeds.get(om,[])); si=0
            mplaces=[]; mfills=[]; p_up_sum=0.0; p_dn_sum=0.0; eval_states=0; intents_up=intents_dn=0
            for it in items:
                now=int(it['decision_ms']); snap=dict(it['snapshot']); ns=int(mod.num(snap.get('timestampNs')) or mod.num(snap.get('timestamp_ns')) or now*1_000_000)
                while ui<len(ups) and int(ups[ui]['source_timestamp_ms'])<=now:
                    u=ups[ui]
                    if int(u['is_checkpoint']): state={'bids':{float(k):float(v) for k,v in (coord.dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (coord.dec(u['native_asks_z']) or {}).items()}}
                    else: coord.apply_changes(state,coord.dec(u['changes_z']) or {})
                    last=int(u['source_timestamp_ms']); ui+=1
                before=len(sim.maker_fills); sim.fill_existing(snap,ns,now)
                for f in sim.maker_fills[before:]:
                    e={'event_ms':int(f['at_ms']),'role':'MAKER','side':str(f['side']),'price':float(f['price']),'shares':float(f['shares'])}; inv.apply(e)
                    q={'market_id':om,'target_market_id':tm,'window_end_ms':ws,'at_ms':int(f['at_ms']),'side':str(f['side']),'price':float(f['price']),'shares':float(f['shares'])}; fills.append(q); mfills.append(q)
                seed_now=False
                while si<len(sd) and int(sd[si]['filled_at_ms'])<=now:
                    x=sd[si]; sim.apply_seed(x); inv.apply({'event_ms':int(x['filled_at_ms']),'role':'TAKER','side':str(x['side']),'price':float(x['price']),'shares':float(x['shares'])}); si+=1; seed_now=True
                age=now-last if last is not None else 10**9
                if not (0<=age<=2000): drop['book_stale']+=1; continue
                feat=inv.features(now); cn=float(feat.pop('_combined_net')); dom='UP' if cn>EPS else 'DOWN' if cn<-EPS else None; bf=coord.outcome_book(state,dom)
                if bf is None: drop['empty_book']+=1; continue
                pf=placement_features(mplaces,now); raw={'seconds_left':(ws-now)/1000.0,**feat,**bf,**pf}
                pu=float(p_up(raw)); pdn=float(p_down(raw)); p_up_sum+=pu; p_dn_sum+=pdn; eval_states+=1
                trig_up=bool(rng.random()<pu); trig_dn=bool(rng.random()<pdn); intents_up+=int(trig_up); intents_dn+=int(trig_dn)
                # Preserve resting orders. Hazard creates a new one only when that side has no live order.
                if not seed_now:
                    if trig_up: place_if_absent(sim,snap,'UP',ns,now,mplaces,om,pu)
                    if trig_dn: place_if_absent(sim,snap,'DOWN',ns,now,mplaces,om,pdn)
                # mirror just-created placements into global log with market mapping
                while len(places) < sum(int(x.get('_global_marker',0)) for x in []): pass
                # store new mplaces globally lazily after market
                # Compute frozen Taker hazard on the Maker-bootstrapped own state.
                t_raw={'seconds_left':(ws-now)/1000.0,**feat,**bf}
                pt=float(fast_taker(t_raw))
                rows.append({'our_market_id':om,'target_market_id':tm,'window_end_ms':ws,'decision_ms':now,'p_maker_up_1s':pu,'p_maker_down_1s':pdn,
                             'p_taker_1s':pt,'active_up_order':int(any(o.side=='UP' for o in sim.orders.values())),'active_down_order':int(any(o.side=='DOWN' for o in sim.orders.values())),
                             **t_raw,**pf})
            # append market placement log with mapping (mplaces contains local generic keys)
            for x in mplaces: places.append({'market_id':om,'target_market_id':tm,'window_end_ms':ws,**x})
            maker_up=sum(float(x['shares']) for x in mfills if x['side']=='UP'); maker_dn=sum(float(x['shares']) for x in mfills if x['side']=='DOWN')
            gross=maker_up+maker_dn; final_abs=abs(maker_up-maker_dn)
            market_rows.append({'ourMarketId':om,'targetMarketId':tm,'windowEndMs':ws,'evalStates':eval_states,'sumPMakerUp':p_up_sum,'sumPMakerDown':p_dn_sum,
                                'makerIntentUp':intents_up,'makerIntentDown':intents_dn,'generatedPlacements':len(mplaces),'generatedMakerFills':len(mfills),
                                'targetMakerParents':target_maker_counts.get(tm,0),'makerGrossShares':gross,'finalMakerAbsNet':final_abs,
                                'makerPairedCoverage':(2*min(maker_up,maker_dn)/gross if gross>EPS else 0.0)})
            if mi%40==0: print(json.dumps({'progressMarkets':mi,'states':len(rows),'placements':len(places),'fills':len(fills)}),flush=True)
        sdf=pd.DataFrame(rows).sort_values(['window_end_ms','decision_ms']); mdf=pd.DataFrame(market_rows).sort_values('windowEndMs'); pdf=pd.DataFrame(places); fdf=pd.DataFrame(fills)
        sdf.to_csv(STATES,index=False); mdf.to_csv(MARKETS,index=False); pdf.to_csv(GEN_PLACES,index=False); fdf.to_csv(GEN_FILLS,index=False)

        # Evaluate frozen Taker SIDE/EFFECT at true Target Taker times using Maker-bootstrapped OUR state.
        teacher=teacher_events(); groups={int(m):q.sort_values('decision_ms') for m,q in sdf.groupby('target_market_id')}; evrows=[]
        for _,e in teacher.iterrows():
            tm=int(e['market_id']); q=groups.get(tm)
            if q is None or q.empty: continue
            ts=q.decision_ms.astype('int64').to_numpy(); t=int(e['checkpoint_ms']); j=int(np.searchsorted(ts,t,side='right')-1)
            if j<0 or t-int(ts[j])>2000: continue
            r=q.iloc[j]; raw={f:r.get(f,math.nan) for f in set(thfs+tsfs+tefs)}
            sp=str(taker_s['model'].predict(numeric_row(raw,tsfs))[0]); ep=str(taker_e['model'].predict(numeric_row(raw,tefs))[0])
            evrows.append({'targetMarketId':tm,'targetParentId':str(e['parent_id']),'eventMs':t,'ourStateMs':int(ts[j]),'truthSide':str(e['label_side']),
                           'predSide':sp,'truthEffect':str(e['label_effect']),'predEffect':ep,'pTaker1s':float(r.p_taker_1s),
                           'lastMakerAgeMs':r.get('last_maker_age_ms'),'makerFills5s':r.get('maker_fills_5s'),'makerGross':r.get('maker_gross'),
                           'makerPairedCoverage':r.get('maker_paired_coverage')})
        edf=pd.DataFrame(evrows); edf.to_csv(EVENT_EVAL,index=False)

        # Target teacher-state reference at the same true Taker timestamps.
        target_ref=teacher.copy()
        rep={'reportVersion':'TARGET_BLIND_MAKER_LIFECYCLE_BOOTSTRAP_STACK_V0','researchOnly':True,'runtimeTargetDataAllowed':False,
             'question':'Can frozen all-market Target UP/DOWN Maker placement hazards bootstrap OUR own passive lifecycle toward the Target state distribution, before adding self-generated Taker activity?',
             'policy':{'makerHazards':'independent Bernoulli samples from frozen Target general UP/DOWN next-1s placement hazards','sizeShares':SHARES,
                       'price':'best-bid -1tick execution proxy with pair cap','resting':f'stable resting; STACK_MODE={STACK_MODE}; max two price levels per side; no timer refresh/cancel chasing',
                       'taker':'only existing recorded OUR OPEN_SEED; no generated Taker yet','rngSeed':RNG_SEED,'stackMode':STACK_MODE,'stackGuard':STACK_GUARD},
             'trainingBoundary':{'generalMakerHazardTrainMaxMarketEndMs':train_max_end,'allBootstrapMarketsAfterTraining':bool(len(mdf) and int(mdf.windowEndMs.min())>train_max_end)},
             'chunk':{'eligibleMarkets':len(eligible),'start':start,'countRequested':count,'chosenMarkets':len(chosen),'suffix':SUFFIX},
             'coverage':{'markets':len(mdf),'states':len(sdf),'generatedPlacements':len(pdf),'generatedMakerFills':len(fdf),'trueTargetEventComparisons':len(edf),'dropped':dict(drop)},
             'makerActivity':{'generatedPlacementsPerMarket':stats(mdf.generatedPlacements.tolist()),'generatedMakerFillsPerMarket':stats(mdf.generatedMakerFills.tolist()),
                              'targetMakerParentsPerMarket':stats(mdf.targetMakerParents.tolist()),'makerGrossSharesPerMarket':stats(mdf.makerGrossShares.tolist()),
                              'finalMakerAbsNet':stats(mdf.finalMakerAbsNet.tolist()),'makerPairedCoverage':stats(mdf.makerPairedCoverage.tolist())},
             'takerStudentTransferAfterMakerBootstrap':{'side':multi(edf.truthSide.astype(str).tolist(),edf.predSide.astype(str).tolist()) if len(edf) else None,
                                                        'effect':multi(edf.truthEffect.astype(str).tolist(),edf.predEffect.astype(str).tolist()) if len(edf) else None,
                                                        'meanP1AtTrueTaker':float(edf.pTaker1s.mean()) if len(edf) else None,'medianP1AtTrueTaker':float(edf.pTaker1s.median()) if len(edf) else None},
             'ownLifecycleAtTrueTaker':{'lastMakerAgeMs':stats(edf.lastMakerAgeMs.tolist()) if len(edf) else None,'makerFills5s':stats(edf.makerFills5s.tolist()) if len(edf) else None,
                                       'makerGross':stats(edf.makerGross.tolist()) if len(edf) else None,'makerPairedCoverage':stats(edf.makerPairedCoverage.tolist()) if len(edf) else None},
             'targetTeacherReference':{'lastMakerAgeMedianMs':float(pd.to_numeric(target_ref.get('last_maker_age_ms'),errors='coerce').median()) if 'last_maker_age_ms' in target_ref else None,
                                       'makerFills5sMedian':float(pd.to_numeric(target_ref.get('maker_fills_5s'),errors='coerce').median()) if 'maker_fills_5s' in target_ref else None,
                                       'makerGrossMedian':float(pd.to_numeric(target_ref.get('maker_gross'),errors='coerce').median()) if 'maker_gross' in target_ref else None,
                                       'makerPairedCoverageMedian':float(pd.to_numeric(target_ref.get('maker_paired_coverage'),errors='coerce').median()) if 'maker_paired_coverage' in target_ref else None},
             'references':{'priorOUROwnState':{'expectedTakersPerMarket':2.6728,'sideBalanced':0.5965,'effectBalanced':0.3810,'meanP1AtTrueTaker':0.0157,
                                               'lastMakerAgeMedianMs':19903.0,'makerFills5sMedian':0.0,'makerGrossMedian':72.0},
                           'TargetTeacherState':{'sideFreshBalancedApprox':0.819,'effectFreshBalancedApprox':0.742}},
             'guards':['No threshold tuning; stochastic hazards use model probabilities directly.','No Target runtime action/state is used to generate Maker placements.','Target events appear only in retrospective transfer evaluation.',
                       'This V0 isolates passive lifecycle bootstrap; no generated Taker feedback yet.','-1tick/18-share are execution proxies, not learned Target depth/size.']}
        REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2)); return 0
    finally:
        our.close(); book.close(); target.close()

if __name__=='__main__': raise SystemExit(main())
