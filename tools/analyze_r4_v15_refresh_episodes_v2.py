from __future__ import annotations
import argparse,json,math
from pathlib import Path
import numpy as np

def mean(xs):return float(np.mean(xs)) if xs else None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--output',required=True);args=ap.parse_args();j=json.loads(Path(args.input).read_text())
    qs=[q for m in j['markets'] for q in m.get('queries',[]) if q.get('managementRepairWeak') and q.get('takerShadowPositive')]
    groups={}
    for q in qs:groups.setdefault((int(q['marketId']),str(q['objectivePurpose'])+'|'+str(q['objectiveSide'])),[]).append(q)
    eps=[]
    for (mid,key),z in groups.items():
        z=sorted(z,key=lambda r:int(r['atMs']));cur=[]
        for q in z:
            if cur and int(q['atMs'])-int(cur[-1]['atMs'])>3000:
                eps.append((mid,key,cur));cur=[]
            cur.append(q)
        if cur:eps.append((mid,key,cur))
    outrows=[]
    for mid,key,z in eps:
        top=max(z,key=lambda r:float(r['pSameObjectiveTaker3s']));start=z[0];end=z[-1];act=any(q.get('actualSameObjectiveTaker3s') for q in z)
        outrows.append({'marketId':mid,'objectiveKey':key,'startMs':int(start['atMs']),'endMs':int(end['atMs']),'durationS':(int(end['atMs'])-int(start['atMs']))/1000.,'queries':len(z),'actualSameObjectiveTaker3s':bool(act),'maxPTaker':float(top['pSameObjectiveTaker3s']),'maxPMaker':float(top['pSameObjectiveMaker3s']),'maxPParent':float(top['pSameObjectiveParent3s']),'maxTakerMinusMaker':float(top['pSameObjectiveTaker3s'])-float(top['pSameObjectiveMaker3s']),'startSecondsLeft':float(start['secondsLeft']),'startFloor':float(start['floor']),'startAbsNet':float(start['absNet']),'startOwners':float(start['weakActiveOwners']),'startUnresolved':float(start['weakUnresolvedShares']),'startProgress':float(start['weakProgressRatio']),'startFill5s':float(start['weakFillShares5s']),'startFutureFloorDelta15s':start.get('futureFloorDelta15s'),'startFutureAbsNetDelta15s':start.get('futureAbsNetDelta15s'),'topAtMs':int(top['atMs']),'topSecondsLeft':float(top['secondsLeft']),'topOwners':float(top['weakActiveOwners']),'topUnresolved':float(top['weakUnresolvedShares']),'topProgress':float(top['weakProgressRatio']),'topFutureFloorDelta15s':top.get('futureFloorDelta15s'),'topFutureAbsNetDelta15s':top.get('futureAbsNetDelta15s')})
    missed=[r for r in outrows if not r['actualSameObjectiveTaker3s']];actual=[r for r in outrows if r['actualSameObjectiveTaker3s']]
    bym={}
    for r in outrows:bym.setdefault(str(r['marketId']),0);bym[str(r['marketId'])]+=1
    summary={'positiveQueries':len(qs),'episodes':len(outrows),'episodeMarkets':len(set(r['marketId'] for r in outrows)),'actualTakerEpisodes':len(actual),'missedEpisodes':len(missed),'missedEpisodeMarkets':len(set(r['marketId'] for r in missed)),'meanEpisodeDurationS':mean([r['durationS'] for r in outrows]),'medianEpisodeDurationS':float(np.median([r['durationS'] for r in outrows])) if outrows else None,'meanMissedMaxPTaker':mean([r['maxPTaker'] for r in missed]),'meanMissedTakerMinusMaker':mean([r['maxTakerMinusMaker'] for r in missed]),'meanMissedStartOwners':mean([r['startOwners'] for r in missed]),'meanMissedStartUnresolved':mean([r['startUnresolved'] for r in missed]),'medianMissedStartProgress':float(np.median([r['startProgress'] for r in missed])) if missed else None,'meanMissedStartFutureFloorDelta15s':mean([float(r['startFutureFloorDelta15s']) for r in missed if r.get('startFutureFloorDelta15s') is not None]),'meanMissedStartFutureAbsNetDelta15s':mean([float(r['startFutureAbsNetDelta15s']) for r in missed if r.get('startFutureAbsNetDelta15s') is not None]),'episodesPerMarket':bym,'warning':'Episodes collapse consecutive positive checkpoints using the frozen 3s model horizon. Future outcomes are scoring-only.'}
    out={'version':'R4_V15_REFRESH_EPISODES_V2','researchOnly':True,'diagnosticOnly':True,'summary':summary,'episodes':outrows};Path(args.output).write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps(summary,indent=2,ensure_ascii=False));print('EPISODES');print(json.dumps(outrows,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
