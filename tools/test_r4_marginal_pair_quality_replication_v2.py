from __future__ import annotations
import json, math, sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'
OUTDIR=ROOT/'data'/'research'/'r4_v0'/'hourly'
TZ=ZoneInfo('Asia/Taipei')
SEALED='2026-08-16'
VERSION='R4_MARGINAL_PAIR_QUALITY_REPLICATION_V2'
N_MARKETS=2000
BLOCK_SIZE=500


def ro(p: Path):
    c=sqlite3.connect(f'file:{p.as_posix()}?mode=ro',uri=True)
    c.row_factory=sqlite3.Row
    c.execute('pragma query_only=on')
    return c


def f(x,d=0.0):
    try:
        y=float(x)
        return y if math.isfinite(y) else d
    except Exception:
        return d


def geom(up,down,cu,cd):
    gross=up+down; paired=min(up,down); cost=cu+cd
    au=cu/up if up>1e-9 else 0.; ad=cd/down if down>1e-9 else 0.
    return {
        'gross':gross,'paired':paired,'cost':cost,'floor':paired-cost,
        'edge':1-(au+ad) if up>1e-9 and down>1e-9 else 0.,
        'absnet':abs(up-down),'coverage':2*paired/gross if gross>1e-9 else 0.,
        'up':up,'down':down
    }


def load(n=N_MARKETS):
    c=ro(DB); meta=[]
    for r in c.execute("""
        select m.market_id,m.window_end_ms,x.winner
        from target_markets m join target_market_results x on x.market_id=m.market_id
        where m.asset='BTC' and m.window_end_ms is not null and x.fill_count>0
        order by m.window_end_ms desc limit ?
    """,(n*2,)):
        dt=datetime.fromtimestamp(int(r['window_end_ms'])/1000,TZ).date().isoformat()
        if dt==SEALED: continue
        meta.append((int(r['market_id']),int(r['window_end_ms']),str(r['winner'] or '').upper()))
        if len(meta)>=n: break
    ids=[m for m,_,_ in meta]
    q=','.join('?'*len(ids)); ev=defaultdict(list)
    for r in c.execute(f"select market_id,role,side,last_event_ms,average_price,shares from target_parent_orders where market_id in ({q}) and quote_type='BID'",ids):
        role=str(r['role'] or '').upper(); side=str(r['side'] or '').upper(); px=f(r['average_price'],-1); sh=f(r['shares'])
        if role in {'MAKER','TAKER'} and side in {'UP','DOWN'} and 0<=px<=1 and sh>0:
            ev[int(r['market_id'])].append({'t':int(r['last_event_ms']),'role':role,'side':side,'px':px,'sh':sh})
    c.close()
    return sorted(meta,key=lambda z:z[1]),{m:sorted(v,key=lambda z:z['t']) for m,v in ev.items()}


def replay(events, winner='', gate=False, stress='NONE'):
    up=down=cu=cd=0.; peak=0.; pos_ms=0.; last_t=None; flags=0; suppressed=0.; ever_pos=False
    weak_maker_counter=0; maker_counter=0; base_acquired_at=None
    for z in events:
        pre=geom(up,down,cu,cd); sh=float(z['sh']); side=z['side']; role=z['role']; px=float(z['px'])
        weak='UP' if up<down else 'DOWN' if down<up else None
        if role=='MAKER':
            maker_counter+=1
            if weak and side==weak:
                weak_maker_counter+=1
                if stress=='WEAK_DROP_ALTERNATE' and weak_maker_counter%2==0:
                    sh=0.0
                elif stress=='WEAK_PARTIAL_HALF_ALTERNATE' and weak_maker_counter%2==0:
                    sh*=0.5
            if stress=='MAKER_DROP_EVERY5' and maker_counter%5==0:
                sh=0.0
        pu,pd,pcu,pcd=up,down,cu,cd
        if sh>0:
            if side=='UP': pu+=sh; pcu+=sh*px
            else: pd+=sh; pcd+=sh*px
        post=geom(pu,pd,pcu,pcd)
        reserve_spent=max(0.,pre['floor']-post['floor'])
        flag=sh>0 and pre['floor']>0 and reserve_spent>1e-12 and post['edge']<0
        if flag: flags+=1
        if gate and flag:
            suppressed+=sh
            post=pre
        else:
            up,down,cu,cd=pu,pd,pcu,pcd
        if last_t is not None and pre['floor']>0:
            pos_ms+=max(0,int(z['t'])-int(last_t))
        last_t=int(z['t'])
        peak=max(peak,post['floor'])
        if post['floor']>0:
            ever_pos=True
            if base_acquired_at is None: base_acquired_at=int(z['t'])
    g=geom(up,down,cu,cd)
    payout=up if winner=='UP' else down if winner=='DOWN' else 0.
    pnl=payout-(cu+cd)
    return {**g,'peakFloor':peak,'positiveDurationSec':pos_ms/1000.,'flags':flags,
            'suppressedShares':suppressed,'everPositive':ever_pos,'baseAcquiredAt':base_acquired_at,'pnl':pnl}


