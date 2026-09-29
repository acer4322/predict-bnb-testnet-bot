from __future__ import annotations
import argparse,glob,json,lzma,math,sys
from collections import defaultdict,Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_pending_submit_reservation_simulator_v1 as reservation
from tools import test_r4_p0b_marginal_successor_probe_simulator_v1 as additive_sim
from tools import test_r4_p0b_successor_credit_probe_simulator_v1 as credit_sim
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
EPS=1e-9
ROLES={'REJECT_NO_ACTION','PREPOSITION_REPAIR_SUBSTITUTE','PARALLEL_STATE_SHAPING'}
TERMINAL={'FULL_FILL','ACK_CANCELED','SUBMIT_REJECTED','IOC_TERMINAL'}

def load_role_rows(include_ambiguous=False):
    rows=[]
    for split,name in [('development','r4_p0b_role_group_development_v1.json'),('independentReplication','r4_p0b_role_group_replication_v1.json')]:
        d=json.loads((P/name).read_text(encoding='utf-8'))
        for r in d.get('rows',[]):
            r=dict(r);r['_source']=f'role_group_{split}';rows.append(r)
    for fn in sorted(P.glob('r4_p0b_role_anatomy_extension_branches_*_v1.json')):
        d=json.loads(fn.read_text(encoding='utf-8'))
        for r in d.get('rows',[]):
            r=dict(r);r['_source']='role_anatomy_extension';rows.append(r)
    out=[];seen=set()
    for r in rows:
        key=(int(r['marketId']),str(r['candidateKey']))
        if key in seen:continue
        seen.add(key)
        if not bool(r.get('branchValid')):continue
        q=float((((r.get('successorFill') or {}).get('additive') or {}).get('fillQty')) or 0.)
        if q<=EPS:continue
        role=str(r.get('role') or '')
        if role not in ROLES and not (include_ambiguous and role=='AMBIGUOUS_TRADEOFF'):continue
        out.append(r)
    return sorted(out,key=lambda r:(int(r['marketId']),str(r['candidateKey'])),reverse=True)

def find_op(r,key):
    xs=[x for x in (r.get('successorOpportunityRows') or []) if str(x.get('candidateKey'))==str(key)]
    if len(xs)!=1:raise RuntimeError(f'candidate match {len(xs)} {key}')
    return xs[0]

def parse_takers(d):
    out=[]
    for x in d.get('takerEvents') or []:
        q=float(x.get('shares') or x.get('filledShares') or x.get('deltaShares') or x.get('qty') or 0.)
        side=str(x.get('side') or '').upper();t=int(x.get('observedAtMs') or x.get('atMs') or x.get('fillMs') or 0)
        if q>EPS and side in {'UP','DOWN'} and t:out.append({'t':t,'side':side,'qty':q,'kind':'TAKER'})
    return out

def share_events(d,journal):
    out=[]
    for e in journal:
        if e.get('event_type') not in {'PARTIAL_FILL','FULL_FILL'}:continue
        q=float((e.get('extras') or {}).get('fillDeltaQty') or 0.)
        if q<=EPS:continue
        out.append({'t':int(e.get('received_at_ms') or 0),'side':str(e.get('side')),'qty':q,'kind':'MAKER','root':e.get('responsibility_id'),'intent':e.get('intent_id')})
    out.extend(parse_takers(d))
    # harvest maker fills occurs before same-timestamp TAKER actions in the simulator.
    return sorted(out,key=lambda x:(int(x['t']),0 if x['kind']=='MAKER' else 1,str(x.get('root') or '')))

def weak_side(up,down):
    if abs(up-down)<=EPS:return None
    return 'UP' if up<down else 'DOWN'

def trajectory(d,journal,candidate_t,candidate_side):
    up=down=0.;prev=None;episode_start=None;first_boundary=None;points=[]
    for e in share_events(d,journal):
        if e['side']=='UP':up+=e['qty']
        else:down+=e['qty']
        w=weak_side(up,down);t=int(e['t'])
        if w==candidate_side and prev!=candidate_side and t<=candidate_t:episode_start=t
        if t>candidate_t and first_boundary is None and w!=candidate_side:first_boundary=t
        points.append({'t':t,'up':up,'down':down,'gap':abs(up-down),'weakSide':w,'eventSide':e['side'],'qty':e['qty'],'kind':e['kind']})
        prev=w
    at=[x for x in points if x['t']<=candidate_t]
    cp=at[-1] if at else {'up':0.,'down':0.,'gap':0.,'weakSide':None}
    return {'episodeStartT':episode_start,'firstBoundaryT':first_boundary,'candidateReconstructed':cp,'points':points}

