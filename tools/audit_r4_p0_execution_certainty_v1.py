from __future__ import annotations
import argparse, json, lzma, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from tools import test_r4_p0b_lifecycle_checkpoint_simulator_v1 as baseline_sim
from tools import test_r4_p0b_pending_submit_reservation_simulator_v1 as reservation_sim

SRC=ROOT/'data/hft_forward_paper_v1/markets'
EPS=1e-9
TERMINAL_CARRIER={'ACK_CANCELED','SUBMIT_REJECTED','IOC_TERMINAL'}
FILL_EVENTS={'PARTIAL_FILL','FULL_FILL'}

def _f(x,default=0.0):
    try:return float(x)
    except (TypeError,ValueError):return float(default)

def _iid(e):
    x=e.get('intent_id')
    return str(x) if x else None

def audit_root(mid,rid,events):
    indexed=list(enumerate(events))
    evs=[e for _,e in sorted(indexed,key=lambda z:(int(z[1].get('received_at_ms') or 0),z[0]))]
    opened=next((e for e in evs if e.get('event_type')=='RESPONSIBILITY_OPENED'),None)
    if opened is None:
        return None
    root_req=_f(opened.get('requested_qty'))
    intents={}
    exec_seen=set()
    actual_confirmed=0.0
    last_field_cum=0.0
    root_completed=False
    root_terminated=False
    counts=Counter()
    qty=Counter()
    kinds=Counter()
    examples=[]
    max_commit=0.0
    max_excess=0.0
    max_reserved=0.0
    last_seq=0
    reprice_requests={}
    reprice_children=defaultdict(list)

    def ensure_intent(iid,e):
        if iid not in intents:
            intents[iid]={
                'requested':_f(e.get('requested_qty')),
                'filled':0.0,
                'phase':'UNKNOWN',
                'parent':e.get('parent_intent_id'),
                'kind':str((e.get('extras') or {}).get('kind') or ''),
                'terminal':None,
                'cancelRequested':False
            }
        return intents[iid]

    def reserved():
        total=0.0
        state=Counter()
        for z in intents.values():
            if z['phase'] in {'PENDING_SUBMIT','LIVE','CANCEL_PENDING'}:
                rem=max(0.0,z['requested']-z['filled'])
                total+=rem
                state[z['phase']]+=rem
        return total,state

    def flag(name,e,amount=0.0,extra=None):
        counts[name]+=1
        if amount>EPS:
            qty[name]+=float(amount)
        if len(examples)<12:
            row={'type':name,'receivedAtMs':int(e.get('received_at_ms') or 0),'eventType':e.get('event_type'),'intentId':_iid(e)}
            if amount>EPS: row['qty']=float(amount)
            if extra: row.update(extra)
            examples.append(row)

    for e in evs:
        et=str(e.get('event_type') or '')
        iid=_iid(e)
        seq=int(e.get('event_seq') or 0)
        if seq<=last_seq:
            flag('EVENT_SEQ_NONMONOTONIC',e)
        last_seq=max(last_seq,seq)

        field_cum=_f(e.get('cum_confirmed_fill_qty'),last_field_cum)
        if field_cum+EPS<last_field_cum:
            flag('CUM_CONFIRMED_DECREASE',e,last_field_cum-field_cum)
        last_field_cum=max(last_field_cum,field_cum)

        if et=='CARRIER_INTENT_CREATED' and iid:
            z=ensure_intent(iid,e)
            z['requested']=_f(e.get('requested_qty'),z['requested'])
            z['parent']=e.get('parent_intent_id')
            z['kind']=str((e.get('extras') or {}).get('kind') or z.get('kind') or '')
            z['phase']='CREATED'
            kinds[z['kind'] or 'UNKNOWN']+=1
            if z['kind']=='REPRICE':
                p=str(z['parent']) if z.get('parent') else None
                if p: reprice_children[p].append(iid)
                if not p or p not in intents:
                    flag('REPRICE_PARENT_MISSING',e)
                elif intents[p]['phase'] not in {'TERMINAL','FILLED'}:
                    flag('REPRICE_BEFORE_PARENT_TERMINAL',e,max(0.0,intents[p]['requested']-intents[p]['filled']),{'parentIntentId':p,'parentPhase':intents[p]['phase']})

        elif et=='SUBMIT_SENT' and iid:
            z=ensure_intent(iid,e)
            z['requested']=_f(e.get('requested_qty'),z['requested'])
            z['phase']='PENDING_SUBMIT'

        elif et=='ACK_NEW' and iid:
            z=ensure_intent(iid,e)
            if z['phase'] in {'TERMINAL','FILLED'}:
                flag('ACK_AFTER_TERMINAL',e)
            z['phase']='LIVE'

        elif et=='CANCEL_REQUESTED' and iid:
            z=ensure_intent(iid,e)
            z['cancelRequested']=True
            z['phase']='CANCEL_PENDING'
            counts['CANCEL_REQUESTED_OBSERVED']+=1
            ex=(e.get('extras') or {})
            if ex.get('repricePx') is not None:
                reprice_requests[iid]={'receivedAtMs':int(e.get('received_at_ms') or 0),'repricePx':_f(ex.get('repricePx'))}
                counts['REPRICE_REQUESTED_OBSERVED']+=1

        elif et in FILL_EVENTS and iid:
            z=ensure_intent(iid,e)
            dq=_f((e.get('extras') or {}).get('fillDeltaQty'))
            exid=e.get('execution_id')
            if exid:
                exid=str(exid)
                if exid in exec_seen:
                    flag('DUPLICATE_EXECUTION_ID',e,dq)
                else:
                    exec_seen.add(exid)
            if root_completed:
                flag('FILL_AFTER_ROOT_COMPLETION',e,dq)
            if z['phase'] in {'TERMINAL','FILLED'}:
                flag('FILL_AFTER_CARRIER_TERMINAL',e,dq,{'priorPhase':z['phase']})
            if z['phase']=='CANCEL_PENDING':
                counts['CANCEL_FILL_RACE_OBSERVED']+=1
                qty['CANCEL_FILL_RACE_OBSERVED']+=dq
            z['filled']+=dq
            actual_confirmed+=dq
            if z['filled']>z['requested']+EPS:
                flag('INTENT_OVERFILL',e,z['filled']-z['requested'])
            expected_cum=min(root_req,actual_confirmed)
            if abs(field_cum-expected_cum)>1e-6:
                flag('CUM_CONFIRMED_MISMATCH',e,abs(field_cum-expected_cum),{'field':field_cum,'expected':expected_cum})
            if et=='FULL_FILL':
                z['phase']='FILLED'
            elif z['phase']!='CANCEL_PENDING':
                z['phase']='LIVE'

        elif et in TERMINAL_CARRIER and iid:
            z=ensure_intent(iid,e)
            if z['phase'] in {'TERMINAL','FILLED'}:
                flag('MULTIPLE_CARRIER_TERMINAL',e)
            released=max(0.0,z['requested']-z['filled'])
            qty[f'{et}_RELEASE_QTY']+=released
            counts[f'{et}_OBSERVED']+=1
            z['phase']='TERMINAL'
            z['terminal']=et

        elif et=='RESPONSIBILITY_COMPLETED':
            root_completed=True
            if actual_confirmed+EPS<root_req:
                flag('RESP_COMPLETED_EARLY',e,root_req-actual_confirmed)
            rsv,_=reserved()
            if rsv>EPS:
                flag('RESP_COMPLETED_WITH_RESERVED_CARRIER',e,rsv)

        elif et=='RESPONSIBILITY_TERMINATED':
            root_terminated=True
            rsv,_=reserved()
            if rsv>EPS:
                counts['MARKET_TERMINAL_RELEASE_OBSERVED']+=1
                qty['MARKET_TERMINAL_RELEASE_OBSERVED']+=rsv

        if e.get('leaves_qty') is not None:
            expected_leaves=max(0.0,root_req-min(root_req,actual_confirmed))
            field_leaves=_f(e.get('leaves_qty'))
            if abs(field_leaves-expected_leaves)>1e-6:
                flag('LEAVES_MISMATCH',e,abs(field_leaves-expected_leaves),{'field':field_leaves,'expected':expected_leaves})

        rsv,by_state=reserved()
        commit=actual_confirmed+rsv
        max_reserved=max(max_reserved,rsv)
        max_commit=max(max_commit,commit)
        excess=max(0.0,commit-root_req)
        if excess>EPS:
            max_excess=max(max_excess,excess)
            counts['QUOTA_COMMITMENT_EXCESS']+=1
            if by_state.get('CANCEL_PENDING',0.0)>EPS:
                counts['QUOTA_EXCESS_CANCEL_PENDING']+=1
            elif by_state.get('PENDING_SUBMIT',0.0)>EPS:
                counts['QUOTA_EXCESS_PENDING_SUBMIT']+=1
            else:
                counts['QUOTA_EXCESS_LIVE_MULTI']+=1
            if len(examples)<12:
                examples.append({'type':'QUOTA_COMMITMENT_EXCESS','receivedAtMs':int(e.get('received_at_ms') or 0),'eventType':et,'intentId':iid,'excess':excess,'commitment':commit,'rootRequested':root_req,'reservedByState':dict(by_state)})

    for parent,reqinfo in reprice_requests.items():
        pz=intents.get(parent)
        if not pz: continue
        children=reprice_children.get(parent) or []
        if children:
            counts['REPRICE_REINSERT_OBSERVED']+=len(children)
            counts['REPRICE_REQUEST_OUTCOME_REINSERTED']+=1
        elif pz.get('phase')=='FILLED' or pz.get('filled',0.0)+EPS>=pz.get('requested',0.0):
            counts['REPRICE_REQUEST_OUTCOME_PARENT_FILLED']+=1
            qty['REPRICE_REQUEST_OUTCOME_PARENT_FILLED']+=float(pz.get('filled',0.0))
        elif pz.get('terminal')=='ACK_CANCELED' and actual_confirmed+EPS>=root_req:
            counts['REPRICE_REQUEST_OUTCOME_ROOT_COMPLETED_ELSEWHERE']+=1
        elif pz.get('terminal')=='ACK_CANCELED' and actual_confirmed+EPS<root_req:
            missing=max(0.0,root_req-actual_confirmed)
            fake={'received_at_ms':reqinfo['receivedAtMs'],'event_type':'REPRICE_EXPECTED_AFTER_ACK_CANCELED','intent_id':parent}
            flag('REPRICE_REINSERT_MISSING_AFTER_CANCEL',fake,missing,{'repricePx':reqinfo['repricePx'],'parentTerminal':pz.get('terminal')})
        elif root_terminated:
            counts['REPRICE_REQUEST_OUTCOME_MARKET_TERMINATED']+=1
        else:
            counts['REPRICE_REQUEST_OUTCOME_UNRESOLVED']+=1

    over=max(0.0,actual_confirmed-root_req)
    if over>EPS:
        counts['ACTUAL_ROOT_OVERFILL']+=1
        qty['ACTUAL_ROOT_OVERFILL']+=over

    partial=any(e.get('event_type')=='PARTIAL_FILL' for e in evs)
    cancel=any(e.get('event_type')=='CANCEL_REQUESTED' for e in evs)
    ack=any(e.get('event_type')=='ACK_NEW' for e in evs)
    submit=any(e.get('event_type')=='SUBMIT_SENT' for e in evs)
    reprice=any(str((e.get('extras') or {}).get('kind') or '')=='REPRICE' for e in evs if e.get('event_type')=='CARRIER_INTENT_CREATED')
    bad_types=[k for k,v in counts.items() if v and k in {
        'QUOTA_COMMITMENT_EXCESS','ACTUAL_ROOT_OVERFILL','RESP_COMPLETED_EARLY','RESP_COMPLETED_WITH_RESERVED_CARRIER',
        'FILL_AFTER_CARRIER_TERMINAL','FILL_AFTER_ROOT_COMPLETION','INTENT_OVERFILL','DUPLICATE_EXECUTION_ID',
        'REPRICE_PARENT_MISSING','REPRICE_BEFORE_PARENT_TERMINAL','REPRICE_REINSERT_MISSING_AFTER_CANCEL','ACK_AFTER_TERMINAL','EVENT_SEQ_NONMONOTONIC',
        'CUM_CONFIRMED_DECREASE','CUM_CONFIRMED_MISMATCH','LEAVES_MISMATCH','MULTIPLE_CARRIER_TERMINAL'}]
    return {
        'marketId':int(mid),'responsibilityId':str(rid),'requestedQty':root_req,
        'actualConfirmedQty':actual_confirmed,'actualOverfillQty':over,
        'intentCount':sum(1 for e in evs if e.get('event_type')=='CARRIER_INTENT_CREATED'),
        'eventCount':len(evs),'maxReservedOutstandingQty':max_reserved,'maxEconomicCommitmentQty':max_commit,
        'maxCommitmentExcessQty':max_excess,'rootCompleted':root_completed,'rootTerminated':root_terminated,
        'support':{'submitSent':submit,'ackNew':ack,'partialFill':partial,'cancelRequested':cancel,
                   'cancelFillRace':counts['CANCEL_FILL_RACE_OBSERVED']>0,'reprice':reprice or counts['REPRICE_REQUESTED_OBSERVED']>0,
                   'marketTerminalRelease':counts['MARKET_TERMINAL_RELEASE_OBSERVED']>0},
        'intentKinds':dict(kinds),'counts':dict(counts),'qty':dict(qty),'violationTypes':sorted(bad_types),'examples':examples
    }

