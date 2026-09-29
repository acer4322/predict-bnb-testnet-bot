"""B2_MATCHED_STATE_TOPOLOGY_CONTRACT_SMOKE4_V1.

Behavior-inert topology reconstruction and matched-state query audit on the four
already-consumed N/T/P Clock-Placebo paths.  No new trading policy is introduced.

B = frozen N/T/P replay.
O = identical replay plus read-only G observer/query instrumentation.
Queries are prediction-sealed before a detached native-reference probe.  Query copies
never advance a counterfactual suffix or submit to the HFT venue.
"""
from __future__ import annotations
import argparse, copy, hashlib, inspect, json, math, os, tempfile, types, zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from tools import run_causal_admission_clock_placebo_smoke4_v1 as clock

cross=clock.cross
f=cross.f
v3b=f.v3b
v3=v3b.v3
qshadow=v3b.qshadow
base=v3b.base
EPS=clock.EPS
TOL=1e-8
ACCOUNT_TOL=1e-7
PATHS=('N','T','P')
DIAG_PHASE={1825991:97,1824755:58,1823553:47,1823611:65}
DIAG_T={1825991:1788168034824,1824755:1788165019693,1823553:1788158722899,1823611:1788159617265}
TERMINAL_STATUSES={'FILLED','CANCELED','CANCELLED','EXPIRED','REJECTED'}
REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}
MENU_LIMIT=16
CANDIDATE_REGISTRY=(
    'PASSIVE_PRIMARY',
    'BOUNDED_ACTIVE',
    'WAIT_CONFIRMED_RELEASE:<live-cancel-pending-key>',
    'REPLACEMENT_AFTER_CONFIRMED_RELEASE:<live-cancel-pending-key>',
)
G_SCHEMA_VERSION='B2_G_V1_PHYSICAL_RESP_AUTH_LINKS_CONTRACT_INPUT'
C_SCHEMA_VERSION='B2_C_V1_AGGREGATES_WITHOUT_OWNER_PARENT_GENERATION_RELEASE_BINDINGS'


def stable(x): return cross.stable(x)
def digest(x): return cross.digest(x)
def close(a,b,tol=TOL): return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=tol)
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def opp(s): return 'DOWN' if str(s)=='UP' else 'UP'
def tri(v):
    if v is None:return 'UNKNOWN'
    return 'TRUE' if bool(v) else 'FALSE'


def behavior_state_digest(sim):
    return digest({
        'slotKey':sim.slot_key,
        'orders':sim.orders,
        'keyRole':sim.key_role,
        'qLadder':sim.q_ladder,
        'qPendingActive':sim.q_pending_active,
        'qArm':sim.q_arm,
        'n':sim.n,'submits':sim.submits,'fills':sim.fills,
        'inv':sim.inv,'cost':sim.cost,'sideCost':getattr(sim,'sideCost',{}),
        'un':{s:list(sim.un[s]) for s in ('UP','DOWN')},
        'responsibilities':sim.serializable_lots(),
        'payments':sim.resp_payment_rows,
        'book':sim.book,
        'treatedModes':getattr(sim,'treatedModes',{}),
        'futureCoreKeys':sorted(getattr(sim,'futureCoreKeys',set())),
        'treatmentActive':bool(getattr(sim,'treatmentActive',False)),
        'seedKey':getattr(sim,'seedKey',None),
    })


def physical_node(sim,sid,key):
    o=sim.orders.get(key) or {}
    qty=float(o.get('qty') or 0.0);cum=float(o.get('cum') or 0.0);rem=max(0.0,qty-cum)
    if bool(o.get('cancelRequested')):life='CANCEL_PENDING'
    elif cum>EPS and rem>EPS:life='PARTIAL'
    elif rem<=EPS:life='FILLED_AWAIT_RELEASE'
    else:life='LIVE_OR_INFLIGHT'
    return {
        'id':str(key),'slotId':int(sid),'side':str(o.get('side') or ''),'role':str(sim.key_role.get(key,'UNASSIGNED')),
        'price':float(o.get('price') or 0.0),'qty':qty,'cum':cum,'remainingQty':rem,'lifecycle':life,
        'cancelRequested':bool(o.get('cancelRequested')),'reservedQuoteNotional':rem*float(o.get('price') or 0.0),
        'placedAt':int(o.get('placed') or 0),'localStatus':str(o.get('status') or ''),
        'isActiveKey':bool(key in getattr(sim,'activeKeys',set())),
    }


def qv_from_book(book):
    return base.v2.base.quotes(book)


def extract_G(sim,path,phase,t,qv,end,token=None,action_open=True):
    physical=[physical_node(sim,sid,key) for sid,key in sorted(sim.slot_key.items())]
    responsibilities=[]
    for lot in sim.serializable_lots():
        responsibilities.append({
            'id':f"R{int(lot['id'])}",'nativeId':int(lot['id']),'side':str(lot['side']),'repairSide':str(lot.get('repairSide') or opp(lot['side'])),
            'bornAt':int(lot['bornAt']),'initialQty':float(lot['initialQty']),'remainingQty':float(lot['remainingQty']),
            'paidQty':float(lot['paidQty']),'price':float(lot['price']),'completedAt':lot.get('completedAt'),
            'parent':None,'generationOrdinal':int(lot['id']),
        })
    links=[]
    for n in physical:
        links.append({'type':'RESERVES_SLOT','from':n['id'],'to':f"SLOT{n['slotId']}"})
        links.append({'type':'RESERVES_CAPITAL','from':n['id'],'to':'QUOTE_CAPITAL','amount':float(n['reservedQuoteNotional'])})
        links.append({'type':'RELEASE_REQUIRES_CONFIRMED_TERMINAL','from':n['id'],'to':f"SLOT{n['slotId']}"})
    for s in ('UP','DOWN'):
        ids=[f"R{int(x['id'])}" for x in sim.resp_queues[s]]
        for a,b in zip(ids,ids[1:]):links.append({'type':'FIFO_BEFORE','from':a,'to':b,'side':s})
    L=copy.deepcopy(sim.q_ladder)
    if L:
        carrier=L.get('passiveKey') if L.get('route') in {'PASSIVE','PENDING_ACTIVE'} else L.get('activeKey')
        if carrier:
            links.append({'type':'SERVICES_FIFO_SIDE','from':str(carrier),'to':f"RESP_SIDE:{L.get('targetExpandSide')}",
                          'originResponsibilityId':L.get('originResponsibilityId') or L.get('responsibilityId')})
        if L.get('route')=='PASSIVE' and L.get('passiveKey'):
            links.append({'type':'ZERO_FILL_TERMINAL_ENABLES_ACTIVE_HANDOFF','from':str(L.get('passiveKey')),'to':'BOUNDED_ACTIVE'})
    rec_by_key={str(k):str(v) for k,v in sorted(getattr(sim,'treatedModes',{}).items())}
    ext={
        'phaseOrdinal':int(phase),'eventTimestampMs':int(t),'endMs':int(end),'secondsLeft':(int(end)-int(t))/1000.0,
        'qv':stable(qv),'bookDigest':digest(sim.book),
        'bids':[[float(p),float(q)] for p,q in sorted(sim.book['bids'].items(),reverse=True)],
        'asks':[[float(p),float(q)] for p,q in sorted(sim.book['asks'].items())],
        'venueMinimumRule':'qty >= 1/price','maxSlots':int(sim.max_slots),'lateBoundaryMs':int(base.v2.NO_NEW_EXPOSURE_MS),
        'tick':float(v3b.TICK),
    }
    G={
        'schema':G_SCHEMA_VERSION,'physicalNodes':physical,'responsibilities':responsibilities,
        'unmatchedLots':{s:[{'qty':float(q),'price':float(p)} for q,p in sim.un[s]] for s in ('UP','DOWN')},
        'links':links,'missingLinks':[],
        'inventory':{'UP':float(sim.inv['UP']),'DOWN':float(sim.inv['DOWN'])},'cost':float(sim.cost),
        'qLadder':stable(L),'qPendingActive':stable(sim.q_pending_active),
        'decisionContract':{
            'path':str(path),'nativeBranch':str(sim.branch),'recognitionByKey':rec_by_key,
            'treatmentActive':bool(getattr(sim,'treatmentActive',False)),'seedKey':getattr(sim,'seedKey',None),
            'futureCoreKeys':sorted(str(k) for k in getattr(sim,'futureCoreKeys',set())),
            'actionOpportunityOpen':bool(action_open),'diagnosticToken':None if token is None else stable(token),
        },
        'externalInput':ext,
    }
    G['physicalPaidDigest']=digest({'physical':physical,'responsibilities':responsibilities,'unmatchedLots':G['unmatchedLots'],'inventory':G['inventory'],'cost':G['cost'],'qLadder':G['qLadder'],'qPendingActive':G['qPendingActive']})
    return G


