from __future__ import annotations

import bisect, json, math, sqlite3, statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PUB_DB = ROOT/'data'/'strategy_target_compare_v1.db'
BOOK_DB = ROOT/'data'/'wallet_maker_book_inference.db'
TARGET_DB = ROOT/'data'/'target_wallet_official_v1.db'
OUT_DIR = ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
REPORT = OUT_DIR/'target_temporary_imbalance_recovery_v0_report.json'
ROWS = OUT_DIR/'target_temporary_imbalance_recovery_v0_rows.csv'
BASE='UNIFIED_CONTROLLER_PAPER_V1%'
GRID=.01
EPS=1e-9


def ro(p:Path):
    c=sqlite3.connect(f'file:{p.resolve().as_posix()}?mode=ro',uri=True,timeout=30)
    c.row_factory=sqlite3.Row; c.execute('pragma query_only=on'); return c

def num(v):
    try:
        x=float(v); return x if math.isfinite(x) else None
    except Exception: return None

def q(xs,p):
    ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
    if not ys:return None
    if len(ys)==1:return ys[0]
    z=(len(ys)-1)*p; lo=int(math.floor(z)); hi=int(math.ceil(z)); w=z-lo
    return ys[lo]*(1-w)+ys[hi]*w

def stats(xs):
    ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return {'n':len(ys),'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,
            'p25':q(ys,.25),'p75':q(ys,.75),'p90':q(ys,.9)}

def load_public(c):
    out=defaultdict(list)
    for r in c.execute("select market_id,coalesce(source_snapshot_ms,decision_ms) ms,public_state_json from our_decisions where strategy_version like ? and public_state_json is not null order by market_id,ms",(BASE,)):
        try:s=json.loads(str(r['public_state_json']))
        except Exception:continue
        if isinstance(s,dict):out[int(r['market_id'])].append((int(r['ms']),s))
    return out

def before(rows,ms,max_age=2000):
    if not rows:return None
    ts=[x[0] for x in rows]; i=bisect.bisect_right(ts,ms)-1
    if i<0:return None
    t,s=rows[i]
    return (t,s) if 0<=ms-t<=max_age else None

def load_parents(c,markets):
    out=defaultdict(list); ids=sorted(markets)
    for st in range(0,len(ids),300):
        b=ids[st:st+300]; qs=','.join('?'*len(b))
        sql=f'''select parent_id,market_id,target_side,target_price,placement_first_ms,placement_last_ms,
                       first_target_ms,last_target_ms,target_filled_shares,resting_ms
                from maker_book_inference_v21_parent_lifecycles
                where market_id in ({qs}) and placement_supports_18=1 and placement_coverage>=.85
                  and fill_allocation_coverage>=.70 and confidence>=.75 and placement_first_ms is not null
                  and first_target_ms is not null and last_target_ms is not null
                order by market_id,placement_first_ms,parent_id'''
        for r in c.execute(sql,b):out[int(r['market_id'])].append(dict(r))
    return out

def load_events(c,markets):
    out=defaultdict(list); ids=sorted(markets)
    for st in range(0,len(ids),300):
        b=ids[st:st+300]; qs=','.join('?'*len(b))
        sql=f'''select market_id,event_ms,role,side,price,shares,id from wallet_shadow_target_events
                where market_id in ({qs}) and asset='BTC' and quote_type='BID' and role in ('MAKER','TAKER')
                  and side in ('UP','DOWN') order by market_id,event_ms,id'''
        for r in c.execute(sql,b):out[int(r['market_id'])].append(dict(r))
    return out