def audit_journal(mid,events):
    by=defaultdict(list)
    for e in events:
        rid=e.get('responsibility_id')
        if rid: by[str(rid)].append(e)
    roots=[]
    for rid,evs in by.items():
        x=audit_root(mid,rid,evs)
        if x is not None: roots.append(x)
    return roots

def aggregate(rows,errors,mode,policy):
    roots=[r for row in rows for r in row['roots']]
    ctr=Counter()
    q=Counter()
    markets_by=defaultdict(set)
    support=Counter()
    bad_roots=0
    bad_markets=set()
    for r in roots:
        if r['violationTypes']:
            bad_roots+=1;bad_markets.add(r['marketId'])
        for k,v in r['counts'].items():
            ctr[k]+=v
            if v: markets_by[k].add(r['marketId'])
        for k,v in r['qty'].items(): q[k]+=float(v)
        for k,v in r['support'].items():
            if v:support[k]+=1
    return {
        'mode':mode,'policy':policy,'markets':len(rows),'errors':len(errors),'roots':len(roots),
        'rootsWithAnyStructuralViolation':bad_roots,'marketsWithAnyStructuralViolation':len(bad_markets),
        'rootViolationRate':bad_roots/len(roots) if roots else None,
        'eventOrRaceCounts':dict(ctr),'quantities':dict(q),
        'marketsBySignal':{k:len(v) for k,v in markets_by.items()},
        'supportRoots':dict(support),
        'maxCommitmentExcessQtyAcrossRoots':max([r['maxCommitmentExcessQty'] for r in roots],default=0.0),
        'sumRootPeakCommitmentExcessQty':sum(r['maxCommitmentExcessQty'] for r in roots),
        'actualRootOverfillRoots':sum(r['actualOverfillQty']>EPS for r in roots),
        'actualRootOverfillQty':sum(r['actualOverfillQty'] for r in roots),
        'examples':[{'marketId':r['marketId'],'responsibilityId':r['responsibilityId'],'violationTypes':r['violationTypes'],'examples':r['examples']} for r in roots if r['violationTypes']][:20]
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--cohort-artifact',required=True)
    ap.add_argument('--mode',choices=['baseline','reservation'],required=True)
    ap.add_argument('--policy',required=True)
    ap.add_argument('--start',type=int,default=0)
    ap.add_argument('--count',type=int,default=0)
    ap.add_argument('--out',required=True)
    a=ap.parse_args()
    pre=json.loads((ROOT/a.cohort_artifact).read_text(encoding='utf-8'))
    ids=[int(x) for x in pre['cohort']]
    if a.count>0: ids=ids[a.start:a.start+a.count]
    else: ids=ids[a.start:]
    sim=baseline_sim if a.mode=='baseline' else reservation_sim
    rows=[];errors=[]
    for j,mid in enumerate(ids,1):
        p=SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz'
        try:
            d=json.load(lzma.open(p,'rt',encoding='utf-8'))
            r=sim.simulate(d,a.policy,collect_shadow=False,collect_provenance=True)
            roots=audit_journal(mid,r['provenanceJournal'])
            rows.append({'marketId':mid,'makerFilledShares':r.get('makerFilledShares'),'rootCount':len(roots),'roots':roots})
            print(json.dumps({'progress':j,'markets':len(ids),'marketId':mid,'roots':len(roots),'badRoots':sum(bool(x['violationTypes']) for x in roots)},ensure_ascii=False),flush=True)
        except Exception as e:
            errors.append({'marketId':mid,'error':f'{type(e).__name__}: {e}'})
            print(json.dumps({'progress':j,'markets':len(ids),'marketId':mid,'error':errors[-1]['error']},ensure_ascii=False),flush=True)
    agg=aggregate(rows,errors,a.mode,a.policy)
    out=ROOT/a.out
    out.parent.mkdir(parents=True,exist_ok=True)
    rep={'version':'R4_P0_EXECUTION_CERTAINTY_AUDIT_V1','researchOnly':True,
         'cohortArtifact':a.cohort_artifact,'start':a.start,'countRequested':a.count,'ids':ids,
         'mode':a.mode,'policy':a.policy,'aggregate':agg,'errors':errors,'rows':rows}
    out.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'aggregate':agg},ensure_ascii=False),flush=True)

if __name__=='__main__':
    main()