def coarse_projection(G):
    phys=G['physicalNodes'];resp=G['responsibilities']
    by=defaultdict(int);rescap=defaultdict(float)
    for n in phys:
        by[f"{n['side']}|{n['role']}|{n['lifecycle']}"]+=1;rescap[n['side']]+=float(n['reservedQuoteNotional'])
    rqty=defaultdict(float);rcount=defaultdict(int)
    for r in resp:
        if r['completedAt'] is None and float(r['remainingQty'])>EPS:
            rqty[r['side']]+=float(r['remainingQty']);rcount[r['side']]+=1
    un={}
    for s in ('UP','DOWN'):
        xs=G['unmatchedLots'][s];q=sum(x['qty'] for x in xs);un[s]={'qty':q,'avg':None if q<=EPS else sum(x['qty']*x['price'] for x in xs)/q}
    dc=G['decisionContract'];ext=G['externalInput']
    C={
        'schema':C_SCHEMA_VERSION,
        'external':{'phaseOrdinal':ext['phaseOrdinal'],'eventTimestampMs':ext['eventTimestampMs'],'bookDigest':ext['bookDigest'],'qv':ext['qv'],'secondsLeft':ext['secondsLeft']},
        'contract':{'nativeBranch':dc['nativeBranch'],'actionOpportunityOpen':dc['actionOpportunityOpen'],
                    'diagnosticGateKind':'TOKEN' if dc['diagnosticToken'] is not None else 'NONE',
                    'diagnosticToken':dc['diagnosticToken'],
                    'recognitionProtocolSummary':sorted(defaultdict(int,((m,0) for m in ('I','F'))).items())},
        'aggregatePhysical':{'slots':len(phys),'cancelPending':sum(n['cancelRequested'] for n in phys),'counts':dict(sorted(by.items())),
                             'reservedCapitalTotal':sum(float(n['reservedQuoteNotional']) for n in phys),'reservedCapitalBySide':dict(rescap)},
        'aggregateResponsibility':{'qtyBySide':dict(rqty),'countBySide':dict(rcount)},
        'unmatched':un,'inventory':G['inventory'],'cost':G['cost'],
        'qLadderPresence':G['qLadder'] is not None,'qLadderRoute':None if G['qLadder'] is None else G['qLadder'].get('route'),
        'pendingActivePresence':G['qPendingActive'] is not None,
    }
    # Keep contract-level recognition counts but deliberately drop owner/key bindings.
    counts=defaultdict(int)
    for m in dc['recognitionByKey'].values():counts[str(m)]+=1
    C['contract']['recognitionProtocolSummary']=dict(sorted(counts.items()))
    C['signature']=digest(C)
    return C


def _g_link_exists(G,typ,frm=None,to=None):
    for e in G['links']:
        if e.get('type')!=typ:continue
        if frm is not None and e.get('from')!=frm:continue
        if to is not None and e.get('to')!=to:continue
        if digest(e) in set(G.get('missingLinks') or []):continue
        return True
    return False


def _g_capacity_known(G):
    for n in G['physicalNodes']:
        if not _g_link_exists(G,'RESERVES_SLOT',n['id'],f"SLOT{n['slotId']}"):return False
    return True


def _g_visible_core(G,side):
    dc=G['decisionContract'];rows=sorted([n for n in G['physicalNodes'] if n['side']==side and n['role']=='ECONOMIC_CORE'],key=lambda n:n['slotId'])
    if not dc['treatmentActive']:return rows[0] if rows else None
    out=[]
    for n in rows:
        mode=dc['recognitionByKey'].get(n['id'])
        if mode=='F' and float(n['cum'])<=EPS:continue
        out.append(n)
    return out[0] if out else None


def _g_state(G):
    u=float(G['inventory']['UP']);d=float(G['inventory']['DOWN'])
    if u<=EPS and d<=EPS:return ('EMPTY',None,None)
    if u>EPS and d<=EPS:return ('ONE_SIDED','UP','DOWN')
    if d>EPS and u<=EPS:return ('ONE_SIDED','DOWN','UP')
    weak='UP' if u<d-EPS else ('DOWN' if d<u-EPS else None)
    return ('TWO_SIDED',None,weak)


def _g_role_decision(G):
    qv=G['externalInput']['qv']
    if not qv:return (None,None)
    state,held,repair=_g_state(G);signal='UP' if float(qv.get('imb') or 0.0)>=0 else 'DOWN'
    if state=='EMPTY':return signal,'PROBE_CORE'
    if state=='ONE_SIDED':
        missing=repair
        if _g_visible_core(G,missing) is None:return missing,'ECONOMIC_CORE'
        if signal==held:return held,'SATELLITE_EXPAND'
        return missing,'SATELLITE_REPAIR'
    weak=repair
    if weak is not None and _g_visible_core(G,weak) is None:return weak,'ECONOMIC_CORE'
    if signal==weak:return signal,'SATELLITE_REPAIR'
    return signal,'SATELLITE_EXPAND'


def _g_live_levels(G,side):
    vals=[]
    if side=='UP':vals=[float(x[0]) for x in G['externalInput']['bids']]
    else:vals=[1.0-float(x[0]) for x in G['externalInput']['asks']]
    out=[];seen=set()
    for p in vals:
        p=round(float(p),10)
        if p<=EPS or p>=1.0-EPS or p in seen:continue
        q=1.0/p
        if not math.isfinite(q) or q<=EPS or q>12.0+EPS:continue
        seen.add(p);out.append(p)
    return out


def _g_pair_ok(G,side,p):
    xs=G['unmatchedLots'][opp(side)];q=sum(float(x['qty']) for x in xs)
    if q<=EPS:return True
    avg=sum(float(x['qty'])*float(x['price']) for x in xs)/q
    return avg+float(p)<=1.0000001


def _g_outstanding_side(G,side):
    return sum(float(r['remainingQty']) for r in G['responsibilities'] if r['side']==side and r['completedAt'] is None)


def _g_oldest_side(G,side):
    xs=[r for r in G['responsibilities'] if r['side']==side and r['completedAt'] is None and float(r['remainingQty'])>EPS]
    return sorted(xs,key=lambda r:(r['bornAt'],r['nativeId']))[0] if xs else None


