from __future__ import annotations
import json,statistics,math
from pathlib import Path
TRACE=Path('data/research/lan_worker_returns/dagger60-lp-fresh101-extreme-trace-20260909-v1/trace.json')
OUT=Path('data/research/r4_v0/p0_provenance_v1/DAGGER60_LOCAL_PENDING_FRESH101_RETEST_EXTREME_PACKET_V1_20260909/MECHANISM_DIAGNOSTICS.json')

def mean(xs): return sum(xs)/len(xs) if xs else None
def med(xs): return statistics.median(xs) if xs else None
def summ(rows):
    if not rows:return {'n':0}
    return {'n':len(rows),'pnl':sum(r['pnl'] for r in rows),'winRate':sum(r['pnl']>0 for r in rows)/len(rows),'meanPairCoverage':mean([r['pairCoverage'] for r in rows]),'meanAbsNet':mean([r['absNet'] for r in rows]),'positiveFloorRate':sum(r['floor']>=0 for r in rows)/len(rows),'meanFills':mean([r['fills'] for r in rows]),'meanSubmits':mean([r['submits'] for r in rows])}

d=json.load(open(TRACE,encoding='utf-8')); per=[]
for idx,m in enumerate(d['markets']):
    t=m['terminal']; dom='UP' if t['up']>t['down']+1e-9 else 'DOWN' if t['down']>t['up']+1e-9 else None; match=dom==t['winner'] if dom else None
    acc=[a for a in m['actionAttempts'] if a.get('accepted')]; mat=m['materializedActions']; eps=m['reexpandEpisodes']
    accR=[a for a in acc if a['roleAtAttempt']=='REPAIR'];accE=[a for a in acc if a['roleAtAttempt']=='EXPAND']
    matR=[a for a in mat if a['roleAtAttempt']=='REPAIR'];matE=[a for a in mat if a['roleAtAttempt']=='EXPAND']
    z=[e for e in eps if e['materializedRepairCount']==0]; h=[e for e in eps if e['materializedRepairCount']>0]
    pre=[e['reexpandPreState'] for e in eps if e.get('reexpandPreState')]
    def fm(a,k):
        z=a.get('strictPastFeature') if a else None
        return None if not z else z.get('featureMap',{}).get(k)
    per.append({'marketId':t['marketId'],'ordinal':idx+1,'winner':t['winner'],'pnl':t['pnl'],'oppositePnl':t['oppositePnl'],'floor':t['floor'],'pairCoverage':t['pairCoverage'],'absNet':t['absNet'],'fills':t['fills'],'submits':t['submits'],'dominant':dom,'dominantMatch':match,
        'acceptedRepair':len(accR),'acceptedExpand':len(accE),'materializedRepair':len(matR),'materializedExpand':len(matE),'repairMaterializationRate':len(matR)/len(accR) if accR else None,'expandMaterializationRate':len(matE)/len(accE) if accE else None,
        'reexpandEpisodes':len(eps),'zeroRepairReexpandEpisodes':len(z),'hasRepairReexpandEpisodes':len(h),'zeroRepairShare':len(z)/len(eps) if eps else None,'medianReexpandElapsedMs':med([e['elapsedMs'] for e in eps]),
        'reexpandPrePairCoverage':mean([fm(x,'pair_coverage') for x in pre if fm(x,'pair_coverage') is not None]),'reexpandPreFloor':mean([fm(x,'floor') for x in pre if fm(x,'floor') is not None]),'reexpandPreAbsNet':mean([fm(x,'abs_net') for x in pre if fm(x,'abs_net') is not None]),'reexpandPreMarginalPairSumWeak':mean([fm(x,'marginal_pair_sum_weak') for x in pre if fm(x,'marginal_pair_sum_weak') is not None])})

def group(ids):
    xs=[r for r in per if ids(r)]
    out=summ(xs)
    for k in ['acceptedRepair','acceptedExpand','materializedRepair','materializedExpand','reexpandEpisodes','zeroRepairReexpandEpisodes','hasRepairReexpandEpisodes']:
        out['sum'+k[0].upper()+k[1:]]=sum(r[k] for r in xs)
    out['repairMaterializationRate']=sum(r['materializedRepair'] for r in xs)/sum(r['acceptedRepair'] for r in xs) if sum(r['acceptedRepair'] for r in xs) else None
    out['expandMaterializationRate']=sum(r['materializedExpand'] for r in xs)/sum(r['acceptedExpand'] for r in xs) if sum(r['acceptedExpand'] for r in xs) else None
    ep=sum(r['reexpandEpisodes'] for r in xs);zr=sum(r['zeroRepairReexpandEpisodes'] for r in xs);out['zeroRepairReexpandShare']=zr/ep if ep else None
    out['medianMarketReexpandElapsedMs']=med([r['medianReexpandElapsedMs'] for r in xs if r['medianReexpandElapsedMs'] is not None])
    return out
blocks={}
for start in [1,21,41,61,81,101]:
    end=min(101,start+19);blocks[f'{start}-{end}']=group(lambda r,s=start,e=end:s<=r['ordinal']<=e)
out={'version':'DAGGER60_FRESH101_TRACE_MECHANISM_DIAGNOSTICS_V1','source':str(TRACE),'overall':group(lambda r:True),'dominantMatch':group(lambda r:r['dominantMatch'] is True),'dominantMismatch':group(lambda r:r['dominantMatch'] is False),'pnlPositive':group(lambda r:r['pnl']>0),'pnlNonPositive':group(lambda r:r['pnl']<=0),'chronologyBlocks':blocks,'perMarket':per}
OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({k:out[k] for k in ['overall','dominantMatch','dominantMismatch','chronologyBlocks']},indent=2))
