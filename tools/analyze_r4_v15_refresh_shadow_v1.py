from __future__ import annotations
import argparse,json,math
from pathlib import Path
import numpy as np

def qtile(xs):
    if not xs:return {}
    a=np.asarray(xs,float);return {str(q):float(np.quantile(a,q)) for q in [0,.1,.25,.5,.75,.9,.95,.99,1]}
def mean(rows,k):
    a=[float(r[k]) for r in rows if r.get(k) is not None and math.isfinite(float(r[k]))];return float(np.mean(a)) if a else None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--output',required=True);args=ap.parse_args();j=json.loads(Path(args.input).read_text());qs=[q for m in j['markets'] for q in m.get('queries',[]) if q.get('managementRepairWeak')]
    for q in qs:
        q['takerMinusMaker']=float(q['pSameObjectiveTaker3s'])-float(q['pSameObjectiveMaker3s']);q['parentMinusTaker']=float(q['pSameObjectiveParent3s'])-float(q['pSameObjectiveTaker3s']);q['consensusTakerSpecific']=bool(q['pSameObjectiveParent3s']>=.5 and q['pSameObjectiveTaker3s']>=.5 and q['pSameObjectiveTaker3s']>q['pSameObjectiveMaker3s'])
    actual=[q for q in qs if q.get('actualSameObjectiveTaker3s')];miss=[q for q in qs if not q.get('actualSameObjectiveTaker3s')];cons=[q for q in qs if q['consensusTakerSpecific']];consmiss=[q for q in cons if not q.get('actualSameObjectiveTaker3s')]
    per=[]
    for mid in sorted(set(q['marketId'] for q in qs)):
        z=[q for q in qs if q['marketId']==mid];c=[q for q in z if q['consensusTakerSpecific']];top=max(z,key=lambda r:r['pSameObjectiveTaker3s'])
        per.append({'marketId':mid,'n':len(z),'consensusN':len(c),'actualTakerN':sum(q['actualSameObjectiveTaker3s'] for q in z),'maxPTaker':float(top['pSameObjectiveTaker3s']),'maxTakerMinusMaker':max(float(q['takerMinusMaker']) for q in z),'topAtMs':top['atMs'],'topSecondsLeft':top['secondsLeft'],'topOwners':top['weakActiveOwners'],'topProgress':top['weakProgressRatio'],'topUnresolved':top['weakUnresolvedShares'],'topFutureFloorDelta15s':top.get('futureFloorDelta15s'),'topFutureAbsNetDelta15s':top.get('futureAbsNetDelta15s')})
    out={'version':'R4_V15_REFRESH_SHADOW_ANALYSIS_V1','researchOnly':True,'diagnosticOnly':True,'managementRepairQueries':len(qs),'actualSameObjectiveTaker3s':len(actual),'pTakerQuantiles':qtile([q['pSameObjectiveTaker3s'] for q in qs]),'takerMinusMakerQuantiles':qtile([q['takerMinusMaker'] for q in qs]),'actualTaker':{'n':len(actual),'meanPTaker':mean(actual,'pSameObjectiveTaker3s'),'meanPMaker':mean(actual,'pSameObjectiveMaker3s'),'meanPParent':mean(actual,'pSameObjectiveParent3s'),'meanTakerMinusMaker':mean(actual,'takerMinusMaker')},'nonActual':{'n':len(miss),'meanPTaker':mean(miss,'pSameObjectiveTaker3s'),'meanPMaker':mean(miss,'pSameObjectiveMaker3s'),'meanPParent':mean(miss,'pSameObjectiveParent3s'),'meanTakerMinusMaker':mean(miss,'takerMinusMaker')},'consensusTakerSpecific':{'rule':'pParent3s>=0.5 AND pTaker3s>=0.5 AND pTaker3s>pMaker3s (diagnostic semantic consistency; not an action threshold)','n':len(cons),'markets':len(set(q['marketId'] for q in cons)),'actualTakerN':sum(q['actualSameObjectiveTaker3s'] for q in cons),'missedN':len(consmiss),'meanFutureFloorDelta15s':mean(cons,'futureFloorDelta15s'),'meanFutureAbsNetDelta15s':mean(cons,'futureAbsNetDelta15s'),'meanOwners':mean(cons,'weakActiveOwners'),'medianProgress':float(np.median([q['weakProgressRatio'] for q in cons])) if cons else None},'perMarket':per,'warning':'All future action/floor fields are scoring-only; this analysis does not authorize action or tune thresholds from PnL.'};Path(args.output).write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