def state_at(traj,t):
    xs=[x for x in traj['points'] if int(x['t'])<=int(t)]
    return xs[-1] if xs else {'t':None,'up':0.,'down':0.,'gap':0.,'weakSide':None}

def state_before(traj,t):
    xs=[x for x in traj['points'] if int(x['t'])<int(t)]
    return xs[-1] if xs else {'t':None,'up':0.,'down':0.,'gap':0.,'weakSide':None}

def logical_carriers(journal,logical):
    roots=[r for r in root_snapshot(journal,None) if str(r.get('logical'))==str(logical)]
    ids={str(r['responsibilityId']) for r in roots}; out=[]
    for e in journal:
        if str(e.get('responsibility_id')) not in ids or e.get('event_type')!='SUBMIT_SENT':continue
        out.append({'t':int(e.get('received_at_ms') or 0),'qty':float(e.get('requested_qty') or 0.),'kind':str((e.get('extras') or {}).get('kind') or ''),'root':e.get('responsibility_id'),'intent':e.get('intent_id')})
    return sorted(out,key=lambda x:(x['t'],x['kind']))

def logical_first_fill(journal,logical,after_t):
    roots=[r for r in root_snapshot(journal,None) if str(r.get('logical'))==str(logical)];ids={str(r['responsibilityId']) for r in roots}
    xs=[e for e in journal if str(e.get('responsibility_id')) in ids and e.get('event_type') in {'PARTIAL_FILL','FULL_FILL'} and int(e.get('received_at_ms') or 0)>int(after_t)]
    if not xs:return None
    return min(int(e.get('received_at_ms') or 0) for e in xs)