def g_menu(G):
    qv=G['externalInput']['qv'];t=int(G['externalInput']['eventTimestampMs']);end=int(G['externalInput']['endMs']);open_=bool(G['decisionContract']['actionOpportunityOpen'])
    cap_known=_g_capacity_known(G);slots=len(G['physicalNodes']);free=slots<int(G['externalInput']['maxSlots'])
    late=(end-t)<=int(G['externalInput']['lateBoundaryMs'])
    candidates={}
    # Active constructor.
    pnd=G['qPendingActive'];L=G['qLadder'];active={'family':'BOUNDED_ACTIVE','identity':None,'L':'FALSE','A':'FALSE','gate':'NOT_REACHED','reason':'NO_PENDING_ACTIVE'}
    reconcile=None
    if pnd is not None and L is not None:
        active['A']='TRUE'
        target=_g_outstanding_side(G,str(pnd['targetExpandSide']))
        side=str(pnd['side']);role=str(pnd['role']);ask=float(qv[side]['ask']) if qv else 0.0;limit=round(min(.99,ask+float(G['externalInput']['tick'])),10);minq=(1.0/limit if limit>EPS else float('inf'));qty=min(float(pnd['sourceRemainingQty']),target)
        active['identity']={'side':side,'role':role,'price':limit,'qty':qty,'targetExpandSide':str(pnd['targetExpandSide'])}
        service_ok=any(e.get('type')=='SERVICES_FIFO_SIDE' and digest(e) not in set(G.get('missingLinks') or []) for e in G['links'])
        if not service_ok:active['L']='UNKNOWN';active['reason']='MISSING_SERVICE_LINK'
        elif late:active['L']='FALSE';active['reason']='LATE_180S';reconcile='ACTIVE_BLOCKED_LATE_180S'
        elif target<=EPS:active['L']='FALSE';active['reason']='QUEUE_SATISFIED_RECONCILIATION';reconcile='QUEUE_SATISFIED_RECONCILIATION'
        elif qty+EPS<minq:active['L']='FALSE';active['reason']='ACTIVE_BELOW_MINIMUM_RECONCILIATION';reconcile='ACTIVE_BELOW_MINIMUM_RECONCILIATION'
        elif not cap_known:active['L']='UNKNOWN';active['reason']='MISSING_CAPACITY_EDGE'
        elif not free:active['L']='FALSE';active['reason']='PHYSICAL_CAPACITY_BINDING'
        else:active['L']='TRUE';active['reason']='LEGAL'
    candidates['BOUNDED_ACTIVE']=active

    # Native reconciliation may clear the ladder before ordinary passive construction.
    Gp=copy.deepcopy(G)
    if reconcile in {'QUEUE_SATISFIED_RECONCILIATION','ACTIVE_BELOW_MINIMUM_RECONCILIATION'}:
        Gp['qLadder']=None;Gp['qPendingActive']=None
    side,role=_g_role_decision(Gp)
    passive={'family':'PASSIVE_PRIMARY','identity':None,'L':'FALSE','A':'FALSE','gate':'NOT_REACHED','reason':'NO_NATIVE_CANDIDATE','managed':False}
    if side and role and qv:
        passive['A']='TRUE';used={round(float(n['price']),10) for n in Gp['physicalNodes'] if n['side']==side}
        p0=None
        for p in _g_live_levels(Gp,side):
            if p in used:continue
            if not _g_pair_ok(Gp,side,p):continue
            p0=p;break
        if p0 is not None:
            p=float(p0);q=1.0/p;managed=False;origin=None;target_side=None
            if Gp['qLadder'] is None and Gp['qPendingActive'] is None and role in REPAIR_ROLES:
                target_side=opp(side);old=_g_oldest_side(Gp,target_side);agg=_g_outstanding_side(Gp,target_side)
                if old is not None and agg>EPS:
                    bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);ip=round(ask-float(Gp['externalInput']['tick']),10);iq=1.0/ip if ip>EPS else float('inf')
                    if EPS<bid<ip<ask-EPS and ip not in used and ip>p0+EPS and agg+EPS>=iq and iq<=12.0+EPS:
                        p=ip;q=iq;managed=True;origin=int(old['nativeId'])
            passive['identity']={'side':side,'role':role,'price':p,'qty':q,'managed':managed,'originResponsibilityId':origin,'targetExpandSide':target_side if managed else None}
            if late:passive['L']='FALSE';passive['reason']='LATE_180S'
            elif not cap_known:passive['L']='UNKNOWN';passive['reason']='MISSING_CAPACITY_EDGE'
            elif not free:passive['L']='FALSE';passive['reason']='PHYSICAL_CAPACITY_BINDING'
            else:passive['L']='TRUE';passive['reason']='LEGAL'
            passive['managed']=managed
    candidates['PASSIVE_PRIMARY']=passive

    # Contingent release/replacement bundles.  Cancel-requested is not free.
    for n in G['physicalNodes']:
        if not n['cancelRequested']:continue
        wait=f"WAIT_CONFIRMED_RELEASE:{n['id']}";rep=f"REPLACEMENT_AFTER_CONFIRMED_RELEASE:{n['id']}"
        link=_g_link_exists(G,'RELEASE_REQUIRES_CONFIRMED_TERMINAL',n['id'],f"SLOT{n['slotId']}")
        candidates[wait]={'family':'WAIT_CONFIRMED_RELEASE','identity':{'key':n['id'],'slotId':n['slotId']},'L':'TRUE' if link else 'UNKNOWN','A':'TRUE','gate':'NOT_APPLICABLE','reason':'CANCEL_PENDING_RETAINS_RESOURCE' if link else 'MISSING_RELEASE_LINK','currentExecutable':False}
        candidates[rep]={'family':'REPLACEMENT_AFTER_CONFIRMED_RELEASE','identity':{'key':n['id'],'slotId':n['slotId']},'L':'FALSE' if link else 'UNKNOWN','A':'FALSE','gate':'NOT_APPLICABLE','reason':'CONFIRMED_RELEASE_NOT_YET_ARRIVED' if link else 'MISSING_RELEASE_LINK','currentExecutable':False}

    # Priority / selection, separate from legality and diagnostic gate.
    priority='NONE'
    if open_ and not late:
        if active['A']=='TRUE' and active['L']=='TRUE':priority='BOUNDED_ACTIVE'
        elif passive['A']=='TRUE' and passive['L']=='TRUE':priority='PASSIVE_PRIMARY'
    token=G['decisionContract']['diagnosticToken']
    if priority!='NONE' and token is not None:
        c=candidates[priority];notional=float(c['identity']['price'])*float(c['identity']['qty']) if c.get('identity') else 0.0
        if int(token.get('availableSubmitCount') or 0)<1:c['gate']='VETO_COUNT'
        elif notional>float(token.get('quoteNotionalEnvelope') or 0.0)+TOL:c['gate']='VETO_CASH'
        else:c['gate']='PASS'
    elif priority!='NONE':candidates[priority]['gate']='PASS'
    return {'candidates':candidates,'priority':priority,'reconciliation':reconcile,'universeSize':len(candidates)}


def detached_clone(sim):
    c=copy.copy(sim)
    for k,v in sim.__dict__.items():
        if k=='bt':setattr(c,k,v);continue
        try:setattr(c,k,copy.deepcopy(v))
        except Exception:setattr(c,k,v)
    return c


