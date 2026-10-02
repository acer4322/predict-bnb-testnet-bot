from __future__ import annotations
import bisect,json,math,sqlite3,joblib
from collections import defaultdict,deque
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
R4=ROOT/'data'/'research'/'r4_v0'
MODEL=R4/'r4_deliberate_side_older600_recent600_v2.joblib'
TDB=ROOT/'data'/'target_wallet_official_v1.db'
PDB=ROOT/'data'/'public_source_snapshot_archive_v2.db'
OUT=R4/'hourly'
TZ=ZoneInfo('Asia/Taipei')
VERSION='R4_MPQ_DELIBERATE_SIDE_OVERRIDE_AB_V1'
PUBLIC=['predictUpMid','spotMinusStrikeBps','chainlinkMinusStrikeBps','spotMinusChainlinkBps','directionScore','spotReturn1sBps','spotReturn3sBps','spotReturn5sBps','futuresReturn1sBps','futuresReturn3sBps','futuresReturn5sBps','perpSpotBasisBps','spotQueueImbalance','futuresQueueImbalance','futuresTakerImbalance1s','secondsLeft']
FEATURES=['floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio','cost_per_gross_share','signed_inventory','signed_payoff_gap','last_price','last_shares','last_role_taker','age_since_last_ms','events_5s','maker_events_15s','taker_events_15s','up_events_15s','down_events_15s','up_shares_15s','down_shares_15s','event_index_norm']+PUBLIC

def f(x,d=0.0):
    try:
        y=float(x); return y if math.isfinite(y) else d
    except Exception:return d

def geom(up,down,cu,cd):
    gross=up+down; paired=min(up,down); cost=cu+cd
    au=cu/up if up>1e-9 else 0.; ad=cd/down if down>1e-9 else 0.
    return {'gross':gross,'paired':paired,'cost':cost,'floor':paired-cost,
            'edge':1-(au+ad) if up>1e-9 and down>1e-9 else 0.,
            'absnet':abs(up-down),'coverage':2*paired/gross if gross>1e-9 else 0.,
            'up':up,'down':down,'upside':max(up-cost,down-cost)}

def load_public(ids):
    c=sqlite3.connect(PDB);c.row_factory=sqlite3.Row;d={};q=','.join('?'*len(ids))
    for r in c.execute(f'select market_id,sampled_at_ms,snapshot_json from public_source_snapshots_v2 where market_id in ({q}) order by market_id,sampled_at_ms',ids):
        mid=int(r['market_id']);d.setdefault(mid,[[],[]]);d[mid][0].append(int(r['sampled_at_ms']));d[mid][1].append(json.loads(r['snapshot_json']))
    c.close();return d

def pub_at(pub,mid,t):
    z=pub.get(mid)
    if not z:return None
    i=bisect.bisect_right(z[0],int(t))-1
    if i<0 or int(t)-z[0][i]>3000:return None
    return z[1][i]

def load_events(ids):
    c=sqlite3.connect(TDB);c.row_factory=sqlite3.Row;q=','.join('?'*len(ids));out=defaultdict(list)
    for r in c.execute(f"select market_id,role,side,last_event_ms,average_price,shares from target_parent_orders where market_id in ({q}) and quote_type='BID'",ids):
        role=str(r['role'] or '').upper();side=str(r['side'] or '').upper();px=f(r['average_price'],-1);sh=f(r['shares'])
        if role in {'MAKER','TAKER'} and side in {'UP','DOWN'} and 0<=px<=1 and sh>0:
            out[int(r['market_id'])].append({'t':int(r['last_event_ms']),'role':role,'side':side,'px':px,'sh':sh})
    winners={int(r['market_id']):str(r['winner'] or '').upper() for r in c.execute(f'select market_id,winner from target_market_results where market_id in ({q})',ids)}
    c.close()
    return {m:sorted(v,key=lambda z:z['t']) for m,v in out.items()},winners

