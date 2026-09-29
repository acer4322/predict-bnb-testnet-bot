from __future__ import annotations
import json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
OUT=P/'r4_p0b_objective_deficit_ledger_contract_smoke_v1.json'
EPS=1e-9

def dig(*x): return hashlib.sha256('|'.join(map(str,x)).encode()).hexdigest()[:20]

def reduce_events(events):
    st={}
    for e in events:
        oid=e['portfolio_objective_id']; typ=e['event_type']
        z=st.setdefault(oid,{'gross':0.0,'confirmed':0.0,'reserved':0.0,'credited':0.0,'responsibility_ids':set(),'status':'OPEN'})
        if typ=='OBJECTIVE_OPENED': z['gross']=float(e['gross_objective_deficit_qty'])
        elif typ=='RESPONSIBILITY_ATTACHED': z['responsibility_ids'].add(e['responsibility_id'])
        elif typ=='OBJECTIVE_RESERVATION_UPDATED': z['reserved']=float(e['reserved_same_objective_commitment_qty'])
        elif typ=='OBJECTIVE_COMPLETION_UPDATED': z['confirmed']=float(e['confirmed_same_objective_completion_qty'])
        elif typ=='OBJECTIVE_CREDIT_APPLIED': z['credited']=float(e['credited_same_objective_completion_qty'])
        elif typ=='OBJECTIVE_COMPLETED': z['status']='COMPLETED'
        elif typ=='OBJECTIVE_TERMINATED': z['status']='TERMINATED'
        z['residual']=max(0.0,z['gross']-z['confirmed']-z['reserved']-z['credited'])
    for z in st.values(): z['responsibility_ids']=sorted(z['responsibility_ids'])
    return st

def ev(seq,oid,typ,**kw): return {'event_seq':seq,'portfolio_objective_id':oid,'event_type':typ,**kw}

def main():
    # Synthetic contract exams only: verifies deterministic reducer semantics, not objective inference.
    oa='obj:'+dig('A'); ob='obj:'+dig('B'); r1='resp:'+dig('r1'); r2='resp:'+dig('r2'); r3='resp:'+dig('r3')
    pre=[
      ev(1,oa,'OBJECTIVE_OPENED',gross_objective_deficit_qty=36),
      ev(2,oa,'RESPONSIBILITY_ATTACHED',responsibility_id=r1),
      ev(3,oa,'OBJECTIVE_RESERVATION_UPDATED',reserved_same_objective_commitment_qty=18),
      ev(4,oa,'RESPONSIBILITY_ATTACHED',responsibility_id=r2),
      ev(5,oa,'OBJECTIVE_COMPLETION_UPDATED',confirmed_same_objective_completion_qty=18),
      ev(6,oa,'OBJECTIVE_RESERVATION_UPDATED',reserved_same_objective_commitment_qty=0),
      ev(7,oa,'OBJECTIVE_CREDIT_APPLIED',credited_same_objective_completion_qty=18),
      ev(8,oa,'OBJECTIVE_COMPLETED')
    ]
    shaping=[
      ev(1,oa,'OBJECTIVE_OPENED',gross_objective_deficit_qty=18),
      ev(2,oa,'RESPONSIBILITY_ATTACHED',responsibility_id=r1),
      ev(3,oa,'OBJECTIVE_RESERVATION_UPDATED',reserved_same_objective_commitment_qty=18),
      ev(4,ob,'OBJECTIVE_OPENED',gross_objective_deficit_qty=18),
      ev(5,ob,'RESPONSIBILITY_ATTACHED',responsibility_id=r3),
      ev(6,ob,'OBJECTIVE_RESERVATION_UPDATED',reserved_same_objective_commitment_qty=18)
    ]
    a=reduce_events(pre); b=reduce_events(shaping)
    checks={
      'preposition_same_objective_multiple_roots': set(a[oa]['responsibility_ids'])=={r1,r2},
      'preposition_credit_closes_residual': abs(a[oa]['residual'])<=EPS,
      'preposition_completed': a[oa]['status']=='COMPLETED',
      'state_shaping_distinct_objectives': set(b)=={oa,ob},
      'state_shaping_no_cross_credit': all(abs(z['credited'])<=EPS for z in b.values()),
      'state_shaping_each_reserved': all(abs(z['reserved']-18)<=EPS for z in b.values()),
    }
    # same input repeatability
    repeat=all(reduce_events(pre)==a for _ in range(10)) and all(reduce_events(shaping)==b for _ in range(10))
    checks['repeatability_10x']=repeat
    art={'version':'R4_P0B_OBJECTIVE_DEFICIT_LEDGER_CONTRACT_SMOKE_V1','status':'CONTRACT_SMOKE_PASS' if all(checks.values()) else 'CONTRACT_SMOKE_FAIL','checks':checks,'prepositionState':a,'stateShapingState':b,'note':'This exam validates ledger accounting semantics only. It does not infer objective identity from current-market features or future role labels.'}
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':art['status'],'checks':checks},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