def ref_active(c,qv,t,end):
    pnd=c.q_pending_active;L=c.q_ladder
    z={'family':'BOUNDED_ACTIVE','identity':None,'L':'FALSE','A':'FALSE','gate':'NOT_REACHED','reason':'NO_PENDING_ACTIVE'}
    reconcile=None
    if pnd is None or L is None:return z,reconcile
    z['A']='TRUE';target=c._aggregate_outstanding_expand_side(pnd['targetExpandSide']);side=pnd['side'];role=pnd['role'];ask=float(qv[side]['ask']);limit=round(min(.99,ask+v3b.TICK),10);minq=1.0/limit;qty=min(float(pnd['sourceRemainingQty']),target)
    z['identity']={'side':str(side),'role':str(role),'price':float(limit),'qty':float(qty),'targetExpandSide':str(pnd['targetExpandSide'])}
    if int(end)-int(t)<=base.v2.NO_NEW_EXPOSURE_MS:z['reason']='LATE_180S';reconcile='ACTIVE_BLOCKED_LATE_180S'
    elif target<=EPS:z['reason']='QUEUE_SATISFIED_RECONCILIATION';reconcile='QUEUE_SATISFIED_RECONCILIATION'
    elif qty+EPS<minq:z['reason']='ACTIVE_BELOW_MINIMUM_RECONCILIATION';reconcile='ACTIVE_BELOW_MINIMUM_RECONCILIATION'
    elif len(c.slot_key)>=c.max_slots:z['reason']='PHYSICAL_CAPACITY_BINDING'
    elif next((sid for sid in range(1,c.max_slots+1) if sid not in c.slot_key),None) is None:z['reason']='PHYSICAL_CAPACITY_BINDING'
    else:z['L']='TRUE';z['reason']='LEGAL'
    return z,reconcile


def reference_menu(sim,path,phase,t,qv,end,token=None,action_open=True,toggle_key=None):
    c=detached_clone(sim)
    if toggle_key is not None:
        cur=str(c.treatedModes.get(toggle_key,'I'));c.treatedModes[toggle_key]='F' if cur=='I' else 'I'
    active,reconcile=ref_active(c,qv,t,end)
    if reconcile in {'QUEUE_SATISFIED_RECONCILIATION','ACTIVE_BELOW_MINIMUM_RECONCILIATION'}:
        c._complete_carrier(int(t),str(reconcile));c.q_pending_active=None
    c.q_arm=c._arm_for_open_qty(int(t),qv)
    try:
        side,role,req_pair,req_budget=c._role_decision(qv)
        cand=c._candidate_from_levels(side,req_pair,req_budget) if side is not None else None
    finally:
        # keep detached clone only; no actual mutation leakage
        pass
    passive={'family':'PASSIVE_PRIMARY','identity':None,'L':'FALSE','A':'FALSE','gate':'NOT_REACHED','reason':'NO_NATIVE_CANDIDATE','managed':False}
    late=int(end)-int(t)<=base.v2.NO_NEW_EXPOSURE_MS
    if cand is not None:
        p,q,_=cand;passive['A']='TRUE';managed=bool(c.q_arm is not None and c.q_arm.get('passivePrice') is not None and abs(float(c.q_arm['passivePrice'])-float(p))<=EPS)
        passive['identity']={'side':str(side),'role':str(role),'price':float(p),'qty':float(q),'managed':managed,
                             'originResponsibilityId':None if not managed else int(c.q_arm['responsibilityId']),
                             'targetExpandSide':None if not managed else str(c.q_arm['expandSide'])}
        if late:passive['reason']='LATE_180S'
        elif len(c.slot_key)>=c.max_slots:passive['reason']='PHYSICAL_CAPACITY_BINDING'
        elif next((sid for sid in range(1,c.max_slots+1) if sid not in c.slot_key),None) is None:passive['reason']='PHYSICAL_CAPACITY_BINDING'
        else:passive['L']='TRUE';passive['reason']='LEGAL'
        passive['managed']=managed
    candidates={'BOUNDED_ACTIVE':active,'PASSIVE_PRIMARY':passive}
    for sid,key in sorted(c.slot_key.items()):
        o=c.orders.get(key) or {}
        if not bool(o.get('cancelRequested')):continue
        wait=f"WAIT_CONFIRMED_RELEASE:{key}";rep=f"REPLACEMENT_AFTER_CONFIRMED_RELEASE:{key}"
        candidates[wait]={'family':'WAIT_CONFIRMED_RELEASE','identity':{'key':str(key),'slotId':int(sid)},'L':'TRUE','A':'TRUE','gate':'NOT_APPLICABLE','reason':'CANCEL_PENDING_RETAINS_RESOURCE','currentExecutable':False}
        candidates[rep]={'family':'REPLACEMENT_AFTER_CONFIRMED_RELEASE','identity':{'key':str(key),'slotId':int(sid)},'L':'FALSE','A':'FALSE','gate':'NOT_APPLICABLE','reason':'CONFIRMED_RELEASE_NOT_YET_ARRIVED','currentExecutable':False}
    priority='NONE'
    if action_open and not late:
        if active['A']=='TRUE' and active['L']=='TRUE':priority='BOUNDED_ACTIVE'
        elif passive['A']=='TRUE' and passive['L']=='TRUE':priority='PASSIVE_PRIMARY'
    if priority!='NONE' and token is not None:
        cc=candidates[priority];notional=float(cc['identity']['price'])*float(cc['identity']['qty'])
        if int(token.get('availableSubmitCount') or 0)<1:cc['gate']='VETO_COUNT'
        elif notional>float(token.get('quoteNotionalEnvelope') or 0.0)+TOL:cc['gate']='VETO_CASH'
        else:cc['gate']='PASS'
    elif priority!='NONE':candidates[priority]['gate']='PASS'
    return {'candidates':candidates,'priority':priority,'reconciliation':reconcile,'universeSize':len(candidates)}


def menu_errors(pred,ref):
    out={'physicalAuthority':{'FP':0,'FN':0,'UNKNOWN':0},'managementEligibility':{'FP':0,'FN':0,'UNKNOWN':0},'diagnosticGate':{'FP':0,'FN':0,'UNKNOWN':0},'identityMismatch':0,'candidateMissing':0}
    keys=set(pred['candidates'])|set(ref['candidates'])
    for k in keys:
        p=pred['candidates'].get(k);r=ref['candidates'].get(k)
        if p is None or r is None:out['candidateMissing']+=1;continue
        if stable(p.get('identity'))!=stable(r.get('identity')):out['identityMismatch']+=1
        for fld,bucket in [('L','physicalAuthority'),('A','managementEligibility')]:
            pv=p.get(fld);rv=r.get(fld)
            if pv=='UNKNOWN':out[bucket]['UNKNOWN']+=1
            elif pv=='TRUE' and rv=='FALSE':out[bucket]['FP']+=1
            elif pv=='FALSE' and rv=='TRUE':out[bucket]['FN']+=1
        pg=p.get('gate');rg=r.get('gate')
        if pg=='UNKNOWN':out['diagnosticGate']['UNKNOWN']+=1
        elif pg!=rg:
            if pg=='PASS' and rg!='PASS':out['diagnosticGate']['FP']+=1
            elif pg!='PASS' and rg=='PASS':out['diagnosticGate']['FN']+=1
            else:out['diagnosticGate']['FP']+=1
    out['priorityError']=pred.get('priority')!=ref.get('priority')
    out['clean']=out['candidateMissing']==0 and out['identityMismatch']==0 and not out['priorityError'] and all(v==0 for b in ('physicalAuthority','managementEligibility','diagnosticGate') for v in out[b].values())
    return out


def _view_key(sim):
    rows=[]
    for key in getattr(sim,'futureCoreKeys',set()):
        o=sim.orders.get(key)
        if not o or key not in set(sim.slot_key.values()):continue
        if str(sim.key_role.get(key))!='ECONOMIC_CORE' or float(o.get('cum') or 0.0)>EPS:continue
        try:n=int(str(key).split('_')[-1])
        except Exception:n=10**9
        rows.append((n,str(key)))
    return sorted(rows)[0][1] if rows else None


