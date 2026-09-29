from __future__ import annotations
import argparse,json,lzma,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_marginal_successor_probe_simulator_v1 as additive_sim
from tools import test_r4_p0b_successor_credit_probe_simulator_v1 as credit_sim
P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=ROOT/'data/hft_forward_paper_v1/markets'
PREFIX_KEYS=['candidateT','parentLogical','candidateSide','candidateGap','candidateFloor','candidateAbsNet','candidateUpside','candidateReservedQty','candidateReservedRootCount','candidatePx']
EPS=1e-9

def load_candidates(split):
    out=[]
    for start in range(0,120,20):
        p=P/f'r4_p0b_role_group_discovery_{split}_{start}_20_v1.json'
        d=json.loads(p.read_text(encoding='utf-8'))
        for m in d['rows']:
            for op in m['opportunities']:
                out.append({'marketId':int(m['marketId']),'baselineOp':op,'baselineCore':m['baselineCore']})
    return sorted(out,key=lambda x:(x['marketId'],int(x['baselineOp']['candidateT']),str(x['baselineOp']['candidateKey'])),reverse=True)

def match_op(r,key):
    xs=[x for x in (r.get('successorOpportunityRows') or []) if str(x.get('candidateKey'))==str(key)]
    if len(xs)!=1: raise RuntimeError(f'candidate match {len(xs)} key={key}')
    return xs[0]

def succ_info(r,b):
    t=int(b['candidateT']);
    created=[e for e in r['provenanceJournal'] if int(e.get('received_at_ms') or 0)==t and e.get('event_type')=='CARRIER_INTENT_CREATED' and (e.get('extras') or {}).get('kind')=='SUCCESSOR_OPTION']
    rid=created[0]['responsibility_id'] if created else None
    ev=[e for e in r['provenanceJournal'] if rid and e.get('responsibility_id')==rid]
    fills=[e for e in ev if e.get('event_type') in {'PARTIAL_FILL','FULL_FILL'}]
    qty=sum(float((e.get('extras') or {}).get('fillDeltaQty') or 0.) for e in fills)
    first=None if not fills else min(int(e.get('received_at_ms') or 0) for e in fills)
    return {'root':rid,'fillQty':qty,'firstFillMs':first,'firstFillDelayMs':None if first is None else first-t}

def dominates(a,b):
    floor_ok=a['floor']>=b['floor']-EPS; abs_ok=a['absNet']<=b['absNet']+EPS
    strict=a['floor']>b['floor']+EPS or a['absNet']<b['absNet']-EPS
    return floor_ok and abs_ok and strict

def role_label(base,add,cred,realized,valid):
    if not valid:return 'INVALID_BRANCH_DIVERGENCE'
    if not realized:return 'EXECUTION_NO_REALIZATION'
    wins=[]
    if dominates(base,add) and dominates(base,cred):wins.append('REJECT_NO_ACTION')
    if dominates(cred,base) and dominates(cred,add):wins.append('PREPOSITION_REPAIR_SUBSTITUTE')
    if dominates(add,base) and dominates(add,cred):wins.append('PARALLEL_STATE_SHAPING')
    return wins[0] if len(wins)==1 else 'AMBIGUOUS_TRADEOFF'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--split',choices=['development','independentReplication'],required=True);ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,required=True);a=ap.parse_args()
    cs=load_candidates(a.split)[a.start:a.start+a.count];rows=[]
    for c in cs:
        mid=c['marketId'];b=c['baselineOp'];key=b['candidateKey'];d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
        add=additive_sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode='ONE',successor_target=key)
        cred=credit_sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode='ONE',successor_target=key)
        ao=match_op(add,key);co=match_op(cred,key)
        apx=all(b.get(k)==ao.get(k) for k in PREFIX_KEYS);cpx=all(b.get(k)==co.get(k) for k in PREFIX_KEYS)
        ai=succ_info(add,b);ci=succ_info(cred,b)
        samefill=abs(ai['fillQty']-ci['fillQty'])<=EPS and ai['firstFillMs']==ci['firstFillMs']
        valid=bool(apx and cpx and samefill)
        basef={k:float(c['baselineCore']['final'][k]) for k in ('floor','absNet','upside')}
        addf={k:float(add['final'][k]) for k in ('floor','absNet','upside')}
        credf={k:float(cred['final'][k]) for k in ('floor','absNet','upside')}
        role=role_label(basef,addf,credf,ai['fillQty']>EPS,valid)
        horizons={}
        for hz in ('5s','15s','30s'):
            horizons[hz]={}
            for name,o in [('ADDITIVE',ao),('CREDIT',co)]:
                horizons[hz][name]={}
                for metric in ('Floor','AbsNet','Upside'):
                    k=f'realized{metric}Delta{hz}';horizons[hz][name][metric[0].lower()+metric[1:]]=float(o.get(k,0.))-float(b.get(k,0.))
        row={'marketId':mid,'candidateKey':key,'candidate':{k:b.get(k) for k in b},'prefixExact':{'additive':apx,'credit':cpx},'successorFill':{'additive':ai,'credit':ci,'exact':samefill},'branchValid':valid,'role':role,'final':{'REJECT_NO_ACTION':basef,'ADDITIVE':addf,'CREDIT':credf},'horizons':horizons,'credit':{'events':cred.get('successorCreditEvents') or [],'outstanding':float(cred.get('successorCreditOutstanding') or 0.)}}
        rows.append(row)
        print(json.dumps({'marketId':mid,'candidateKey':key,'valid':valid,'fill':ai['fillQty'],'role':role,'final':row['final'],'creditUsed':sum(float(x.get('qty') or 0.) for x in row['credit']['events'])},ensure_ascii=False),flush=True)
    out=P/f'r4_p0b_role_group_branches_{a.split}_{a.start}_{a.count}_v1.json';out.write_text(json.dumps({'split':a.split,'rows':rows},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(out.relative_to(ROOT)),'rows':len(rows),'valid':sum(x['branchValid'] for x in rows)},ensure_ascii=False))
if __name__=='__main__':main()