def feature_row(state,hist,z,pub_snap,event_i,event_n):
    up,down,cu,cd,fees=state; t=int(z['t']);gross=up+down;base=min(up,down);cost=cu+cd;totcost=cost+fees
    pu=up-totcost;pd=down-totcost;floor=min(pu,pd);ups=max(pu,pd);ss=abs(up-down)
    r5=[x for x in hist if t-int(x['t'])<=5000];r15=[x for x in hist if t-int(x['t'])<=15000];last=hist[-1] if hist else None
    feat={'floor':floor,'upside':ups,'upside_gap':ups-floor,'surplus_shares':ss,'base_pair_shares':base,
          'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':totcost/gross if gross else 0.,
          'signed_inventory':up-down,'signed_payoff_gap':pu-pd,
          'last_price':float(last['px']) if last else 0.,'last_shares':float(last['sh']) if last else 0.,
          'last_role_taker':1. if last and last['role']=='TAKER' else 0.,
          'age_since_last_ms':float(t-int(last['t'])) if last else 0.,'events_5s':float(len(r5)),
          'maker_events_15s':float(sum(x['role']=='MAKER' for x in r15)),'taker_events_15s':float(sum(x['role']=='TAKER' for x in r15)),
          'up_events_15s':float(sum(x['side']=='UP' for x in r15)),'down_events_15s':float(sum(x['side']=='DOWN' for x in r15)),
          'up_shares_15s':float(sum(x['sh'] for x in r15 if x['side']=='UP')),'down_shares_15s':float(sum(x['sh'] for x in r15 if x['side']=='DOWN')),
          'event_index_norm':event_i/max(1,event_n-1)}
    for k in PUBLIC: feat[k]=f(pub_snap.get(k)) if pub_snap else 0.0
    return np.asarray([[float(feat[k]) for k in FEATURES]],dtype=float)

def replay(mid,events,winner,pub,model,policy):
    up=down=cu=cd=fees=0.;hist=[];peak=0.;pos_ms=0.;last_t=None;flags=0;supp=0.;overrides=0;override_correct_winner=0;public_missing=0
    for i,z in enumerate(events):
        pre=geom(up,down,cu,cd);sh=float(z['sh']);px=float(z['px']);side=z['side'];role=z['role']
        pu,pd,pcu,pcd=up,down,cu,cd
        if side=='UP':pu+=sh;pcu+=sh*px
        else:pd+=sh;pcd+=sh*px
        post=geom(pu,pd,pcu,pcd);reserve=max(0.,pre['floor']-post['floor'])
        flag=pre['floor']>0 and reserve>1e-12 and post['edge']<0
        suppress=False
        if flag:
            flags+=1
            if policy=='HARD_MPQ': suppress=True
            elif policy=='SIDE_OVERRIDE':
                ps=pub_at(pub,mid,z['t'])
                if ps is None:
                    public_missing+=1;suppress=True
                else:
                    # The side teacher was trained with Taker fees in state language. Use the current counterfactual accepted-state history.
                    X=feature_row((up,down,cu,cd,fees),hist,z,ps,i,len(events))
                    p_up=float(model.predict_proba(X)[0,1]);pred='UP' if p_up>=.5 else 'DOWN'
                    if pred==side:
                        overrides+=1
                        if side==winner: override_correct_winner+=1
                        suppress=False
                    else:suppress=True
        if suppress:
            supp+=sh
        else:
            up,down,cu,cd=pu,pd,pcu,pcd
            if role=='TAKER':fees+=sh*px*.02
            hist.append(dict(z))
        cur=geom(up,down,cu,cd)
        if last_t is not None and pre['floor']>0:pos_ms+=max(0,int(z['t'])-int(last_t))
        last_t=int(z['t']);peak=max(peak,cur['floor'])
    g=geom(up,down,cu,cd);payout=up if winner=='UP' else down if winner=='DOWN' else 0.;pnl=payout-(cu+cd)
    return {**g,'pnl':pnl,'peakFloor':peak,'positiveDurationSec':pos_ms/1000.,'flags':flags,'suppressedShares':supp,
            'overrides':overrides,'overrideWinnerAligned':override_correct_winner,'publicMissingFlags':public_missing}

def med(rows,key):
    a=[r[key] for r in rows];return median(a) if a else None

def summarize(markets,base,hard,over):
    active=[m for m in markets if hard[m]['flags']>0]
    def deltas(a,b,key):return [a[m][key]-b[m][key] for m in active]
    def md(a):return median(a) if a else None
    hfloor=deltas(hard,base,'floor');ofloor=deltas(over,base,'floor');hpnl=deltas(hard,base,'pnl');opnl=deltas(over,base,'pnl')
    pnl_recovered=[over[m]['pnl']-hard[m]['pnl'] for m in active]
    return {'markets':len(markets),'activeMarkets':len(active),
            'hard':{'medianDeltaFinalFloor':md(hfloor),'medianDeltaPnl':md(hpnl),'medianDeltaCoverage':md(deltas(hard,base,'coverage')),'medianDeltaAbsNet':md(deltas(hard,base,'absnet')),
                    'floorImproved':sum(x>1e-9 for x in hfloor),'floorWorsened':sum(x<-1e-9 for x in hfloor)},
            'override':{'medianDeltaFinalFloor':md(ofloor),'medianDeltaPnl':md(opnl),'medianDeltaCoverage':md(deltas(over,base,'coverage')),'medianDeltaAbsNet':md(deltas(over,base,'absnet')),
                        'floorImproved':sum(x>1e-9 for x in ofloor),'floorWorsened':sum(x<-1e-9 for x in ofloor),'medianPnlRecoveredVsHard':md(pnl_recovered),
                        'overrideEvents':sum(over[m]['overrides'] for m in active),'publicMissingFlags':sum(over[m]['publicMissingFlags'] for m in active),
                        'overrideWinnerAligned':sum(over[m]['overrideWinnerAligned'] for m in active)},
            'activeMarketIds':active}