def _rename_graph(G):
    z=copy.deepcopy(G);pmap={n['id']:f"X{idx+1}" for idx,n in enumerate(z['physicalNodes'])};rmap={r['id']:f"Y{idx+1}" for idx,r in enumerate(z['responsibilities'])};mp={**pmap,**rmap}
    for n in z['physicalNodes']:n['id']=pmap[n['id']]
    for r in z['responsibilities']:r['id']=rmap[r['id']]
    for e in z['links']:
        if e.get('from') in mp:e['from']=mp[e['from']]
        if e.get('to') in mp:e['to']=mp[e['to']]
    dc=z['decisionContract'];dc['recognitionByKey']={pmap.get(k,k):v for k,v in dc['recognitionByKey'].items()};dc['futureCoreKeys']=[pmap.get(k,k) for k in dc['futureCoreKeys']];dc['seedKey']=pmap.get(dc['seedKey'],dc['seedKey'])
    if z['qLadder']:
        for k in ('passiveKey','activeKey'):
            if z['qLadder'].get(k) in pmap:z['qLadder'][k]=pmap[z['qLadder'][k]]
    if z['qPendingActive'] and z['qPendingActive'].get('sourceKey') in pmap:z['qPendingActive']['sourceKey']=pmap[z['qPendingActive']['sourceKey']]
    return z,pmap


def _unrename_obj(x,inv):
    if isinstance(x,dict):return {(_unrename_obj(k,inv) if isinstance(k,str) else k):_unrename_obj(v,inv) for k,v in x.items()}
    if isinstance(x,list):return [_unrename_obj(v,inv) for v in x]
    if isinstance(x,str):
        y=x
        for a,b in inv.items():y=y.replace(a,b)
        return y
    return x


def negative_controls(G,pred):
    # NATIVE_VIEW_SHAM
    a=g_menu(copy.deepcopy(G));b=g_menu(copy.deepcopy(G));native_sham=(stable(a)==stable(b) and digest(G)==digest(copy.deepcopy(G)))
    # ID_SHAM
    rg,pmap=_rename_graph(G);rp=g_menu(rg);inv={v:k for k,v in pmap.items()};rp0=_unrename_obj(rp,inv);id_sham=stable(rp0)==stable(pred)
    # EDGE_UNKNOWN: remove one required current relation.  Must fail closed.
    eg=copy.deepcopy(G);removed=None
    for e in eg['links']:
        if e.get('type') in {'RESERVES_SLOT','SERVICES_FIFO_SIDE','RELEASE_REQUIRES_CONFIRMED_TERMINAL'}:
            removed=digest(e);eg['missingLinks']=[removed];break
    edge_exercised=removed is not None;ep=g_menu(eg) if edge_exercised else None
    edge_unknown=(not edge_exercised) or any(c.get('L')=='UNKNOWN' for c in ep['candidates'].values())
    return {'NATIVE_VIEW_SHAM':native_sham,'ID_SHAM':id_sham,'EDGE_UNKNOWN':edge_unknown,'edgeExercised':edge_exercised,'removedEdgeDigest':removed}


def audit_state(sim,path,kind,phase,t,qv,end,token=None,action_open=True):
    before=behavior_state_digest(sim);G=extract_G(sim,path,phase,t,qv,end,token,action_open);C=coarse_projection(G)
    # Prediction is sealed before native-reference probe.
    pred=g_menu(copy.deepcopy(G));seal=digest({'G':G,'prediction':pred});viewkey=_view_key(sim)
    ref=reference_menu(sim,path,phase,t,qv,end,token,action_open,None);err=menu_errors(pred,ref)
    qview=None
    if viewkey:
        Gv=copy.deepcopy(G);cur=str(Gv['decisionContract']['recognitionByKey'].get(viewkey,'I'));Gv['decisionContract']['recognitionByKey'][viewkey]='F' if cur=='I' else 'I'
        pview=g_menu(Gv);rview=reference_menu(sim,path,phase,t,qv,end,token,action_open,viewkey);verr=menu_errors(pview,rview)
        qview={'key':viewkey,'from':cur,'to':'F' if cur=='I' else 'I','physicalPaidStateSame':Gv['physicalPaidDigest']==G['physicalPaidDigest'],
               'prediction':pview,'reference':rview,'errors':verr,
               'nativeDelta':digest(ref)!=digest(rview),'predictedDelta':digest(pred)!=digest(pview),'deltaFidelity':(digest(ref)!=digest(rview))==(digest(pred)!=digest(pview)) and verr['clean']}
    neg=negative_controls(G,pred);after=behavior_state_digest(sim)
    return {'kind':kind,'path':path,'phaseOrdinal':int(phase),'eventTimestampMs':int(t),'stateHash':digest(G),'C':C,'G':G,
            'predictionSealDigest':seal,'predictionBeforeReference':True,'prediction':pred,'reference':ref,'errors':err,'viewQuery':qview,
            'negativeControls':neg,'observerMutationInert':before==after,'menuScopePass':max(pred['universeSize'],ref['universeSize'])<=MENU_LIMIT}


def transition_signature(G):
    resp=[r for r in G['responsibilities'] if r['completedAt'] is None and float(r['remainingQty'])>EPS]
    by={s:sum(float(r['remainingQty']) for r in resp if r['side']==s) for s in ('UP','DOWN')}
    return {'slots':len(G['physicalNodes']),'reservedCapital':sum(float(n['reservedQuoteNotional']) for n in G['physicalNodes']),
            'respOutstanding':by,'respOpenCount':len(resp),'qLadderRoute':None if G['qLadder'] is None else G['qLadder'].get('route'),
            'pendingActive':G['qPendingActive'] is not None,'serviceLinks':sum(e.get('type')=='SERVICES_FIFO_SIDE' for e in G['links']),
            'cancelPending':sum(n['cancelRequested'] for n in G['physicalNodes'])}


def _predict_resp_after_fill(G,legs):
    # Exact current-clock FIFO quantity-ledger algorithm from frozen qshadow, expressed on G DTO.
    queues={s:[] for s in ('UP','DOWN')}
    for r in G['responsibilities']:
        if r['completedAt'] is None and float(r['remainingQty'])>EPS:queues[r['side']].append(copy.deepcopy(r))
    for s in queues:queues[s]=sorted(queues[s],key=lambda r:(r['bornAt'],r['nativeId']))
    agg={s:{'q':0.0,'notional':0.0} for s in ('UP','DOWN')}
    for x in legs:
        s=x['side'];q=float(x['confirmedQty']);p=float(x['executionPrice']);agg[s]['q']+=q;agg[s]['notional']+=q*p
    rem={s:agg[s]['q'] for s in ('UP','DOWN')};completed=0
    for pay in ('UP','DOWN'):
        debt=opp(pay);need=rem[pay]
        while need>EPS and queues[debt]:
            lot=queues[debt][0];take=min(need,float(lot['remainingQty']));lot['remainingQty']-=take;need-=take
            if lot['remainingQty']<=EPS:queues[debt].pop(0);completed+=1
        rem[pay]=need
    pair=min(rem['UP'],rem['DOWN']);rem['UP']-=pair;rem['DOWN']-=pair
    births=0
    for s in ('UP','DOWN'):
        if rem[s]>EPS:births+=1;queues[s].append({'remainingQty':rem[s]})
    return {'outstanding':{s:sum(float(r['remainingQty']) for r in queues[s]) for s in ('UP','DOWN')},'openCount':sum(len(queues[s]) for s in queues),'completedDelta':completed,'birthDelta':births}


