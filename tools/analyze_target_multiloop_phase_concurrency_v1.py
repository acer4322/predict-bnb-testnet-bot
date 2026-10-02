from __future__ import annotations
import argparse,json,math
from pathlib import Path
import pandas as pd

PHASES=[('300_240',240,300,60),('240_180',180,240,60),('180_120',120,180,60),('120_60',60,120,60),('60_30',30,60,30),('30_15',15,30,15),('15_0',0,15,15)]
TAIL_WIDTH={'30_60S':30.0,'15_30S':15.0,'10_15S':5.0,'5_10S':5.0,'0_5S':5.0}

def q(xs,q):
    z=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
    if not z:return None
    i=(len(z)-1)*q;lo=int(math.floor(i));hi=int(math.ceil(i));w=i-lo
    return z[lo]*(1-w)+z[hi]*w

def stats(xs):
    z=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return {'n':len(z),'mean':sum(z)/len(z) if z else None,'median':q(z,.5),'p75':q(z,.75),'p90':q(z,.9),'max':max(z) if z else None}

def rolling_counts(times,window_ms):
    times=sorted(int(x) for x in times);out=[];j=0
    for i,t in enumerate(times):
        while j<=i and t-times[j]>window_ms:j+=1
        out.append(i-j+1)
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--repair-rows',required=True);ap.add_argument('--tail-report',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    d=pd.read_csv(a.repair_rows)
    d=d[['marketId','eventMs','startSecondsLeft']].dropna().copy();d['marketId']=d.marketId.astype(int);d['eventMs']=d.eventMs.astype('int64');d['startSecondsLeft']=d.startSecondsLeft.astype(float)
    rows=[]
    for name,lo,hi,width in PHASES:
        z=d[(d.startSecondsLeft>lo)&(d.startSecondsLeft<=hi)].copy()
        market_counts=z.groupby('marketId').size().tolist() if len(z) else []
        same_ms=(z.groupby(['marketId','eventMs']).size().tolist() if len(z) else [])
        r1=[];r3=[];r5=[]
        for _,g in z.groupby('marketId'):
            ts=g.eventMs.tolist();r1+=rolling_counts(ts,1000);r3+=rolling_counts(ts,3000);r5+=rolling_counts(ts,5000)
        markets=int(z.marketId.nunique()) if len(z) else 0
        rows.append({'phase':name,'loExclusive':lo,'hiInclusive':hi,'widthSec':width,'events':int(len(z)),'markets':markets,'eventsPerMarket':(len(z)/markets if markets else None),'eventRatePerMarketSecond':(len(z)/(markets*width) if markets else None),'eventsPerMarketDistribution':stats(market_counts),'sameTimestampMultiplicity':stats(same_ms),'rolling1sEventCount':stats(r1),'rolling3sEventCount':stats(r3),'rolling5sEventCount':stats(r5)})
    tail=json.load(open(a.tail_report,encoding='utf-8'));tr=[]
    for bucket,block in (tail.get('bySecondsLeft') or {}).items():
        width=TAIL_WIDTH.get(bucket);parents=int(block.get('parents') or 0);markets=int(block.get('markets') or 0)
        tr.append({'bucket':bucket,'widthSec':width,'parents':parents,'markets':markets,'parentsPerMarket':parents/markets if markets else None,'parentRatePerMarketSecond':parents/(markets*width) if markets and width else None,'observedCostUsdt':block.get('observedCostUsdt'),'observedShares':block.get('observedShares')})
    out={'version':'TARGET_MULTILOOP_PHASE_CONCURRENCY_V1','date':'2026-09-05','researchOnly':True,'hypothesis':'Target may run multiple concurrent/overlapping repair-expand responsibilities early, shrink open responsibility count with time, and use Active/Taker intervention for residual convergence late.','repairProxy':{'source':a.repair_rows,'definition':'REPAIR_EFFECT event density is a responsibility-activity proxy only; it does not directly identify unresolved concurrent loops.','phaseRows':rows},'lateActiveProxy':{'source':a.tail_report,'definition':'cheap opposite Taker parent fills <=60s are a late Active/insurance proxy; normalized parent hazard is reported to avoid unequal-bucket bias.','rows':tr,'episodeParents':tail.get('summary',{}).get('episodeParents')},'decision':'USE_PHASE_DEPENDENT_CONCURRENCY_AS_TESTABLE_ARCHITECTURE_NOT_FIXED_FIVE','boundary':['REPAIR_EFFECT is an inventory-effect label, not semantic intent ground truth','short-window event multiplicity is overlap/activity evidence, not proof of simultaneous unresolved responsibility count','tail insurance is a descriptive Active/Taker proxy and not all Repair Takers','no winner/PnL conditioning','no numeric rule transferred to OUR runtime']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'decision':out['decision'],'repairPhaseRows':rows,'lateActiveRows':tr,'episodeParents':out['lateActiveProxy']['episodeParents']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