def root_snapshot(journal,cutoff=None):
    roots={};intents={}
    seq=journal if cutoff is None else journal[:int(cutoff)]
    for e in seq:
        rid=str(e.get('responsibility_id') or '');et=str(e.get('event_type') or '');iid=str(e.get('intent_id') or '')
        if not rid:continue
        r=roots.setdefault(rid,{'responsibilityId':rid,'logical':None,'side':str(e.get('side') or ''),'requestedQty':0.,'confirmedQty':0.,'openedAt':None,'needMs':None,'completedAt':None,'terminatedAt':None,'intents':set()})
        if et=='RESPONSIBILITY_OPENED':
            r['logical']=str((e.get('extras') or {}).get('logical'));r['requestedQty']=float(e.get('requested_qty') or 0.);r['openedAt']=int(e.get('received_at_ms') or 0);r['needMs']=(e.get('extras') or {}).get('needMs')
        if iid:r['intents'].add(iid)
        if et=='CARRIER_INTENT_CREATED' and r['logical'] is None:
            lg=(e.get('extras') or {}).get('logical');r['logical']=None if lg is None else str(lg)
        if et=='SUBMIT_SENT' and iid:
            z=intents.setdefault(iid,{'intentId':iid,'root':rid,'state':'PENDING_SUBMIT','requestedQty':float(e.get('requested_qty') or 0.),'filledQty':0.,'submitAt':int(e.get('received_at_ms') or 0),'kind':str((e.get('extras') or {}).get('kind') or '')})
            z['state']='PENDING_SUBMIT'
        elif et=='ACK_NEW' and iid:
            z=intents.setdefault(iid,{'intentId':iid,'root':rid,'state':'LIVE','requestedQty':float(e.get('requested_qty') or 0.),'filledQty':0.,'submitAt':None,'kind':''});z['state']='LIVE'
        elif et=='CANCEL_REQUESTED' and iid:
            z=intents.setdefault(iid,{'intentId':iid,'root':rid,'state':'CANCEL_PENDING','requestedQty':float(e.get('requested_qty') or 0.),'filledQty':0.,'submitAt':None,'kind':''});z['state']='CANCEL_PENDING'
        elif et in {'PARTIAL_FILL','FULL_FILL'} and iid:
            z=intents.setdefault(iid,{'intentId':iid,'root':rid,'state':'LIVE','requestedQty':float(e.get('requested_qty') or 0.),'filledQty':0.,'submitAt':None,'kind':''})
            q=float((e.get('extras') or {}).get('fillDeltaQty') or 0.);z['filledQty']+=q
            r['confirmedQty']=max(float(r['confirmedQty']),float(e.get('cum_confirmed_fill_qty') or 0.))
            if et=='FULL_FILL':z['state']='TERMINAL_FILLED'
        elif et in {'ACK_CANCELED','SUBMIT_REJECTED','IOC_TERMINAL'} and iid:
            z=intents.setdefault(iid,{'intentId':iid,'root':rid,'state':'TERMINAL','requestedQty':float(e.get('requested_qty') or 0.),'filledQty':0.,'submitAt':None,'kind':''});z['state']='TERMINAL_'+et
        if et=='RESPONSIBILITY_COMPLETED':r['completedAt']=int(e.get('received_at_ms') or 0)
        if et=='RESPONSIBILITY_TERMINATED':r['terminatedAt']=int(e.get('received_at_ms') or 0)
    out=[]
    for rid,r in roots.items():
        ack=pending=cancel=0.;active=[];pending_ids=[]
        for iid in r['intents']:
            z=intents.get(iid); 
            if not z:continue
            rem=max(0.,float(z['requestedQty'])-float(z['filledQty']))
            if z['state']=='LIVE':ack+=rem;active.append(iid)
            elif z['state']=='PENDING_SUBMIT':pending+=rem;pending_ids.append(iid)
            elif z['state']=='CANCEL_PENDING':cancel+=rem;active.append(iid)
        rr={k:v for k,v in r.items() if k!='intents'}
        rr.update({'ackedCommitment':ack,'pendingSubmitCommitment':pending,'cancelPendingCommitment':cancel,'reservedCommitment':ack+pending+cancel,'unresolvedQty':max(0.,float(r['requestedQty'])-float(r['confirmedQty'])),'activeIntentIds':sorted(active),'pendingIntentIds':sorted(pending_ids)})
        out.append(rr)
    return out

def logical_summary(journal,candidate_t):
    by={}
    roots=root_snapshot(journal,None)
    for r in roots:
        lg=str(r.get('logical'))
        if lg=='None':continue
        ev=[e for e in journal if str(e.get('responsibility_id'))==str(r['responsibilityId'])]
        submits=[e for e in ev if e.get('event_type')=='SUBMIT_SENT']
        fills=[e for e in ev if e.get('event_type') in {'PARTIAL_FILL','FULL_FILL'} and int(e.get('received_at_ms') or 0)>candidate_t]
        first_submit=min((int(e.get('received_at_ms') or 0) for e in submits),default=None)
        initial_req=None
        if submits:
            se=min(submits,key=lambda e:int(e.get('received_at_ms') or 0));initial_req=float(se.get('requested_qty') or 0.)
        by[lg]={'logical':lg,'root':r['responsibilityId'],'side':r['side'],'openedAt':r['openedAt'],'needMs':r.get('needMs'),'firstSubmitAt':first_submit,'initialCarrierRequestedQty':initial_req or 0.,'futureFillQty':sum(float((e.get('extras') or {}).get('fillDeltaQty') or 0.) for e in fills),'completedAt':r['completedAt'],'terminatedAt':r['terminatedAt']}
    return by