def inv_until(events,ms,inclusive=False):
    mu=md=tu=td=0.0
    for e in events:
        t=int(e['event_ms'])
        if t>ms or (not inclusive and t>=ms): break
        sh=float(e['shares'])
        if e['role']=='MAKER':
            if e['side']=='UP':mu+=sh
            else:md+=sh
        else:
            if e['side']=='UP':tu+=sh
            else:td+=sh
    mn=mu-md; cn=(mu+tu)-(md+td); mg=mu+md; cg=mu+md+tu+td
    return {'makerUp':mu,'makerDown':md,'takerUp':tu,'takerDown':td,'makerNet':mn,'combinedNet':cn,
            'makerAbsNet':abs(mn),'combinedAbsNet':abs(cn),'makerPairedCoverage':2*min(mu,md)/mg if mg>EPS else 1.0,
            'combinedPairedCoverage':2*min(mu+tu,md+td)/cg if cg>EPS else 1.0,
            'makerDominant':'UP' if mn>EPS else 'DOWN' if mn<-EPS else None,
            'combinedDominant':'UP' if cn>EPS else 'DOWN' if cn<-EPS else None}

def role_reduces(net,side,shares):
    after=net+(shares if side=='UP' else -shares)
    return abs(after)<abs(net)-EPS

def first_repairs(events,start_ms,end_ms):
    # Walk from state including start_ms. Record first opposite Maker inventory-reducing fill and first Taker combined-net-reducing fill.
    pre=inv_until(events,start_ms,inclusive=True)
    mn=float(pre['makerNet']); cn=float(pre['combinedNet'])
    maker_t=taker_t=None
    for e in events:
        t=int(e['event_ms'])
        if t<=start_ms:continue
        if t>end_ms:break
        sh=float(e['shares']); side=str(e['side'])
        if e['role']=='MAKER':
            if maker_t is None and role_reduces(mn,side,sh): maker_t=t
            mn += sh if side=='UP' else -sh
        else:
            if taker_t is None and role_reduces(cn,side,sh): taker_t=t
            cn += sh if side=='UP' else -sh
    return maker_t,taker_t

def age_bin(ms):
    if ms<500:return 'A_LT0_5S'
    if ms<1500:return 'A_0_5_1_5S'
    if ms<3000:return 'A_1_5_3S'
    if ms<5000:return 'A_3_5S'
    return 'A_5S_PLUS'
def pc_bin(x):
    if x<.4:return 'PC_LT40'
    if x<.65:return 'PC_40_65'
    if x<.8:return 'PC_65_80'
    if x<.9:return 'PC_80_90'
    return 'PC_90_100'
def off_bin(x):
    if x is None:return 'Q_UNKNOWN'
    if x<=-1:return 'Q_AHEAD_INSIDE'
    if x<1:return 'Q_AT_BID'
    if x<2:return 'Q_1T_BEHIND'
    if x<4:return 'Q_2_3T_BEHIND'
    return 'Q_4T_PLUS_BEHIND'
def time_bin(s):
    if s is None:return 'T_UNKNOWN'
    if s>240:return 'T300_240'
    if s>180:return 'T240_180'
    if s>120:return 'T180_120'
    if s>60:return 'T120_60'
    if s>30:return 'T60_30'
    if s>15:return 'T30_15'
    return 'T15_0'
def path_label(mt,tt,horizon_end):
    if mt is None and tt is None:return 'NO_REPAIR_SIGNAL'
    if mt is not None and tt is None:return 'MAKER_ONLY'
    if tt is not None and mt is None:return 'TAKER_ONLY'
    if mt==tt:return 'MIXED_SAME_TIME'
    return 'MAKER_FIRST_THEN_TAKER' if mt<tt else 'TAKER_FIRST_THEN_MAKER'

def summarize(rows):
    n=len(rows); pc=Counter(r['repairPath15s'] for r in rows)
    return {'n':n,'markets':len({int(r['marketId']) for r in rows}),
            'repairPath15s':dict(pc),'repairAny15sRate':sum(r['repairPath15s']!='NO_REPAIR_SIGNAL' for r in rows)/n if n else None,
            'makerPreAbsNet':stats([r['makerPreAbsNet'] for r in rows]),'makerPostAbsNet':stats([r['makerPostAbsNet'] for r in rows]),
            'makerExpansion':stats([r['makerExpansion'] for r in rows]),'makerPairedCoveragePre':stats([r['makerPairedCoveragePre'] for r in rows]),
            'makerAbsNet15s':stats([r['makerAbsNet15s'] for r in rows]),'combinedAbsNet15s':stats([r['combinedAbsNet15s'] for r in rows]),
            'makerRecoveredToPre15sRate':sum(bool(r['makerRecoveredToPre15s']) for r in rows)/n if n else None,
            'combinedImproved15sRate':sum(bool(r['combinedImproved15s']) for r in rows)/n if n else None}
