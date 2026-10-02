from __future__ import annotations
import json,sys
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_protection_inherited_responsibility_v1 as v1
PREREG=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_protection_inherited_responsibility_preregistered_v2.json'; OUT=ROOT/'data/research/r4_v0/p0_provenance_v1'

def summ(rows):
 rel=Counter(str(r.get('relation')) for r in rows); sr=Counter(str(r.get('relation')) for r in rows if int(r.get('reserve_spend_by_inherited_root') or 0)); br=Counter(str(r.get('relation')) for r in rows if int(r.get('base_break_by_inherited_root') or 0)); vals=[float(r.get('reserve_spend_amount') or 0) for r in rows]
 return {'rows':len(rows),'markets':len(set(int(r['marketId']) for r in rows)),'reserveSpendSupport':v1.support(rows,'reserve_spend_by_inherited_root'),'baseBreakSupport':v1.support(rows,'base_break_by_inherited_root'),'sumReserveSpend':float(sum(vals)),'meanReserveSpendPerRoot':float(sum(vals)/len(vals)) if vals else 0.0,'relationCounts':dict(rel),'reserveSpendRelationCounts':dict(sr),'baseBreakRelationCounts':dict(br)}

def main():
 pr=json.loads(PREREG.read_text(encoding='utf-8'));ids=[int(x) for x in pr['preOutcomeSupportConstruction']['candidateMarketIds']]; files=sorted(OUT.glob('r4_p0b_protection_inherited_responsibility_v2_chunk_*.json'))
 byid={}; rows=[]
 for p in files:
  d=json.loads(p.read_text(encoding='utf-8'))
  for m in d['marketResults']:byid[int(m['marketId'])]=m
  rows.extend(d['rows'])
 missing=[m for m in ids if m not in byid]
 if missing: raise SystemExit('MISSING_CHUNKS:'+','.join(map(str,missing)))
 dev_ids=ids[:90];rep_ids=ids[90:];train_ids=set(dev_ids[:60]);hold_ids=set(dev_ids[60:]);devset=set(dev_ids);repset=set(rep_ids)
 dr=[r for r in rows if int(r['marketId']) in devset];rr=[r for r in rows if int(r['marketId']) in repset];tr=[r for r in dr if int(r['marketId']) in train_ids];ho=[r for r in dr if int(r['marketId']) in hold_ids]
 pd=v1.eval_binary(tr,ho,'reserve_spend_by_inherited_root');prp=v1.eval_binary(dr,rr,'reserve_spend_by_inherited_root');bd=v1.eval_binary(tr,ho,'base_break_by_inherited_root');brp=v1.eval_binary(dr,rr,'base_break_by_inherited_root')
 if pd.get('status')!='EVALUATED' or prp.get('status')!='EVALUATED':status='INCONCLUSIVE'
 else:
  keep=pd['delta']['aucLift']>0 and pd['delta']['logLossImprovement']>=0 and prp['delta']['aucLift']>0 and prp['delta']['logLossImprovement']>=0;status='KEEP' if keep else 'REJECT'
 dataset={'version':'R4_P0B_PROTECTION_INHERITED_RESPONSIBILITY_DATASET_V2','researchOnly':True,'actionAuthority':False,'preregistered':str(PREREG.relative_to(ROOT)).replace('\\','/'),'candidateMarketIds':ids,'developmentMarketResults':[byid[m] for m in dev_ids],'replicationMarketResults':[byid[m] for m in rep_ids],'developmentRows':dr,'replicationRows':rr,'sourceChunks':[str(p.relative_to(ROOT)).replace('\\','/') for p in files],'guards':pr['guards']}
 dp=OUT/'r4_p0b_protection_inherited_responsibility_dataset_v2.json';dp.write_text(json.dumps(dataset,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
 result={'version':'R4_P0B_PROTECTION_INHERITED_RESPONSIBILITY_V2','status':status,'researchOnly':True,'actionAuthority':False,'question':pr['question'],'preregistered':str(PREREG.relative_to(ROOT)).replace('\\','/'),'dataset':str(dp.relative_to(ROOT)).replace('\\','/'),'candidateSupport':{'frozenCandidateMarkets':len(ids),'developmentCandidates':90,'replicationCandidates':45},'developmentSummary':summ(dr),'replicationSummary':summ(rr),'developmentSplit':{'trainCandidateMarkets':60,'holdoutCandidateMarkets':30,'trainRootRows':len(tr),'holdoutRootRows':len(ho)},'primaryReserveSpend':{'developmentHoldout':pd,'independentReplication':prp},'secondaryBaseBreak':{'developmentHoldout':bd,'independentReplication':brp},'interpretation':('KEEP_SIGNAL: strict-past inherited-responsibility state adds portable information beyond floor-only for reserve-consumption belief. Belief/semantic evidence only; no veto/defer authority.' if status=='KEEP' else 'REJECT: support sufficient but responsibility semantics did not portably improve reserve-consumption belief over floor-only. No veto/defer authority.' if status=='REJECT' else 'INCONCLUSIVE: primary reserve-consumption target lacks preregistered support. Do not lower support gates or mine thresholds.'),'guards':pr['guards']}
 rp=OUT/'r4_p0b_protection_inherited_responsibility_v2.json';rp.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
 stat={'version':'R4_P0B_PROTECTION_LANE_STATUS_V2','lane':'LANE_B_PROTECTION','status':status,'researchOnly':True,'actionAuthority':False,'question':pr['question'],'preregistered':result['preregistered'],'cohort':{'preOutcomeCandidateMarkets':135,'development':90,'independentReplication':45},'exactMetrics':{'developmentSummary':result['developmentSummary'],'replicationSummary':result['replicationSummary'],'primaryReserveSpend':result['primaryReserveSpend'],'secondaryBaseBreak':result['secondaryBaseBreak']},'guards':pr['guards'],'filesChanged':['tools/test_r4_p0b_protection_inherited_responsibility_v2.py','tools/test_r4_p0b_protection_inherited_responsibility_v2_chunk.py','tools/test_r4_p0b_protection_inherited_responsibility_v2_aggregate.py',result['preregistered'],result['dataset'],str(rp.relative_to(ROOT)).replace('\\','/'),'data/research/r4_v0/p0_provenance_v1/r4_p0b_protection_lane_status_v2.json'],'priorV1':'data/research/r4_v0/p0_provenance_v1/r4_p0b_protection_inherited_responsibility_v1.json'}
 sp=OUT/'r4_p0b_protection_lane_status_v2.json';sp.write_text(json.dumps(stat,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
 print(json.dumps({'status':status,'result':str(rp.relative_to(ROOT)).replace('\\','/'),'laneStatus':str(sp.relative_to(ROOT)).replace('\\','/'),'development':result['developmentSummary'],'replication':result['replicationSummary'],'primary':result['primaryReserveSpend'],'secondary':result['secondaryBaseBreak']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