def future_root_rows(journal,candidate_t,candidate_side,boundary_t,existing_roots):
    roots=root_snapshot(journal,None);out=[]
    for r in roots:
        ev=[e for e in journal if str(e.get('responsibility_id'))==str(r['responsibilityId'])]
        fut=[e for e in ev if int(e.get('received_at_ms') or 0)>candidate_t]
        if not fut and r['responsibilityId'] not in existing_roots:continue
        submits=[e for e in ev if e.get('event_type')=='SUBMIT_SENT']
        first_submit=min((int(e.get('received_at_ms') or 0) for e in submits),default=r.get('openedAt'))
        new=bool(r.get('openedAt') is not None and int(r['openedAt'])>candidate_t)
        same_side=str(r['side'])==candidate_side
        same_obj=bool(same_side and (not new or boundary_t is None or (first_submit is not None and int(first_submit)<int(boundary_t))))
        # Existing same-side root at candidate belongs to current objective by construction.
        if r['responsibilityId'] in existing_roots and same_side:same_obj=True
        fills=sum(float((e.get('extras') or {}).get('fillDeltaQty') or 0.) for e in fut if e.get('event_type') in {'PARTIAL_FILL','FULL_FILL'})
        cancel=sum(1 for e in fut if e.get('event_type')=='CANCEL_REQUESTED')
        reprices=sum(1 for e in fut if e.get('event_type')=='CARRIER_INTENT_CREATED' and (e.get('extras') or {}).get('kind')=='REPRICE')
        initial_req=0.
        if submits:
            se=min(submits,key=lambda e:int(e.get('received_at_ms') or 0));initial_req=float(se.get('requested_qty') or 0.)
        # Root opening while another same-side root is still reserved is parallel; otherwise serial/new.
        pre=root_snapshot([e for e in journal if int(e.get('received_at_ms') or 0)<int(first_submit or 0)],None) if new and first_submit else []
        same_active=sum(float(x['reservedCommitment']) for x in pre if str(x['side'])==candidate_side)
        topology='EXISTING_ROOT' if not new else ('PARALLEL_ROOT' if same_active>EPS else 'SERIAL_OR_NEW_ROOT')
        out.append({'responsibilityId':r['responsibilityId'],'logical':r['logical'],'side':r['side'],'newAfterCandidate':new,'firstSubmitAt':first_submit,'initialCarrierRequestedQty':initial_req,'futureFillQty':fills,'cancelRequests':cancel,'repriceIntents':reprices,'completedAt':r['completedAt'],'terminatedAt':r['terminatedAt'],'sameCandidateSide':same_side,'sameObjectiveEpisode':same_obj,'topology':topology})
    return sorted(out,key=lambda x:(int(x['firstSubmitAt'] or 0),str(x['logical'])))

def horizon_summary(froots,candidate_t,hz_ms):
    lim=candidate_t+hz_ms if hz_ms is not None else None
    xs=[x for x in froots if lim is None or int(x.get('firstSubmitAt') or 10**30)<=lim]
    return {'rootCount':len(xs),'sameSideRootCount':sum(x['sameCandidateSide'] for x in xs),'oppositeSideRootCount':sum(not x['sameCandidateSide'] for x in xs),'sameObjectiveNewDemandQty':sum(float(x['initialCarrierRequestedQty']) for x in xs if x['newAfterCandidate'] and x['sameObjectiveEpisode']),'differentObjectiveNewDemandQty':sum(float(x['initialCarrierRequestedQty']) for x in xs if x['newAfterCandidate'] and not x['sameObjectiveEpisode']),'futureFillQty':sum(float(x['futureFillQty']) for x in xs)}

def first_event_after(journal,t,types=None):
    xs=[e for e in journal if int(e.get('received_at_ms') or 0)>t and (types is None or e.get('event_type') in types)]
    return None if not xs else min(int(e.get('received_at_ms') or 0) for e in xs)

def branch_routing(base_j,branch_j,candidate_t,successor_logical_prefix='SUCCESSOR:'):
    b=logical_summary(base_j,candidate_t);x=logical_summary(branch_j,candidate_t)
    def clean(d):return {k:v for k,v in d.items() if not str(k).startswith(successor_logical_prefix)}
    b=clean(b);x=clean(x)
    bset=set(b);xset=set(x)
    changed=[]
    for k in sorted(bset & xset):
        if b[k]['side']!=x[k]['side'] or abs(float(b[k]['futureFillQty'])-float(x[k]['futureFillQty']))>EPS or abs(float(b[k]['initialCarrierRequestedQty'])-float(x[k]['initialCarrierRequestedQty']))>EPS:changed.append(k)
    return {'newLogicals':sorted(xset-bset),'missingLogicals':sorted(bset-xset),'changedLogicals':changed,'routingChanged':bool((xset^bset) or changed)}