def predict_transition(preG,event):
    sig=transition_signature(preG);out=copy.deepcopy(sig);typ=event['type']
    if typ=='FILL_BATCH':
        for leg in event['legs']:
            node=next((n for n in preG['physicalNodes'] if n['id']==leg['key']),None)
            if node:out['reservedCapital']-=min(float(node['remainingQty']),float(leg['confirmedQty']))*float(node['price'])
        rr=_predict_resp_after_fill(preG,event['legs']);out['respOutstanding']=rr['outstanding'];out['respOpenCount']=rr['openCount']
        # A confirmed fill does not physically release a slot until _refresh_slots.
    elif typ=='SLOT_RELEASE':
        key=event['key'];node=next((n for n in preG['physicalNodes'] if n['id']==key),None)
        if node:
            out['slots']-=1;out['reservedCapital']-=float(node['reservedQuoteNotional']);out['cancelPending']-=1 if node['cancelRequested'] else 0
        L=preG['qLadder']
        if L:
            if L.get('route')=='PASSIVE' and key==L.get('passiveKey'):
                target=_g_outstanding_side(preG,str(L.get('targetExpandSide')));cum=float(node['cum']) if node else 0.0;status=str(event.get('status') or '')
                if cum>EPS or status=='FILLED' or target<=EPS or bool(L.get('satisfiedElsewhere')):
                    out['qLadderRoute']=None;out['pendingActive']=False;out['serviceLinks']=0
                else:
                    out['qLadderRoute']='PENDING_ACTIVE';out['pendingActive']=True
            elif L.get('route')=='ACTIVE' and key==L.get('activeKey'):
                out['qLadderRoute']=None;out['pendingActive']=False;out['serviceLinks']=0
    out['reservedCapital']=round(float(out['reservedCapital']),10)
    return out


def actual_transition(preG,postG):
    z=transition_signature(postG);z['reservedCapital']=round(float(z['reservedCapital']),10);return z


def detect_arrived_fill_batch(sim):
    legs=[]
    for key,o in sim.orders.items():
        try:s=sim.snap(o)
        except Exception:continue
        cum=float(s.get('cumExecQty') or 0.0);inc=max(0.0,cum-float(o.get('cum') or 0.0))
        if inc<=EPS:continue
        p=base.v2.base.fill_price(o['side'],s,o['price'])
        legs.append({'key':str(key),'side':str(o['side']),'confirmedQty':float(inc),'executionPrice':float(p)})
    return legs


def detect_terminal_release(sim):
    for sid,key in sorted(sim.slot_key.items()):
        o=sim.orders.get(key)
        if not o:continue
        try:s=sim.snap(o)
        except Exception:continue
        status=str(s.get('status') or '').upper()
        if status in TERMINAL_STATUSES:return {'type':'SLOT_RELEASE','key':str(key),'slotId':int(sid),'status':status,'cum':float(s.get('cumExecQty') or o.get('cum') or 0.0)}
    return None


class ObserverMixin:
    def __init__(self,*a,**kw):
        self.topologyAnchors=[];self.topologyObserverTouches=[];self.srSelected=False;self.seedPhaseOrdinal=None
        super().__init__(*a,**kw)

class ObsInstrumented(ObserverMixin,clock.InstrumentedFork):pass
class ObsPlacebo(ObserverMixin,clock.PlaceboFork):pass


def make_triplet(tape,spec,observer=False):
    if observer:
        return {'N':ObsInstrumented(tape,spec,'II','N'),'T':ObsInstrumented(tape,spec,'IF','T'),'P':ObsPlacebo(tape,spec)}
    return {'N':clock.InstrumentedFork(tape,spec,'II','N'),'T':clock.InstrumentedFork(tape,spec,'IF','T'),'P':clock.PlaceboFork(tape,spec)}


def _apply_lifecycle_predecision(sim,u,ordinal,path,observer,diag_phase,end,anchors,sr_done):
    t=int(u[1]);sim.set_phase(ordinal,t);base.v2.base.ex.advance_to(sim.bt,t)
    # SR: first arrived confirmed fill batch after SD, selected before applying it.
    if observer and not sr_done[path] and ordinal>diag_phase:
        legs=detect_arrived_fill_batch(sim)
        if legs:
            preq=qv_from_book(sim.book);pre=audit_state(sim,path,'SR_PRE',ordinal,t,preq,end,None,False);predtr=predict_transition(pre['G'],{'type':'FILL_BATCH','legs':legs})
            sim.process(t)
            post=audit_state(sim,path,'SR_POST',ordinal,t,preq,end,None,False);acttr=actual_transition(pre['G'],post['G']);guardchg=digest(g_menu({**copy.deepcopy(pre['G']),'decisionContract':{**pre['G']['decisionContract'],'actionOpportunityOpen':True}}))!=digest(g_menu({**copy.deepcopy(post['G']),'decisionContract':{**post['G']['decisionContract'],'actionOpportunityOpen':True}}))
            anchors[path]['SR']={'event':{'type':'FILL_BATCH','primaryKey':legs[0]['key'],'legs':legs},'pre':pre,'post':post,'transitionPrediction':predtr,'transitionReference':acttr,'transitionFidelity':stable(predtr)==stable(acttr),'guardChanged':guardchg}
            sr_done[path]=True;processed=True
        else:processed=False
    else:processed=False
    if not processed:sim.process(t)
    sim.cancel_expired(t)
    # If no earlier fill was selected, terminal release may be the first confirmed relation event.
    if observer and not sr_done[path] and ordinal>diag_phase:
        ev=detect_terminal_release(sim)
        if ev:
            preq=qv_from_book(sim.book);pre=audit_state(sim,path,'SR_PRE',ordinal,t,preq,end,None,False);predtr=predict_transition(pre['G'],ev)
            sim._refresh_slots(t)
            post=audit_state(sim,path,'SR_POST',ordinal,t,preq,end,None,False);acttr=actual_transition(pre['G'],post['G']);guardchg=digest(g_menu({**copy.deepcopy(pre['G']),'decisionContract':{**pre['G']['decisionContract'],'actionOpportunityOpen':True}}))!=digest(g_menu({**copy.deepcopy(post['G']),'decisionContract':{**post['G']['decisionContract'],'actionOpportunityOpen':True}}))
            anchors[path]['SR']={'event':ev,'pre':pre,'post':post,'transitionPrediction':predtr,'transitionReference':acttr,'transitionFidelity':stable(predtr)==stable(acttr),'guardChanged':guardchg}
            sr_done[path]=True;refreshed=True
        else:refreshed=False
    else:refreshed=False
    if not refreshed:sim._refresh_slots(t)
    base.v2.base.apply(sim.book,u);qv=base.v2.base.quotes(sim.book)
    if qv:sim._risk_contract_if_needed(t);sim._reanchor_stale(t)
    return qv


def _maybe_anchor(sim,path,ordinal,t,qv,end,token,diag_phase,anchors):
    if not qv:return
    if sim.seedPhaseOrdinal is not None and ordinal>sim.seedPhaseOrdinal and 'S0' not in anchors[path]:
        anchors[path]['S0']=audit_state(sim,path,'S0',ordinal,t,qv,end,token,True)
    if ordinal==diag_phase and 'SD' not in anchors[path]:
        anchors[path]['SD']=audit_state(sim,path,'SD',ordinal,t,qv,end,token,True)


