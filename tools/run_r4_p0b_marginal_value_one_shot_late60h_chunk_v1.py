from __future__ import annotations
import argparse,json,lzma,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_marginal_successor_probe_simulator_v1 as probe
P=ROOT/'data/research/r4_v0/p0_provenance_v1'; SRC=ROOT/'data/hft_forward_paper_v1/markets'
PREFIX_KEYS=['candidateT','parentLogical','candidateSide','candidateGap','candidateFloor','candidateAbsNet','candidateUpside','candidateReservedQty','candidateReservedRootCount','candidatePx']

def load_candidates():
 out=[]
 for start in range(0,60,5):
  d=json.loads((P/f'r4_p0b_marginal_value_discovery_late60h_chunk_{start}_5.json').read_text(encoding='utf-8'))
  for m in d['rows']:
   for op in m['opportunities']:
    out.append({'marketId':int(m['marketId']),'baselineOp':op})
 return sorted(out,key=lambda x:(x['marketId'],int(x['baselineOp']['candidateT']),str(x['baselineOp']['candidateKey'])),reverse=True)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,required=True);a=ap.parse_args(); cand=load_candidates()[a.start:a.start+a.count]; rows=[]
 for c in cand:
  mid=c['marketId']; b=c['baselineOp']; d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
  o=probe.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode='ONE',successor_target=b['candidateKey'])
  matches=[x for x in o.get('successorOpportunityRows',[]) if x.get('candidateKey')==b['candidateKey']]
  if len(matches)!=1: raise RuntimeError(f'{mid} target match {len(matches)}')
  v=matches[0]; prefix=all(b.get(k)==v.get(k) for k in PREFIX_KEYS)
  created=[e for e in o['provenanceJournal'] if int(e.get('received_at_ms') or 0)>=int(b['candidateT']) and e.get('event_type')=='CARRIER_INTENT_CREATED' and (e.get('extras') or {}).get('kind')=='SUCCESSOR_OPTION']
  created=[e for e in created if int(e.get('received_at_ms') or 0)==int(b['candidateT'])]
  rid=created[0]['responsibility_id'] if created else None
  sev=[e for e in o['provenanceJournal'] if rid and e.get('responsibility_id')==rid]
  fills=[e for e in sev if e.get('event_type') in {'PARTIAL_FILL','FULL_FILL'}]
  row={'marketId':mid,'candidateKey':b['candidateKey'],'prefixExact':prefix,'successorRoot':rid,'successorCreated':bool(rid),'successorFillQty':sum(float((e.get('extras') or {}).get('fillDeltaQty') or 0.) for e in fills),'firstFillDelayMs':None if not fills else min(int(e.get('received_at_ms') or 0) for e in fills)-int(b['candidateT']),'features':{k:v.get(k) for k in v.keys() if k not in {'realizedFloorDelta5s','realizedAbsNetDelta5s','realizedUpsideDelta5s','realizedFloorDelta15s','realizedAbsNetDelta15s','realizedUpsideDelta15s','realizedFloorDelta30s','realizedAbsNetDelta30s','realizedUpsideDelta30s','realizedFinalFloorDelta','realizedFinalAbsNetDelta','realizedFinalUpsideDelta'}},'marginal':{}}
  for hz in ('5s','15s','30s'):
   for metric in ('Floor','AbsNet','Upside'):
    key=f'realized{metric}Delta{hz}'; row['marginal'][f'{metric[0].lower()+metric[1:]}Delta{hz}']=float(v.get(key,0.))-float(b.get(key,0.))
  for metric in ('Floor','AbsNet','Upside'):
   key=f'realizedFinal{metric}Delta'; row['marginal'][f'final{metric}Delta']=float(v.get(key,0.))-float(b.get(key,0.))
  rows.append(row); print(json.dumps({'marketId':mid,'candidateKey':b['candidateKey'],'prefixExact':prefix,'fillQty':row['successorFillQty'],'firstFillDelayMs':row['firstFillDelayMs'],'marginal':row['marginal']},ensure_ascii=False),flush=True)
 out=P/f'r4_p0b_marginal_value_one_shot_late60h_chunk_{a.start}_{a.count}.json';out.write_text(json.dumps({'rows':rows},ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(out.relative_to(ROOT)),'rows':len(rows),'prefixExact':sum(x['prefixExact'] for x in rows)},ensure_ascii=False))
if __name__=='__main__':main()