def audit_one(role_row):
    mid=int(role_row['marketId']);key=str(role_row['candidateKey']);role=str(role_row['role']);c=role_row['candidate'];t=int(c['candidateT']);side=str(c['candidateSide']);parent=str(c['parentLogical'])
    d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
    base=reservation.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True)
    add=additive_sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode='ONE',successor_target=key)
    cred=credit_sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode='ONE',successor_target=key)
    ao=find_op(add,key);co=find_op(cred,key);cursor=int(ao.get('provenanceJournalCursor') or 0)
    prefix=add['provenanceJournal'][:cursor]
    current=root_snapshot(prefix,None)
    parent_root=next((r for r in current if str(r.get('logical'))==parent),None)
    candidate_side_roots=[r for r in current if str(r['side'])==side]
    opp_roots=[r for r in current if str(r['side'])!=side]
    ack=sum(float(r['ackedCommitment']) for r in candidate_side_roots);pend=sum(float(r['pendingSubmitCommitment']) for r in candidate_side_roots);can=sum(float(r['cancelPendingCommitment']) for r in candidate_side_roots);reserved=ack+pend+can
    confirmed=sum(float(r['confirmedQty']) for r in candidate_side_roots)
    trb=trajectory(d,base['provenanceJournal'],t,side);tra=trajectory(d,add['provenanceJournal'],t,side);trc=trajectory(d,cred['provenanceJournal'],t,side)
    recon=trb['candidateReconstructed'];gap=float(c['candidateGap']);gap_err=abs(float(recon.get('gap') or 0.)-gap)
    existing={r['responsibilityId'] for r in current}
    froots=future_root_rows(base['provenanceJournal'],t,side,trb['firstBoundaryT'],existing)
    base_log=logical_summary(base['provenanceJournal'],t);add_log=logical_summary(add['provenanceJournal'],t);cred_log=logical_summary(cred['provenanceJournal'],t)
    succ=float((((role_row.get('successorFill') or {}).get('additive') or {}).get('fillQty')) or 0.)
    succ_t=(((role_row.get('successorFill') or {}).get('additive') or {}).get('firstFillMs'))
    # Use rerun credit events rather than frozen row heuristic output; validate target against Reservation objective episode and ADDITIVE no-credit demand.
    credit_events=cred.get('successorCreditEvents') or []
    mappings=[];creditable=0.
    for ce in credit_events:
        lg=str(ce.get('targetLogical'));q=float(ce.get('qty') or 0.)
        b=base_log.get(lg);a=add_log.get(lg);cr=cred_log.get(lg)
        target_t=(b or a or cr or {}).get('firstSubmitAt')
        need_t=(a or b or cr or {}).get('needMs')
        same_obj=bool(b and b.get('side')==side and (trb['firstBoundaryT'] is None or (target_t is not None and int(target_t)<int(trb['firstBoundaryT']))))
        stable=bool(b and a and abs(float(b.get('initialCarrierRequestedQty') or 0.)-float(a.get('initialCarrierRequestedQty') or 0.))<=EPS)
        base_req=float((b or {}).get('initialCarrierRequestedQty') or 0.);add_req=float((a or {}).get('initialCarrierRequestedQty') or 0.);cred_req=float((cr or {}).get('initialCarrierRequestedQty') or 0.)
        ce_t=int(ce.get('t') or t)
        ac=[x for x in logical_carriers(add['provenanceJournal'],lg) if int(x['t'])>=ce_t]
        cc=[x for x in logical_carriers(cred['provenanceJournal'],lg) if int(x['t'])>=ce_t]
        add_future_carrier=sum(float(x['qty']) for x in ac);cred_future_carrier=sum(float(x['qty']) for x in cc)
        mechanical=min(q,max(0.,add_future_carrier-cred_future_carrier))
        target_fill=logical_first_fill(add['provenanceJournal'],lg,ce_t)
        add_before_fill=state_before(tra,target_fill) if target_fill is not None else {'t':None,'up':0.,'down':0.,'gap':0.,'weakSide':None}
        add_boundary=tra['firstBoundaryT']; boundary_before_fill=bool(target_fill is not None and add_boundary is not None and int(add_boundary)<int(target_fill))
        opened=(a or {}).get('openedAt');
        if opened is None:lifecycle='ABSENT_IN_ADDITIVE'
        elif int(opened)<=t:lifecycle='ROOT_ALREADY_OPEN_AT_CANDIDATE'
        elif succ_t is not None and int(opened)<=int(succ_t):lifecycle='ROOT_OPENED_BEFORE_SUCCESSOR_FILL'
        elif int(opened)<=ce_t:lifecycle='ROOT_OPENED_BEFORE_CREDIT_ASSIGNMENT'
        else:lifecycle='FUTURE_ROOT_AFTER_CREDIT_ASSIGNMENT'
        # Frozen role is used only as post-outcome economic validation of the mechanically identified target; this is ground truth, not a runtime rule.
        cq=min(q,mechanical) if role=='PREPOSITION_REPAIR_SUBSTITUTE' else 0.;creditable+=cq
        mappings.append({'targetLogical':lg,'creditEventQty':q,'reservationRoot':None if not b else b.get('root'),'reservationDemandQty':base_req,'additiveDemandQty':add_req,'creditDemandQty':cred_req,'suppressedQty':mechanical,'existsInReservation':bool(b),'sameReservationObjectiveEpisode':same_obj,'stableReservationToAdditiveDemand':stable,'targetNeedMs':need_t,'targetLifecycleClass':lifecycle,'additiveFutureCarrierDemandAfterCredit':add_future_carrier,'creditFutureCarrierDemandAfterCredit':cred_future_carrier,'additiveFutureCarrierKindsAfterCredit':[x['kind'] for x in ac],'creditFutureCarrierKindsAfterCredit':[x['kind'] for x in cc],'targetAdditiveFirstFillMs':target_fill,'additiveObjectiveBoundaryT':add_boundary,'additiveBoundaryBeforeTargetFill':boundary_before_fill,'additiveStateImmediatelyBeforeTargetFill':add_before_fill,'mechanicallySuppressedFutureCarrierQty':mechanical,'economicallyValidatedPrepositionCreditQty':cq})
    noncredit=max(0.,succ-creditable)
    routing_add=branch_routing(base['provenanceJournal'],add['provenanceJournal'],t);routing_credit=branch_routing(add['provenanceJournal'],cred['provenanceJournal'],t)
    current_residual=max(0.,gap-reserved)
    future_same_new=sum(float(x['initialCarrierRequestedQty']) for x in froots if x['newAfterCandidate'] and x['sameObjectiveEpisode'])
    future_opp=sum(float(x['initialCarrierRequestedQty']) for x in froots if x['newAfterCandidate'] and not x['sameCandidateSide'])
    boundary_before_fill=bool(succ_t is not None and trb['firstBoundaryT'] is not None and int(trb['firstBoundaryT'])<=int(succ_t))
    if role=='PREPOSITION_REPAIR_SUBSTITUTE':
        expl=f'PREPOSITION: successor {succ:.2f} shares has {creditable:.2f} shares of concrete causally-valid future same-objective substitution; generic side matching is not sufficient.'
    elif role=='PARALLEL_STATE_SHAPING':
        expl=f'STATE_SHAPING: successor {succ:.2f} shares has only {creditable:.2f} causally-valid substitution credit; {noncredit:.2f} remains additive. ADDITIVE routingChanged={routing_add["routingChanged"]}; generic credit would remove work that remains needed or belongs to changed topology.'
    else:
        reason=[]
        if current_residual<=EPS:reason.append('current reservation already funds current gap')
        if boundary_before_fill:reason.append('Reservation objective episode ends/reverses before successor fill')
        if future_same_new+reserved>=gap-EPS:reason.append('current+future baseline same-objective pipeline covers candidate deficit')
        if routing_add['routingChanged']:reason.append('successor changes downstream routing')
        expl='REJECT: '+('; '.join(reason) if reason else 'successor is not a clean substitute and worsens the frozen economic branch')+'.'
    horizons={tag:horizon_summary(froots,t,ms) for tag,ms in [('5s',5000),('15s',15000),('30s',30000),('final',None)]}
    return {
      'marketId':mid,'candidateKey':key,'source':role_row.get('_source'),'knownRole':role,'candidateSide':side,'parentLogical':parent,'parentRoot':None if not parent_root else parent_root['responsibilityId'],
      'candidateT':t,'secondsLeft':c.get('seconds_left',c.get('secondsLeftPublic')),'candidateDeficit':gap,'candidateFloor':float(c['candidateFloor']),'candidateUpside':float(c['candidateUpside']),
      'candidateStrictPast':{'confirmedCandidateSideRootQty':confirmed,'ackedCommitment':ack,'pendingSubmitCommitment':pend,'cancelPendingCommitment':can,'reservedQty':reserved,'currentResidualAfterReservation':current_residual,'candidateSideRoots':candidate_side_roots,'oppositeSideRoots':opp_roots,'activePendingIntentCount':sum(len(r['activeIntentIds'])+len(r['pendingIntentIds']) for r in current),'reconstructedGap':float(recon.get('gap') or 0.),'reconstructionGapError':gap_err,'episodeStartT':trb['episodeStartT']},
      'reservationObjectiveGroundTruth':{'firstObjectiveBoundaryT':trb['firstBoundaryT'],'boundaryBeforeSuccessorFill':boundary_before_fill,'futureRoots':froots,'horizons':horizons,'futureSameObjectiveNewDemandQty':future_same_new,'futureOppositeSideNewDemandQty':future_opp},
      'successorRealizedQty':succ,'successorFirstFillMs':succ_t,
      'creditAudit':{'events':credit_events,'mappings':mappings,'mechanicallySuppressedFutureQty':sum(float(x.get('mechanicallySuppressedFutureCarrierQty') or 0.) for x in mappings),'creditableFutureQty':creditable,'nonCreditableParallelQty':noncredit},
      'causalDivergence':{'structuralDivergenceAtCandidateT':t,'firstRealizedReservationVsAdditiveDivergenceT':succ_t,'firstCreditVsAdditiveDivergenceT':min((int(x.get('t') or 0) for x in credit_events),default=None),'additiveVsReservationRouting':routing_add,'creditVsAdditiveRouting':routing_credit},
      'finalBranches':role_row.get('final'),'finalRoleExplanation':expl
    }