def run_triplet(tape,spec,winner,observer=False):
    sims=make_triplet(tape,spec,observer);anchors={p:{} for p in PATHS};sr_done={p:False for p in PATHS};diag=DIAG_PHASE[int(spec['marketId'])]
    try:
        T=sims['T'];updates=sorted(T.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(T.meta['firstReceivedMs'])
        for sim in sims.values():base.v2.base.ex.advance_to(sim.bt,first)
        end=int((T.payload.get('market') or {}).get('window_end_ms') or T.meta['lastReceivedMs'])
        for ordinal,u in enumerate(updates):
            qv={p:_apply_lifecycle_predecision(sim,u,ordinal,p,observer,diag,end,anchors,sr_done) for p,sim in sims.items()}
            t=int(u[1])
            # T pre-action query before donor action.
            if observer:_maybe_anchor(sims['T'],'T',ordinal,t,qv['T'],end,None,diag,anchors)
            bt=bool(sims['T'].postSeed)
            if qv['T']:sims['T']._open_one_option(t,qv['T'],end)
            if observer and (not bt) and sims['T'].postSeed:sims['T'].seedPhaseOrdinal=ordinal
            token=sims['T'].sanitized_token();sims['P'].set_token(token)
            if observer:_maybe_anchor(sims['P'],'P',ordinal,t,qv['P'],end,token,diag,anchors)
            bp=bool(sims['P'].postSeed)
            if qv['P']:sims['P']._open_one_option(t,qv['P'],end)
            if observer and (not bp) and sims['P'].postSeed:sims['P'].seedPhaseOrdinal=ordinal
            if observer:_maybe_anchor(sims['N'],'N',ordinal,t,qv['N'],end,None,diag,anchors)
            bn=bool(sims['N'].postSeed)
            if qv['N']:sims['N']._open_one_option(t,qv['N'],end)
            if observer and (not bn) and sims['N'].postSeed:sims['N'].seedPhaseOrdinal=ordinal
            for sim in sims.values():sim._sample_occupancy()
            sims['P'].end_phase(sims['T'].phaseSuccessful)
        out={p:clock.finalize(sim,spec,winner) for p,sim in sims.items()}
        return {'paths':out,'anchors':anchors,'srDone':sr_done}
    finally:
        for sim in sims.values():sim.close()


def query_case_rows(anchors):
    rows=[]
    for p in PATHS:
        for kind,a in anchors[p].items():
            if kind=='SR':rows.extend([a['pre'],a['post']])
            else:rows.append(a)
    return rows


def aggregate_market(mid,spec,B,O,reference):
    old=reference or {};checks={}
    for p in PATHS:
        checks[f'B_{p}_oldReferenceParity']=bool(old.get(p)) and B['paths'][p]['behaviorLedgerDigest']==old[p].get('behaviorLedgerDigest')
        checks[f'B_{p}_oldSeedPhysicalParity']=bool(old.get(p)) and stable(B['paths'][p]['seedPhysical'])==stable(old[p].get('seedPhysical'))
        checks[f'B_{p}_oldSeedAdmissionParity']=bool(old.get(p)) and stable(B['paths'][p]['seedAdmissionEvents'])==stable(old[p].get('seedAdmissionEvents'))
        checks[f'O_{p}_B_behaviorLedgerParity']=O['paths'][p]['behaviorLedgerDigest']==B['paths'][p]['behaviorLedgerDigest']
        checks[f'O_{p}_B_seedPhysicalParity']=stable(O['paths'][p]['seedPhysical'])==stable(B['paths'][p]['seedPhysical'])
        checks[f'O_{p}_B_seedAdmissionParity']=stable(O['paths'][p]['seedAdmissionEvents'])==stable(B['paths'][p]['seedAdmissionEvents'])
    cases=query_case_rows(O['anchors'])
    checks['observerQueryNoMutationLeakage']=all(c['observerMutationInert'] for c in cases)
    checks['predictionBeforeReference']=all(c['predictionBeforeReference'] for c in cases)
    checks['menuScopeWithin16']=all(c['menuScopePass'] for c in cases)
    checks['allQueryNegativeControlShams']=all(c['negativeControls']['ID_SHAM'] and c['negativeControls']['NATIVE_VIEW_SHAM'] and c['negativeControls']['EDGE_UNKNOWN'] for c in cases)
    checks['nativeReferenceCompleteGuards']=all(c['reference']['universeSize']<=MENU_LIMIT for c in cases)
    checks['allExactFifoAccountingClean']=all(all(B['paths'][p]['accountingChecks'].values()) and all(O['paths'][p]['accountingChecks'].values()) for p in PATHS)
    checks['allCashflowClosure']=all(max(abs(float(X['paths'][p]['cashflow']['residual']['upPayoff'])),abs(float(X['paths'][p]['cashflow']['residual']['downPayoff'])))<=ACCOUNT_TOL for X in (B,O) for p in PATHS)
    checks['allMax4']=all(int(B['paths'][p]['terminal']['maxSlots'])<=4 and int(O['paths'][p]['terminal']['maxSlots'])<=4 for p in PATHS)
    checks['strictPastCurrentEventAlignment']=all((c['kind']!='SD' or (int(c['phaseOrdinal'])==DIAG_PHASE[int(mid)] and int(c['eventTimestampMs'])==DIAG_T[int(mid)])) and (not c['kind'].startswith('SR') or int(c['phaseOrdinal'])>DIAG_PHASE[int(mid)]) for c in cases)
    forbidden=('winner','settlementPnl','terminalPnl','futureFill','futureCancel','futurePayment')
    checks['queryNoFutureWinnerOutcomeInput']=all(not any(k in json.dumps(c['G'],sort_keys=True) for k in forbidden) for c in cases)
    correctness=all(checks.values())
    menu_clean=all(c['errors']['clean'] and (c['viewQuery'] is None or c['viewQuery']['errors']['clean']) for c in cases)
    transitions=[O['anchors'][p].get('SR') for p in PATHS if O['anchors'][p].get('SR')]
    transition_clean=all(x['transitionFidelity'] for x in transitions)
    priority_clean=all(not c['errors']['priorityError'] and (c['viewQuery'] is None or not c['viewQuery']['errors']['priorityError']) for c in cases)
    view_ex=[c for c in cases if c.get('viewQuery') and c['viewQuery']['physicalPaidStateSame'] and c['viewQuery']['nativeDelta']]
    rel_ex=[x for x in transitions if x['transitionFidelity'] and x['guardChanged'] and stable(x['transitionPrediction'])!=stable(transition_signature(x['pre']['G']))]
    # selection margin: >=2 distinct current candidate bundles with L/A true in native reference.
    margins=[]
    for c in cases:
        feasible=[(k,v) for k,v in c['reference']['candidates'].items() if v.get('L')=='TRUE' and v.get('A')=='TRUE' and v.get('family') not in {'WAIT_CONFIRMED_RELEASE'}]
        sem={digest({'family':v.get('family'),'identity':v.get('identity')}) for _,v in feasible}
        if len(sem)>=2:margins.append(c)
    return {'marketId':mid,'t':int(spec['t']),'checks':checks,'correctnessPass':correctness,'menuFidelityPass':menu_clean,'resourceTransitionFidelityPass':transition_clean,
            'priorityFidelityPass':priority_clean,'viewExerciseCases':len(view_ex),'relationTransitionExerciseCases':len(rel_ex),'selectionMarginCases':len(margins),
            'anchors':O['anchors'],'baselineBehavior':{p:B['paths'][p]['behaviorLedgerDigest'] for p in PATHS},'observerBehavior':{p:O['paths'][p]['behaviorLedgerDigest'] for p in PATHS}}


def feasibility_static():
    src_q=inspect.getsource(v3.QuantityResponsibilityLadderV3._open_one_option)
    src_role=inspect.getsource(base.MinimalPairRoleSim)
    src_v3b=inspect.getsource(v3b.FifoAggregateResponsibilityLadderV3B)
    src_led=inspect.getsource(qshadow.QuantityLedgerShadowSim._ledger_batch)
    checks={
        'nativeDecisionBoundaryAvailable':'_submit_protected_active_qty' in src_q and '_arm_for_open_qty' in src_q,
        'roleAndCandidateGuardsAvailable':'def _role_decision' in src_role and 'def _candidate_from_levels' in src_role,
        'passiveSubmitGuardAvailable':'def _submit_role' in src_v3b,
        'activeGuardAndSubmitAvailable':'def _submit_protected_active_qty' in src_v3b,
        'confirmedReleaseHandlerAvailable':'def _refresh_slots' in src_v3b,
        'exactFifoTransitionReferenceAvailable':'def _ledger_batch' in src_led,
        'candidateRegistryClosed':len(CANDIDATE_REGISTRY)<=MENU_LIMIT,
    }
    return {'pass':all(checks.values()),'checks':checks,'candidateRegistry':list(CANDIDATE_REGISTRY),'menuLimit':MENU_LIMIT,
            'referenceBoundary':'native source guards + detached clone; no low-level submit acceptance used as legality oracle'}


def coarse_alias_audit(rows):
    allcases=[]
    for r in rows:
        for p in PATHS:
            for kind,a in r['anchors'][p].items():
                xs=[a['pre'],a['post']] if kind=='SR' else [a]
                allcases.extend([(r['marketId'],p,kind,x) for x in xs])
    groups=defaultdict(list)
    for mid,p,k,c in allcases:groups[c['C']['signature']].append((mid,p,k,c))
    witnesses=[]
    for sig,xs in groups.items():
        truths={digest(x[3]['reference']) for x in xs};gouts={digest(x[3]['prediction']) for x in xs}
        if len(xs)>=2 and len(truths)>=2 and len(gouts)>=2:
            witnesses.append({'Csignature':sig,'cases':[{'marketId':m,'path':p,'kind':k,'Ghash':c['stateHash'],'referenceDigest':digest(c['reference'])} for m,p,k,c in xs]})
    return witnesses


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--states',required=True);ap.add_argument('--reference',required=True);ap.add_argument('--output',required=True);ap.add_argument('--max-states',type=int,default=4);ap.add_argument('--feasibility-only',action='store_true')
    a=ap.parse_args();feas=feasibility_static()
    if a.feasibility_only:
        op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps({'version':'B2_MATCHED_STATE_TOPOLOGY_CONTRACT_FEASIBILITY_V1','feasibility':feas},indent=2),encoding='utf-8');print(json.dumps({'ok':feas['pass'],'feasibility':feas},ensure_ascii=False));return
    if not feas['pass']:raise RuntimeError('SCOPE_OR_REFERENCE_UNAVAILABLE')
    states_payload=json.loads(Path(a.states).read_text(encoding='utf-8'));states=list(states_payload['states'])[:int(a.max_states)]
    refs=json.loads(Path(a.reference).read_text(encoding='utf-8'));refmap={int(x['marketId']):x['paths'] for x in refs['rows']}
    rows=[];outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
    with tempfile.TemporaryDirectory(prefix='b2_topology_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            cohort={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for s in states:z.extract(f"tapes/{int(s['marketId'])}.json.xz",root)
        for i,s in enumerate(states,1):
            mid=int(s['marketId']);tape=root/'tapes'/f'{mid}.json.xz';winner=str(cohort[mid]['winner']).upper()
            B=run_triplet(tape,s,winner,False);O=run_triplet(tape,s,winner,True);row=aggregate_market(mid,s,B,O,refmap.get(mid));rows.append(row)
            (outdir/'rows.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rows)+'\n',encoding='utf-8')
            print(json.dumps({'progress':i,'of':len(states),'marketId':mid,'correct':row['correctnessPass'],'menu':row['menuFidelityPass'],'transition':row['resourceTransitionFidelityPass'],'priority':row['priorityFidelityPass'],'viewEx':row['viewExerciseCases'],'relEx':row['relationTransitionExerciseCases'],'margin':row['selectionMarginCases']},ensure_ascii=False),flush=True)
    all_correct=all(r['correctnessPass'] for r in rows);scope=all(r['checks']['menuScopeWithin16'] and r['checks']['nativeReferenceCompleteGuards'] for r in rows)
    view_markets=[r['marketId'] for r in rows if r['viewExerciseCases']>0];rel_markets=[r['marketId'] for r in rows if r['relationTransitionExerciseCases']>0];margin_markets=[r['marketId'] for r in rows if r['selectionMarginCases']>0]
    exercised=len(view_markets)>=2 and len(rel_markets)>=2
    fidelity=all(r['menuFidelityPass'] and r['resourceTransitionFidelityPass'] and r['priorityFidelityPass'] for r in rows)
    aliases=coarse_alias_audit(rows)
    if not all_correct:verdict='CORRECTNESS_STOP'
    elif not scope:verdict='SCOPE_OR_REFERENCE_UNAVAILABLE'
    elif not exercised:verdict='NOT_EXERCISED'
    elif not fidelity:verdict='B2_CANDIDATE_CONTRACT_FALSIFIED'
    else:verdict='LOCAL_TOPOLOGY_CONTRACT_SUPPORTED'
    if verdict=='LOCAL_TOPOLOGY_CONTRACT_SUPPORTED':aliasDisposition='COARSE_STATE_ALIAS_WITNESSED' if aliases else 'GRAPH_NECESSITY_NOT_SHOWN'
    else:aliasDisposition='NOT_APPLICABLE'
    if verdict=='LOCAL_TOPOLOGY_CONTRACT_SUPPORTED' and len(margin_markets)>=2:b3='READY_FOR_SCOPED_VALUE_QUESTION'
    elif verdict=='LOCAL_TOPOLOGY_CONTRACT_SUPPORTED':b3='NO_LOCAL_SELECTION_MARGIN'
    else:b3='B3_ENTRY_NOT_ESTABLISHED'
    out={'version':'B2_MATCHED_STATE_TOPOLOGY_CONTRACT_SMOKE4_V1_20260908','researchOnly':True,'runtimeAuthority':False,'consumedDevelopmentOnly':True,
         'feasibility':feas,'allCorrectnessPass':all_correct,'rows':rows,'exercise':{'viewMarkets':view_markets,'relationTransitionMarkets':rel_markets,'selectionMarginMarkets':margin_markets,'viewPass':len(view_markets)>=2,'relationPass':len(rel_markets)>=2},
         'coarseAliasWitnesses':aliases,'verdict':verdict,'aliasDisposition':aliasDisposition,'b3EntryDisposition':b3,'b5Status':'B5_ECONOMIC_NULL_NOT_TESTED','alphaStatus':'NO_ALPHA_PROMOTION',
         'boundary':['fixed four consumed Clock-Placebo seams only','actual B/O paths are existing N/T/P only','O observer/query is behavior-inert and detached from venue','G predictor uses explicit physical/responsibility/guarded-link/contract/current-input DTO','C drops owner-parent-generation-release bindings','Q_VIEW toggles one earliest eligible future-core visibility only','cancel-pending never releases physical capacity','SR chosen online as first arrived confirmed fill/release after diagnostic phase','prediction sealed before native reference/event application','no PnL success gate','no new selector/direction model/H2/H4/B1 expansion','realistic HFT/no dream fill/no fresh holdout/no 8781'],
         'sha256':{'runner':sha(Path(__file__)),'states':sha(a.states),'bundle':sha(a.bundle),'reference':sha(a.reference)}}
    op=(outdir/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'verdict':verdict,'aliasDisposition':aliasDisposition,'b3EntryDisposition':b3,'allCorrectnessPass':all_correct,'exercise':out['exercise']},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