def main():
    art=joblib.load(MODEL);model=art['model'];ids=[int(x) for x in art['testMarketIds']]
    events,winners=load_events(ids)
    total_test_markets=len(ids)
    active_ids=[]
    for m in ids:
        e=events.get(m,[]);up=down=cu=cd=0.;hit=False
        for z in e:
            pre=geom(up,down,cu,cd);sh=float(z['sh']);px=float(z['px']);side=z['side']
            pu,pd,pcu,pcd=up,down,cu,cd
            if side=='UP':pu+=sh;pcu+=sh*px
            else:pd+=sh;pcd+=sh*px
            post=geom(pu,pd,pcu,pcd);reserve=max(0.,pre['floor']-post['floor'])
            if pre['floor']>0 and reserve>1e-12 and post['edge']<0:
                hit=True;break
            up,down,cu,cd=pu,pd,pcu,pcd
        if hit and winners.get(m) in {'UP','DOWN'}:active_ids.append(m)
    ids=active_ids
    pub=load_public(ids)
    base={};hard={};over={}
    for m in ids:
        e=events[m];w=winners[m]
        base[m]=replay(m,e,w,pub,model,'BASELINE');hard[m]=replay(m,e,w,pub,model,'HARD_MPQ');over[m]=replay(m,e,w,pub,model,'SIDE_OVERRIDE')
    s=summarize(ids,base,hard,over);h=s['hard'];o=s['override']
    hard_gain=float(h['medianDeltaFinalFloor'] or 0.);over_gain=float(o['medianDeltaFinalFloor'] or 0.);hard_pnl=float(h['medianDeltaPnl'] or 0.);recovered=float(o['medianPnlRecoveredVsHard'] or 0.)
    floor_retention=(over_gain/hard_gain) if hard_gain>1e-9 else None
    pnl_recovery_fraction=(recovered/abs(hard_pnl)) if hard_pnl< -1e-9 else None
    gate=bool(s['activeMarkets']>=15 and hard_gain>0 and over_gain>0 and (floor_retention is not None and floor_retention>=.5) and (pnl_recovery_fraction is not None and pnl_recovery_fraction>=.25))
    report={'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),'candidate':'MPQ hard protection by default; override only when independently-trained strict-past Deliberate Side predicts the candidate side at natural 0.5 boundary.',
            'split':{'sideModelTrainMarkets':len(art['trainMarketIds']),'sideModelIndependentTestMarkets':total_test_markets,'abActiveMarketsPrefiltered':len(ids),'abMarketIdsAreSideModelTestOnly':True,'prefilterExactForNoBaselineMPQ':True},'summary':s,
            'tradeoff':{'floorGainRetentionFractionVsHard':floor_retention,'pnlRecoveryFractionVsHardMedianLoss':pnl_recovery_fraction},
            'keepGate':{'required':'active>=15; override median floor gain >0 and >=50% of hard MPQ median floor gain; recover >=25% of hard MPQ median PnL loss','pass':gate},
            'guards':{'winnerNotRuntimeInput':True,'winnerEvaluationOnly':True,'sideBoundaryTuned':False,'sideBoundary':0.5,'strictPastPublicMaxAgeMs':3000,'noEchtgeldTraining':True,'noLiveR3Change':True,'no8781Change':True,'mpqRuleFrozen':True},
            'rows':{str(m):{'baseline':base[m],'hardMPQ':hard[m],'sideOverride':over[m]} for m in s['activeMarketIds']}}
    out=OUT/f"r4_mpq_deliberate_side_override_ab_v1_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.json";out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'activeMarkets':s['activeMarkets'],'hard':h,'override':o,'tradeoff':report['tradeoff'],'keep':gate},ensure_ascii=False))

if __name__=='__main__':main()