def consolidate():
    fs=sorted(P.glob('r4_p0b_objective_counterfactual_audit_chunk_*_v2.json'))
    rows=[];seen=set()
    for f in fs:
        d=json.loads(f.read_text(encoding='utf-8'))
        for r in d.get('rows',[]):
            k=(int(r['marketId']),str(r['candidateKey']))
            if k in seen:continue
            seen.add(k);rows.append(r)
    rolec=Counter(r['knownRole'] for r in rows)
    agg={}
    for role in sorted(rolec):
        rr=[r for r in rows if r['knownRole']==role]
        agg[role]={
          'n':len(rr),
          'creditableFullSuccessorCount':sum(float(r['creditAudit']['creditableFutureQty'])>=float(r['successorRealizedQty'])-EPS for r in rr),
          'zeroCreditableCount':sum(float(r['creditAudit']['creditableFutureQty'])<=EPS for r in rr),
          'routingChangedCount':sum(bool(r['causalDivergence']['additiveVsReservationRouting']['routingChanged']) for r in rr),
          'boundaryBeforeSuccessorFillCount':sum(bool(r['reservationObjectiveGroundTruth']['boundaryBeforeSuccessorFill']) for r in rr),
          'currentPlusFuturePipelineCoversGapCount':sum(float(r['candidateStrictPast']['reservedQty'])+float(r['reservationObjectiveGroundTruth']['futureSameObjectiveNewDemandQty'])>=float(r['candidateDeficit'])-EPS for r in rr),
          'medianCurrentResidualAfterReservation':sorted(float(r['candidateStrictPast']['currentResidualAfterReservation']) for r in rr)[len(rr)//2] if rr else None
        }
    report={'version':'R4_P0B_OBJECTIVE_COUNTERFACTUAL_AUDIT_V2','researchOnly':True,'rows':rows,'roleCounts':dict(rolec),'aggregateByRole':agg,'candidateCount':len(rows),'maxCandidateGapReconstructionError':max((float(r['candidateStrictPast']['reconstructionGapError']) for r in rows),default=None)}
    out=P/'r4_p0b_objective_counterfactual_audit_candidate_table_v2.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    # compact CSV requested candidate-level table
    import csv
    cp=P/'r4_p0b_objective_counterfactual_audit_candidate_table_v2.csv'
    cols=['marketId','candidateKey','knownRole','candidateSide','parentRoot','candidateDeficit','reservedQty','ackedCommitment','pendingSubmitCommitment','cancelPendingCommitment','futureSameObjectiveNewDemandQty','futureOppositeSideNewDemandQty','successorRealizedQty','creditableFutureQty','nonCreditableParallelQty','firstObjectiveBoundaryT','firstRealizedDivergenceT','firstCreditDivergenceT','routingChanged','finalRoleExplanation']
    with cp.open('w',encoding='utf-8-sig',newline='') as fh:
        w=csv.DictWriter(fh,fieldnames=cols);w.writeheader()
        for r in rows:
            w.writerow({'marketId':r['marketId'],'candidateKey':r['candidateKey'],'knownRole':r['knownRole'],'candidateSide':r['candidateSide'],'parentRoot':r['parentRoot'],'candidateDeficit':r['candidateDeficit'],'reservedQty':r['candidateStrictPast']['reservedQty'],'ackedCommitment':r['candidateStrictPast']['ackedCommitment'],'pendingSubmitCommitment':r['candidateStrictPast']['pendingSubmitCommitment'],'cancelPendingCommitment':r['candidateStrictPast']['cancelPendingCommitment'],'futureSameObjectiveNewDemandQty':r['reservationObjectiveGroundTruth']['futureSameObjectiveNewDemandQty'],'futureOppositeSideNewDemandQty':r['reservationObjectiveGroundTruth']['futureOppositeSideNewDemandQty'],'successorRealizedQty':r['successorRealizedQty'],'creditableFutureQty':r['creditAudit']['creditableFutureQty'],'nonCreditableParallelQty':r['creditAudit']['nonCreditableParallelQty'],'firstObjectiveBoundaryT':r['reservationObjectiveGroundTruth']['firstObjectiveBoundaryT'],'firstRealizedDivergenceT':r['causalDivergence']['firstRealizedReservationVsAdditiveDivergenceT'],'firstCreditDivergenceT':r['causalDivergence']['firstCreditVsAdditiveDivergenceT'],'routingChanged':r['causalDivergence']['additiveVsReservationRouting']['routingChanged'],'finalRoleExplanation':r['finalRoleExplanation']})
    print(json.dumps({'artifact':str(out.relative_to(ROOT)),'csv':str(cp.relative_to(ROOT)),'candidateCount':len(rows),'roleCounts':dict(rolec),'aggregateByRole':agg,'maxGapError':report['maxCandidateGapReconstructionError']},ensure_ascii=False))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,default=0);ap.add_argument('--count',type=int,default=2);ap.add_argument('--include-ambiguous',action='store_true');ap.add_argument('--consolidate',action='store_true');a=ap.parse_args()
    if a.consolidate:return consolidate()
    allrows=load_role_rows(a.include_ambiguous);sel=allrows[a.start:a.start+a.count];out=[];errs=[]
    for i,r in enumerate(sel,1):
        try:
            z=audit_one(r);out.append(z);print(json.dumps({'progress':i,'marketId':z['marketId'],'candidateKey':z['candidateKey'],'role':z['knownRole'],'gap':z['candidateDeficit'],'reserved':z['candidateStrictPast']['reservedQty'],'futureSameObjective':z['reservationObjectiveGroundTruth']['futureSameObjectiveNewDemandQty'],'creditable':z['creditAudit']['creditableFutureQty'],'routingChanged':z['causalDivergence']['additiveVsReservationRouting']['routingChanged']},ensure_ascii=False),flush=True)
        except Exception as e:
            errs.append({'marketId':r.get('marketId'),'candidateKey':r.get('candidateKey'),'error':f'{type(e).__name__}:{e}'});print(json.dumps(errs[-1],ensure_ascii=False),flush=True)
    p=P/f'r4_p0b_objective_counterfactual_audit_chunk_{a.start}_{len(sel)}_v2.json';p.write_text(json.dumps({'start':a.start,'count':len(sel),'rows':out,'errors':errs},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(p.relative_to(ROOT)),'rows':len(out),'errors':errs,'totalEligible':len(allrows)},ensure_ascii=False))
if __name__=='__main__':main()