def summarize(rows):
    active=[r for r in rows if r['flags']>0]
    def med(k,rs=active): return median([r[k] for r in rs]) if rs else None
    def mean(k,rs=active): return sum(r[k] for r in rs)/len(rs) if rs else None
    return {
        'markets':len(rows),'activeMarkets':len(active),'activationRate':len(active)/len(rows) if rows else 0.,
        'medianDeltaFinalFloorActive':med('deltaFinalFloor'),'meanDeltaFinalFloorActive':mean('deltaFinalFloor'),
        'improved':sum(r['deltaFinalFloor']>1e-9 for r in active),'worsened':sum(r['deltaFinalFloor']<-1e-9 for r in active),
        'same':sum(abs(r['deltaFinalFloor'])<=1e-9 for r in active),
        'medianDeltaPositiveDurationSec':med('deltaPositiveDurationSec'),
        'medianDeltaCoverage':med('deltaCoverage'),'medianDeltaAbsNet':med('deltaAbsNet'),
        'medianDeltaPnl':med('deltaPnl'),
        'baselineFinalFloorPositiveRate':sum(r['baselineFinalFloor']>0 for r in rows)/len(rows) if rows else 0.,
        'gateFinalFloorPositiveRate':sum(r['gateFinalFloor']>0 for r in rows)/len(rows) if rows else 0.,
    }


def eval_block(meta,ev,stress='NONE'):
    rows=[]
    for mid,wend,winner in meta:
        e=ev.get(mid,[])
        b=replay(e,winner,False,stress); g=replay(e,winner,True,stress)
        rows.append({
            'marketId':mid,'windowEndMs':wend,'winner':winner,'stress':stress,'flags':g['flags'],
            'baselineEverPositive':b['everPositive'],'gateEverPositive':g['everPositive'],
            'baselineFinalFloor':b['floor'],'gateFinalFloor':g['floor'],'deltaFinalFloor':g['floor']-b['floor'],
            'baselinePeakFloor':b['peakFloor'],'gatePeakFloor':g['peakFloor'],
            'deltaPositiveDurationSec':g['positiveDurationSec']-b['positiveDurationSec'],
            'deltaCoverage':g['coverage']-b['coverage'],'deltaAbsNet':g['absnet']-b['absnet'],
            'deltaPnl':g['pnl']-b['pnl'],'suppressedShares':g['suppressedShares']
        })
    return rows


def main():
    meta,ev=load()
    blocks=[]; all_rows=[]
    for i in range(0,len(meta),BLOCK_SIZE):
        block=meta[i:i+BLOCK_SIZE]
        if not block: continue
        rows=eval_block(block,ev,'NONE'); all_rows.extend(rows)
        blocks.append({'blockIndex':i//BLOCK_SIZE+1,'startWindowEndMs':block[0][1],'endWindowEndMs':block[-1][1],**summarize(rows)})
    combined=summarize(all_rows)
    stress_results={}
    for stress in ['WEAK_DROP_ALTERNATE','WEAK_PARTIAL_HALF_ALTERNATE','MAKER_DROP_EVERY5']:
        rows=eval_block(meta,ev,stress)
        # Preservation robustness is evaluated only where a positive base existed and the gate actually activated.
        eligible=[r for r in rows if r['baselineEverPositive'] and r['flags']>0]
        stress_results[stress]={**summarize(eligible),'eligiblePreservationVariants':len(eligible)}
    positive_blocks=sum((b['medianDeltaFinalFloorActive'] or 0)>0 for b in blocks if b['activeMarkets']>0)
    keep_replication=bool(
        positive_blocks>=3 and
        (combined['medianDeltaFinalFloorActive'] or 0)>0 and
        (combined['medianDeltaPositiveDurationSec'] or 0)>=0 and
        (combined['medianDeltaCoverage'] is not None and combined['medianDeltaCoverage']>=-0.05) and
        (combined['medianDeltaAbsNet'] is not None and combined['medianDeltaAbsNet']<=5.0)
    )
    stress_eligible=sum(v['eligiblePreservationVariants'] for v in stress_results.values())
    stress_positive=sum((v['medianDeltaFinalFloorActive'] or 0)>0 for v in stress_results.values() if v['eligiblePreservationVariants']>=10)
    stress_status='PASS' if stress_eligible>=30 and stress_positive>=2 else ('INCONCLUSIVE' if stress_eligible<30 else 'FAIL')
    report={
        'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),
        'candidate':{'semanticRule':'pre_floor>0 AND reserve_spent_if_filled>0 AND post_fill_combined_pair_edge<0 => suppress candidate fill','thresholdTuned':False,'pathFixedCounterfactual':True},
        'cohort':{'ordinaryMarkets':len(meta),'blockSize':BLOCK_SIZE,'blocks':len(blocks),'sealed20260816':True},
        'blocks':blocks,'combined':combined,'executionStress':stress_results,
        'replicationGate':{'positiveBlocksRequired':3,'positiveBlocksObserved':positive_blocks,'pass':keep_replication},
        'stressGate':{'status':stress_status,'eligibleVariants':stress_eligible,'note':'Preservation-only gate; variants that never acquire a positive base are acquisition failures, not MPQ failures.'},
        'guards':{'noEchtgeldTraining':True,'noNewEchtgeld':True,'noLiveR3Change':True,'no8781Change':True,'winnerEvaluationOnly':True}
    }
    stamp=datetime.now(TZ).strftime('%Y%m%d_%H%M%S')
    out=OUTDIR/f'r4_marginal_pair_quality_replication_v2_{stamp}.json'
    out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'replicationPass':keep_replication,'stressStatus':stress_status,'positiveBlocks':positive_blocks,'combined':combined,'stress':stress_results},ensure_ascii=False))

if __name__=='__main__': main()
