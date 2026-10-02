from __future__ import annotations
import json,sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_protection_inherited_responsibility_v1 as v1

PREREG=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_protection_inherited_responsibility_preregistered_v2.json'
OUTDIR=ROOT/'data/research/r4_v0/p0_provenance_v1'


def run_ids(ids,name):
    rows=[]; markets=[]
    for i,mid in enumerate(ids,1):
        try:r=v1.one_market(int(mid))
        except Exception as e:r={'marketId':int(mid),'status':'ERROR','error':f'{type(e).__name__}:{e}','rows':[]}
        rows.extend(r.pop('rows',[]));markets.append(r)
        print(json.dumps({'cohort':name,'i':i,'n':len(ids),'marketId':mid,'status':r.get('status'),'rowsTotal':len(rows)},ensure_ascii=False),flush=True)
    return markets,rows


def summarize(rows):
    rel=Counter(str(r.get('relation')) for r in rows)
    spendrel=Counter(str(r.get('relation')) for r in rows if int(r.get('reserve_spend_by_inherited_root') or 0))
    breakrel=Counter(str(r.get('relation')) for r in rows if int(r.get('base_break_by_inherited_root') or 0))
    return {
        'rows':len(rows),'markets':len(set(int(r['marketId']) for r in rows)),
        'reserveSpendSupport':v1.support(rows,'reserve_spend_by_inherited_root'),
        'baseBreakSupport':v1.support(rows,'base_break_by_inherited_root'),
        'sumReserveSpend':float(sum(float(r.get('reserve_spend_amount') or 0) for r in rows)),
        'meanReserveSpendPerRoot':float(sum(float(r.get('reserve_spend_amount') or 0) for r in rows)/len(rows)) if rows else 0.0,
        'relationCounts':dict(rel),'reserveSpendRelationCounts':dict(spendrel),'baseBreakRelationCounts':dict(breakrel)
    }


def main():
    pr=json.loads(PREREG.read_text(encoding='utf-8'));ids=[int(x) for x in pr['preOutcomeSupportConstruction']['candidateMarketIds']]
    dev_ids=ids[:90];rep_ids=ids[90:];train_ids=set(dev_ids[:60]);hold_ids=set(dev_ids[60:])
    dm,dr=run_ids(dev_ids,'development90');rm,rr=run_ids(rep_ids,'independentReplication45')
    train=[r for r in dr if int(r['marketId']) in train_ids];hold=[r for r in dr if int(r['marketId']) in hold_ids]
    primary_dev=v1.eval_binary(train,hold,'reserve_spend_by_inherited_root');primary_rep=v1.eval_binary(dr,rr,'reserve_spend_by_inherited_root')
    secondary_dev=v1.eval_binary(train,hold,'base_break_by_inherited_root');secondary_rep=v1.eval_binary(dr,rr,'base_break_by_inherited_root')
    if primary_dev.get('status')!='EVALUATED' or primary_rep.get('status')!='EVALUATED': status='INCONCLUSIVE'
    else:
        keep=(primary_dev['delta']['aucLift']>0 and primary_dev['delta']['logLossImprovement']>=0 and primary_rep['delta']['aucLift']>0 and primary_rep['delta']['logLossImprovement']>=0)
        status='KEEP' if keep else 'REJECT'
    dataset={
        'version':'R4_P0B_PROTECTION_INHERITED_RESPONSIBILITY_DATASET_V2','researchOnly':True,'actionAuthority':False,
        'preregistered':str(PREREG.relative_to(ROOT)).replace('\\','/'),'candidateMarketIds':ids,
        'developmentMarketResults':dm,'replicationMarketResults':rm,'developmentRows':dr,'replicationRows':rr,
        'guards':pr['guards']
    }
    dp=OUTDIR/'r4_p0b_protection_inherited_responsibility_dataset_v2.json';dp.write_text(json.dumps(dataset,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    result={
        'version':'R4_P0B_PROTECTION_INHERITED_RESPONSIBILITY_V2','status':status,'researchOnly':True,'actionAuthority':False,
        'question':pr['question'],'preregistered':str(PREREG.relative_to(ROOT)).replace('\\','/'),'dataset':str(dp.relative_to(ROOT)).replace('\\','/'),
        'candidateSupport':{'frozenCandidateMarkets':len(ids),'developmentCandidates':len(dev_ids),'replicationCandidates':len(rep_ids)},
        'developmentSummary':summarize(dr),'replicationSummary':summarize(rr),
        'developmentSplit':{'trainCandidateMarkets':len(train_ids),'holdoutCandidateMarkets':len(hold_ids),'trainRootRows':len(train),'holdoutRootRows':len(hold)},
        'primaryReserveSpend':{'developmentHoldout':primary_dev,'independentReplication':primary_rep},
        'secondaryBaseBreak':{'developmentHoldout':secondary_dev,'independentReplication':secondary_rep},
        'interpretation':(
            'KEEP_SIGNAL: strict-past inherited-responsibility state adds portable information beyond floor-only for reserve-consumption belief. This is belief/semantic evidence only; it grants no veto/defer action authority.' if status=='KEEP' else
            'REJECT: support was sufficient but responsibility semantics did not portably improve the reserve-consumption belief over floor-only. Do not create veto/defer authority from this representation.' if status=='REJECT' else
            'INCONCLUSIVE: even after a pre-outcome strict-past support expansion, the primary reserve-consumption target lacks the preregistered positive/negative support in holdout or replication. Do not lower support gates or mine thresholds.'
        ),
        'guards':pr['guards']
    }
    rp=OUTDIR/'r4_p0b_protection_inherited_responsibility_v2.json';rp.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    status_art={
        'version':'R4_P0B_PROTECTION_LANE_STATUS_V2','lane':'LANE_B_PROTECTION','status':status,'researchOnly':True,'actionAuthority':False,
        'question':pr['question'],'preregistered':result['preregistered'],
        'cohort':{'preOutcomeCandidateMarkets':len(ids),'development':len(dev_ids),'independentReplication':len(rep_ids)},
        'exactMetrics':{'developmentSummary':result['developmentSummary'],'replicationSummary':result['replicationSummary'],'primaryReserveSpend':result['primaryReserveSpend'],'secondaryBaseBreak':result['secondaryBaseBreak']},
        'guards':pr['guards'],
        'filesChanged':['tools/test_r4_p0b_protection_inherited_responsibility_v2.py',result['preregistered'],result['dataset'],str(rp.relative_to(ROOT)).replace('\\','/'),'data/research/r4_v0/p0_provenance_v1/r4_p0b_protection_lane_status_v2.json'],
        'priorV1':'data/research/r4_v0/p0_provenance_v1/r4_p0b_protection_inherited_responsibility_v1.json'
    }
    sp=OUTDIR/'r4_p0b_protection_lane_status_v2.json';sp.write_text(json.dumps(status_art,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'status':status,'result':str(rp.relative_to(ROOT)).replace('\\','/'),'laneStatus':str(sp.relative_to(ROOT)).replace('\\','/'),'development':result['developmentSummary'],'replication':result['replicationSummary'],'primary':result['primaryReserveSpend'],'secondary':result['secondaryBaseBreak']},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