def group(rows,key,min_n=20):
    d=defaultdict(list)
    for r in rows:d[str(r[key])].append(r)
    return {k:summarize(v) for k,v in sorted(d.items()) if len(v)>=min_n}
def cross(rows,a,b,min_n=30):
    d=defaultdict(list)
    for r in rows:d[(str(r[a]),str(r[b]))].append(r)
    return {f'{x}|{y}':summarize(v) for (x,y),v in sorted(d.items()) if len(v)>=min_n}

def main():
    pc=ro(PUB_DB); bc=ro(BOOK_DB); tc=ro(TARGET_DB)
    try:
        public=load_public(pc); markets=set(public); parents=load_parents(bc,markets); events=load_events(tc,markets); meta={int(r['market_id']):dict(r) for r in bc.execute('select market_id,window_end_ms from maker_book_inference_markets where window_end_ms is not null')}
        rows=[]; candidates=realized=0
        for mid in sorted(markets):
            ps=parents.get(mid,[]); ev=events.get(mid,[]); mend=int(meta.get(mid,{}).get('window_end_ms') or 0)
            if not ps or not ev or not mend:continue
            for p in ps:
                cp=int(p['placement_first_ms'])-1; side=str(p['target_side'])
                prior=[q for q in ps if q['parent_id']!=p['parent_id'] and q['target_side']==side and int(q['placement_first_ms'])<=cp<int(q['last_target_ms'])]
                if not prior:continue
                pre=inv_until(ev,cp,inclusive=True)
                if pre['makerDominant']!=side or pre['makerAbsNet']<18-EPS:continue
                candidates+=1
                anchor=int(p['last_target_ms'])
                post=inv_until(ev,anchor,inclusive=True)
                expansion=float(post['makerAbsNet'])-float(pre['makerAbsNet'])
                if expansion<=1.0:continue
                realized+=1
                snap=before(public[mid],cp,2000)
                if snap is None:continue
                _,s=snap
                prior.sort(key=lambda q:(int(q['placement_first_ms']),str(q['parent_id'])))
                cur=prior[-1]; age=cp-int(cur['placement_first_ms'])
                bid=num(s.get('predictUpBid') if side=='UP' else s.get('predictDownBid'))
                if bid is None:bid=num(s.get('predict_up_bid') if side=='UP' else s.get('predict_down_bid'))
                off=(bid-float(cur['target_price']))/GRID if bid is not None else None
                bias=str(s.get('directionBias') or 'NEUTRAL').upper(); align='NEUTRAL' if bias not in ('UP','DOWN') else ('TAILWIND' if bias==side else 'HEADWIND')
                vol=str(s.get('volatilityAlert') or 'UNKNOWN').upper(); sec=num(s.get('secondsLeft')); sec=sec if sec is not None else num(s.get('seconds_left'))
                mt15,tt15=first_repairs(ev,anchor,min(anchor+15000,mend)); mt30,tt30=first_repairs(ev,anchor,min(anchor+30000,mend))
                s15=inv_until(ev,min(anchor+15000,mend),inclusive=True); s30=inv_until(ev,min(anchor+30000,mend),inclusive=True)
                row={'marketId':mid,'parentId':str(p['parent_id']),'side':side,'placementMs':int(p['placement_first_ms']),'anchorFillEndMs':anchor,
                     'activeSameBefore':len(prior),'currentAgeMs':age,'ageBin':age_bin(age),'quoteOffsetTicks':off,'quoteOffsetBin':off_bin(off),
                     'secondsLeft':sec,'timeBin':time_bin(sec),'directionBias':bias,'directionAlignment':align,'volatilityAlert':vol,'directionScore':num(s.get('directionScore')),
                     'makerPreAbsNet':float(pre['makerAbsNet']),'makerPostAbsNet':float(post['makerAbsNet']),'makerExpansion':expansion,
                     'makerPairedCoveragePre':float(pre['makerPairedCoverage']),'pairedCoverageBin':pc_bin(float(pre['makerPairedCoverage'])),
                     'combinedPreAbsNet':float(pre['combinedAbsNet']),'combinedPostAbsNet':float(post['combinedAbsNet']),
                     'repairPath15s':path_label(mt15,tt15,min(anchor+15000,mend)),'repairPath30s':path_label(mt30,tt30,min(anchor+30000,mend)),
                     'firstMakerRepairDelayMs':mt15-anchor if mt15 is not None else None,'firstTakerRepairDelayMs':tt15-anchor if tt15 is not None else None,
                     'makerAbsNet15s':float(s15['makerAbsNet']),'makerAbsNet30s':float(s30['makerAbsNet']),'combinedAbsNet15s':float(s15['combinedAbsNet']),'combinedAbsNet30s':float(s30['combinedAbsNet']),
                     'makerRecoveredToPre15s':int(float(s15['makerAbsNet'])<=float(pre['makerAbsNet'])+1.0),'makerRecoveredToPre30s':int(float(s30['makerAbsNet'])<=float(pre['makerAbsNet'])+1.0),
                     'combinedImproved15s':int(float(s15['combinedAbsNet'])<float(post['combinedAbsNet'])-1.0),'combinedImproved30s':int(float(s30['combinedAbsNet'])<float(post['combinedAbsNet'])-1.0)}
                rows.append(row)
        blocks={'byDirectionAlignment':group(rows,'directionAlignment'),'byPairedCoverage':group(rows,'pairedCoverageBin'),'byAge':group(rows,'ageBin'),'byQuoteOffset':group(rows,'quoteOffsetBin'),'byTime':group(rows,'timeBin'),'byVolatility':group(rows,'volatilityAlert'),
                'directionXPairedCoverage':cross(rows,'directionAlignment','pairedCoverageBin'),'directionXQuoteOffset':cross(rows,'directionAlignment','quoteOffsetBin'),'ageXDirection':cross(rows,'ageBin','directionAlignment')}
        rep={'reportVersion':'TARGET_TEMPORARY_IMBALANCE_RECOVERY_V0','researchOnly':True,'liveTradingChanges':False,
             'question':'When Target authorizes a same-side overlapping Maker parent while that side is already Maker-dominant, and that parent later realizes a genuine Maker abs-net expansion, how is the temporary imbalance repaired?',
             'definition':{'candidate':'new high-confidence anchored same-side parent while another same-side parent is active and strict-past Maker inventory is already dominant on that side with abs-net>=18','realizedExcursion':'candidate whose inventory after the new parent final Target fill is >1 share more imbalanced than strict-past placement inventory','repairMaker':'first subsequent Maker fill that reduces instantaneous Maker |net|','repairTaker':'first subsequent Taker fill that reduces instantaneous combined |net|','horizons':['15s','30s']},
             'coverage':{'publicMarkets':len(public),'candidateAuthorizations':candidates,'realizedExcursionsWithPublicState':len(rows),'realizedBeforePublicFilter':realized,'markets':len({r['marketId'] for r in rows})},
             'overall':summarize(rows),'blocks':blocks,
             'guards':['Target future events are used only to assign post-hoc recovery outcomes, never as action inputs.','Anchored Maker placement/active-parent ownership is inferred, not private order ground truth.','This is descriptive actual-path analysis, not a deployable backtest and not EBM training.','Abs-net thresholds are fixed semantic deadbands, not tuned profit thresholds.']}
        OUT_DIR.mkdir(parents=True,exist_ok=True); pd.DataFrame(rows).to_csv(ROWS,index=False); REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'coverage':rep['coverage'],'overall':rep['overall'],'blocks':{k:v for k,v in blocks.items()}},ensure_ascii=False,indent=2))
    finally:
        pc.close();bc.close();tc.close()

if __name__=='__main__':main()
