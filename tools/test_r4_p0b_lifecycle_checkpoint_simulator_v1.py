from __future__ import annotations
import argparse,bisect,json,math,sys,sqlite3,hashlib
from collections import Counter,deque
from pathlib import Path
from statistics import median
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_preposition_responsibility_prune_v2 as v2
from tools import test_r4_queue_rest_latency_normalized_v1 as norm
from tools import hftbacktest_execution_tape_feed_v1 as tape
from tools import hftbacktest_execution_shift_audit_v0 as ex
from src.predict_bot.execution_tape_archive_v1 import load_archive

OUT=ROOT/'data/research/r4_v0/hourly'; ENTRY=1092; RESP=273; CANCEL_CONFIRM=ENTRY+RESP; MAX_REST=5000; CADENCE=500; EPS=1e-9
PUBLIC_SOURCE_DB=ROOT/'data/public_source_snapshot_archive_v2.db'
POLICIES=('ROLL_KEEP',)

def public_source_rows(mid):
    db=sqlite3.connect(f'file:{PUBLIC_SOURCE_DB}?mode=ro',uri=True)
    try:
        rr=db.execute('select sampled_at_ms,snapshot_json from public_source_snapshots_v2 where market_id=? order by sampled_at_ms',(int(mid),)).fetchall()
    finally: db.close()
    out=[]
    for t,raw in rr:
        try:z=json.loads(raw)
        except Exception:continue
        out.append((int(t),z))
    return out

def public_source_at(rows,t,max_age_ms=1000):
    if not rows:return None
    ts=[x[0] for x in rows];j=bisect.bisect_right(ts,int(t))-1
    if j<0:return None
    at,z=rows[j]
    if int(t)-at>int(max_age_ms):return None
    return z

def strike_confidence_gate(rows,t,side,mode='CONFIRM'):
    z=public_source_at(rows,t,1000)
    if z is None:return False,{'reason':'NO_FRESH_PUBLIC_SOURCE'}
    try:
        sl=float(z.get('secondsLeft')); d=float(z.get('spotMinusStrikeBps')); p=float(z.get('predictUpMid') if side=='UP' else z.get('predictDownMid'))
    except (TypeError,ValueError):return False,{'reason':'MISSING_STRIKE_OR_PREDICT'}
    toward=d if side=='UP' else -d; phase=(120.0-EPS<=sl<=300.0+EPS)
    if mode in {'CONFIRM','CONTRARY'}:
        confidence=(0.55-EPS<=p<0.80-EPS)
        dist=(2.0-EPS<=toward<10.0-EPS) if mode=='CONFIRM' else (-10.0+EPS<toward<=-2.0+EPS)
    else:
        # Weak-completion hypothesis: candidate is the Predict-disfavored side while strike confirms the opposite side.
        confidence=(0.20+EPS<p<=0.45+EPS)
        dist=(-10.0+EPS<toward<=-2.0+EPS) if mode=='WEAK_COMPLETE' else (2.0-EPS<=toward<10.0-EPS)
    ok=bool(confidence and phase and dist)
    return ok,{'reason':'PASS' if ok else 'OUTSIDE_GATE','secondsLeft':sl,'spotMinusStrikeBps':d,'towardSideBps':toward,'predictSideMid':p,'mode':mode}

def strike_formation_mode_gate(rows,t,weak_side,mode='MOD_SUPPORT'):
    z=public_source_at(rows,t,1000)
    if z is None:return False,{'reason':'NO_FRESH_PUBLIC_SOURCE'}
    try:
        d=float(z.get('spotMinusStrikeBps')); up=float(z.get('predictUpMid')); dn=float(z.get('predictDownMid')) if z.get('predictDownMid') is not None else 1.0-up
    except (TypeError,ValueError):return False,{'reason':'MISSING_STRIKE_OR_PREDICT'}
    edge=abs(up-.5)
    dominant='DOWN' if weak_side=='UP' else 'UP'
    toward_dom=d if dominant=='UP' else -d
    confidence=(0.10-EPS<=edge<0.30-EPS)
    direction=(toward_dom>EPS) if mode=='MOD_SUPPORT' else (toward_dom<-EPS)
    ok=bool(confidence and direction)
    return ok,{'reason':'PASS' if ok else 'OUTSIDE_GATE','spotMinusStrikeBps':d,'towardDominantBps':toward_dom,'predictEdge':edge,'dominantSide':dominant,'weakSide':weak_side,'mode':mode}

def apply_book(book,chg):
    for k in ('bids','asks'):
        for x in (chg or {}).get(k,[]) or []:
            p=float(x[0]);a=float(x[2])
            if a<=EPS:book[k].pop(p,None)
            else:book[k][p]=a

def book_rows(mid):
    d=load_archive(ROOT/'data/execution_tape_v1/markets'/f'{mid}.json.xz')
    ups=sorted(d.get('updates') or [],key=lambda r:(int(r[1]),int(r[0])))
    book={'bids':{},'asks':{}};flow=deque();rows=[]
    for u in ups:
        t=int(u[1]); cp=int(u[3]);chg=u[6] or {}
        if cp and u[4] is not None and u[5] is not None:
            book={'bids':{float(k):float(v) for k,v in (u[4] or {}).items()},'asks':{float(k):float(v) for k,v in (u[5] or {}).items()}}
        else:
            ba=aa=br=ar=0.
            for x in chg.get('bids',[]) or []:
                dv=float(x[3]);ba+=max(0.,dv);br+=max(0.,-dv)
            for x in chg.get('asks',[]) or []:
                dv=float(x[3]);aa+=max(0.,dv);ar+=max(0.,-dv)
            apply_book(book,chg);flow.append((t,ba,aa,br,ar))
        while flow and flow[0][0]<t-1000:flow.popleft()
        if not book['bids'] or not book['asks']:continue
        bp=sorted(book['bids'],reverse=True);ap=sorted(book['asks']);bb=bp[0];ba=ap[0]
        top5=sum(book['bids'][x] for x in bp[:5])+sum(book['asks'][x] for x in ap[:5])
        churn=sum(x[1]+x[2]+x[3]+x[4] for x in flow)
        rows.append((t,float(bb),float(ba),float(top5),float(churn)))
    return rows

def quant(vals,p):
    vals=sorted(float(x) for x in vals)
    if not vals:return None
    z=(len(vals)-1)*p;lo=int(z);hi=min(lo+1,len(vals)-1);w=z-lo
    return vals[lo]*(1-w)+vals[hi]*w

def state_at(rows,t):
    ts=[r[0] for r in rows];j=bisect.bisect_right(ts,int(t))-1
    return rows[j] if j>=0 else None

def thin_gate(rows,t):
    ts=[r[0] for r in rows];j=bisect.bisect_right(ts,int(t))-1
    if j<0:return False,None
    cur=rows[j]; lo=bisect.bisect_left(ts,int(t)-30000); prior=[r[3] for r in rows[lo:j]]
    if len(prior)<20:return False,{'reason':'INSUFFICIENT_HISTORY','n':len(prior)}
    q25=quant(prior,.25);ok=cur[3]<=q25+EPS
    return ok,{'bestBid':cur[1],'bestAsk':cur[2],'top5':cur[3],'q25Top5':q25,'churn1s':cur[4]}

def model_support(dec,t,side):
    ds=[int(r.get('decisionMs') or 0) for r in dec];j=bisect.bisect_right(ds,int(t))-1
    if j<0:return False
    m=(dec[j].get('models') or {});pu=float(m.get('pMakerUp') or 0.);pd=float(m.get('pMakerDown') or 0.)
    return pu>=pd if side=='UP' else pd>=pu

def offset_ticks(side,target_px,bs):
    if bs is None:return None
    _t,bb,ba,_d,_c=bs
    if side=='UP':return (bb-float(target_px))/.01
    native=1.-float(target_px);return (native-ba)/.01

def touch_target_px(side,bs):
    if bs is None:return None
    if side=='UP':x=bs[1]
    else:x=1.-bs[2]
    return max(.01,min(.99,round(float(x)+1e-12,2)))

def simulate(d,policy,collect_shadow=False,collect_provenance=False):
    mid=int(d['marketId']); events,times,meta=tape.build_archive_events(mid,trade_offset='mid'); br=book_rows(mid); pub=public_source_rows(mid)
    shadow=[]; management_shadow=[]; management_lifecycle=[]; management_events=[]
    # prep with maximal user-side lead so each logical has a 5s true-rest opportunity window.
    orders,takers,dec=v2.prep(d,meta,ENTRY+MAX_REST)
    bt=ex.new_bt(events,entry_latency_ms=ENTRY,response_latency_ms=RESP,queue_model='risk');ex.initialize_bt(bt)
    state=(0.,0.,0.,0.,0.);trace=[];hids={};last_exec={};logical_filled={o['logical']:0. for o in orders};next_id=1
    cancel_req=set(); retired=set(); reprice_pending={}; counts=Counter(); reasons=Counter(); early=early_surplus=early_damage=total_fill=aged_fill=0.

    # P0-A bridge: map existing simulator logical orders to durable economic responsibilities.
    # This instrumentation is research-only and must not alter execution behavior.
    prov_events=[]; prov_state={}; prov_seq=Counter(); prov_last_intent={}; prov_intent_seq=Counter(); prov_applied_exec=set(); prov_terminal_seen=set()
    def _dig(*parts):
        return hashlib.sha256('|'.join(str(x) for x in parts).encode('utf-8')).hexdigest()[:20]
    def _resp_id(o):
        return 'resp:'+_dig('mgmt',mid,o.get('logical'),o.get('side'),o.get('need'),o.get('qty'))
    def _emit(event_type,at_ms,*,rid,side,intent=None,parent=None,client=None,venue=None,exec_id=None,requested=None,cum=None,leaves=None,price=None,state_name=None,received_ms=None,extras=None):
        if not collect_provenance:return
        prov_seq[rid]+=1
        ev={'schema_version':'R4_P0_MANAGEMENT_PROVENANCE_EVENT_V1','event_seq':int(prov_seq[rid]),'market_id':mid,'event_type':event_type,'event_at_ms':int(at_ms),'received_at_ms':int(received_ms if received_ms is not None else at_ms),
            'controller_version':'R4_MANAGEMENT_PROVENANCE_BRIDGE_V1_RESEARCH','replay_revision':'MANAGEMENT_HFT_ORIGINAL_SIM_V1','responsibility_id':rid,'responsibility_type':'LOGICAL_MAKER_RESPONSIBILITY','side':str(side),
            'intent_id':intent,'parent_intent_id':parent,'carrier_role':'MAKER','client_order_id':client,'orig_client_order_id':parent,'venue_order_id':venue,'execution_id':exec_id,
            'requested_qty':requested,'cum_confirmed_fill_qty':cum,'leaves_qty':leaves,'price':price,'lifecycle_state':state_name,'source_archive_pointer':f'data/execution_tape_v1/markets/{mid}.json.xz','raw_payload_hash':None}
        if extras is not None:ev['extras']=extras
        prov_events.append(ev)
    def _ensure_resp(o,at_ms,px):
        rid=_resp_id(o)
        if rid not in prov_state:
            rq=float(o.get('qty') or 0.); prov_state[rid]={'logical':o.get('logical'),'side':str(o.get('side')),'requested_qty':rq,'confirmed_qty':float(logical_filled.get(o.get('logical'),0.)),'status':'OPEN','intent_ids':[]}
            _emit('RESPONSIBILITY_OPENED',at_ms,rid=rid,side=o['side'],requested=rq,cum=prov_state[rid]['confirmed_qty'],leaves=max(0.,rq-prov_state[rid]['confirmed_qty']),price=float(px),state_name='OPEN',extras={'logical':o.get('logical'),'needMs':o.get('need')})
        return rid
    def _new_intent(o,qty,px,kind,at_ms,hid):
        rid=_ensure_resp(o,at_ms,px); lid=o['logical']; prov_intent_seq[lid]+=1; parent=prov_last_intent.get(lid)
        iid='intent:'+_dig(rid,kind,at_ms,f'{float(px):.8f}',f'{float(qty):.8f}',prov_intent_seq[lid]); client='cl:'+_dig(iid,'client')
        prov_last_intent[lid]=iid; prov_state[rid]['intent_ids'].append(iid)
        _emit('CARRIER_INTENT_CREATED',at_ms,rid=rid,side=o['side'],intent=iid,parent=parent,client=client,venue=str(hid),requested=float(qty),cum=prov_state[rid]['confirmed_qty'],leaves=max(0.,prov_state[rid]['requested_qty']-prov_state[rid]['confirmed_qty']),price=float(px),state_name='CARRIER_CREATED',extras={'kind':kind,'logical':lid})
        _emit('SUBMIT_SENT',at_ms,rid=rid,side=o['side'],intent=iid,parent=parent,client=client,venue=str(hid),requested=float(qty),cum=prov_state[rid]['confirmed_qty'],leaves=max(0.,prov_state[rid]['requested_qty']-prov_state[rid]['confirmed_qty']),price=float(px),state_name='SUBMITTED',extras={'kind':kind})
        return rid,iid,parent,client
    actions={}
    def add(t,prio,kind,obj=None):actions.setdefault(int(t),[]).append((prio,kind,obj))
    for x in takers:add(x['t'],10,'TAKER',x)
    for o in orders:
        add(o['need'],50,'NEED',o)
        if o.get('cancel') is not None:add(o['cancel'],80,'ORIG_CANCEL',o)
    # Global 500ms lifecycle clock, receipt-aligned. First tick is firstReceived rounded up.
    first=int(meta['firstReceivedMs']);last=int(meta['lastReceivedMs']);t0=((first+CADENCE-1)//CADENCE)*CADENCE
    for t in range(t0,last+1,CADENCE):
        add(t,30,'LIFE',None)
        if t+CANCEL_CONFIRM+1<=last:add(t+CANCEL_CONFIRM+1,35,'REINSERT_TICK',None)
    timeline=sorted(set([t for t in times if first<=t<=last]+list(actions)))
    def live_rows(lid=None):
        rr=[]
        for hid,z in hids.items():
            if lid is not None and z['logical']!=lid:continue
            s=ex.order_snapshot(bt,hid);st=str(s.get('status') or '');rem=float(s.get('leavesQty') or 0.) if st in {'NEW','PARTIALLY_FILLED'} else 0.
            if rem>EPS:rr.append((hid,z,s,rem))
        return rr
    def request_cancel(hid,reason,retire=False,reprice_px=None):
        if hid in cancel_req:return False
        cur=bt.orders(0).get(hid)
        if cur is None or int(cur.status) not in {int(ex.NEW),int(ex.PARTIALLY_FILLED)} or not bool(cur.cancellable):return False
        bt.cancel(0,hid,False);cancel_req.add(hid);counts['cancelRequests']+=1;reasons[reason]+=1
        z=hids[hid];lid=z['logical']
        if collect_provenance and z.get('responsibilityId'):
            rid=z['responsibilityId'];ps=prov_state[rid]
            _emit('CANCEL_REQUESTED',cur_t,rid=rid,side=z['side'],intent=z.get('intentId'),parent=z.get('parentIntentId'),client=z.get('clientOrderId'),venue=str(hid),requested=float(z.get('qty') or 0.),cum=float(ps['confirmed_qty']),leaves=max(0.,float(ps['requested_qty'])-float(ps['confirmed_qty'])),price=float(z['px']),state_name='CANCEL_PENDING',extras={'reason':reason,'retire':bool(retire),'repricePx':reprice_px})
        if retire:retired.add(lid)
        if reprice_px is not None:
            reprice_pending[lid]={'oldHid':hid,'px':float(reprice_px),'at':cur_t+CANCEL_CONFIRM+1,'o':z['o']};counts['repriceRequests']+=1
        return True
    def management_row_before_submit(o,qty,px,kind):
        if not collect_shadow:return None
        gg=v2.geom(state); weak=v2.weak_side(state)
        if weak is None or gg['gross']<=EPS:return None
        dom='DOWN' if weak=='UP' else 'UP'; rel=v2.relation(state,o['side']); build_now=1 if rel=='WEAK' else 0
        lr=[x for x in live_rows() if int(x[1].get('submittedAt') or 0)<int(cur_t)]; wm=[x for x in lr if x[1]['side']==weak]; dm=[x for x in lr if x[1]['side']==dom]
        wage=max([(cur_t-int(x[1]['submittedAt']))/1000. for x in wm],default=0.); dage=max([(cur_t-int(x[1]['submittedAt']))/1000. for x in dm],default=0.)
        prior=[e for e in management_events if int(e['t'])<int(cur_t)]
        ev5=sum(1 for e in prior if int(e['t'])>=int(cur_t)-5000); ev15=[e for e in prior if int(e['t'])>=int(cur_t)-15000]
        seq=[int(e['build_now']) for e in ev15]+[int(build_now)]; transitions=sum(1 for a,b in zip(seq,seq[1:]) if a!=b)
        if not prior or int(prior[-1]['build_now'])!=build_now: run_start=int(cur_t)
        else:
            run_start=int(prior[-1]['t']); j=len(prior)-2
            while j>=0 and int(prior[j]['build_now'])==build_now:
                run_start=int(prior[j]['t']); j-=1
        def resp_progress(rr,side_name):
            bylog={}
            for _hid,z,_snap,_rem in rr:
                lid=str(z['logical'])
                if lid in bylog: continue
                total=float(z['o'].get('qty') or 0.)
                realized=float(logical_filled.get(lid,0.))
                bylog[lid]=(max(0.,total),max(0.,min(realized,total)))
            assigned=sum(x[0] for x in bylog.values()); done=sum(x[1] for x in bylog.values())
            unresolved=max(0.,assigned-done); ratio=(done/assigned) if assigned>EPS else 0.
            recent=sum(float(x.get('q') or 0.) for x in trace if x.get('role')=='MAKER' and x.get('side')==side_name and int(cur_t)-5000<=int(x.get('t') or 0)<int(cur_t))
            return float(unresolved),float(ratio),float(recent)
        wu,wp,wf=resp_progress(wm,weak);du,dp,df=resp_progress(dm,dom)
        weak_resp_ids=sorted({str(x[1].get('responsibilityId')) for x in wm if x[1].get('responsibilityId')})
        dom_resp_ids=sorted({str(x[1].get('responsibilityId')) for x in dm if x[1].get('responsibilityId')})
        weak_intent_ids=sorted({str(x[1].get('intentId')) for x in wm if x[1].get('intentId')})
        dom_intent_ids=sorted({str(x[1].get('intentId')) for x in dm if x[1].get('intentId')})
        pending_cancel_resp=[]; pending_cancel_intent=[]
        if collect_provenance:
            for _hid in sorted(cancel_req):
                _z=hids.get(_hid)
                if not _z: continue
                _s=ex.order_snapshot(bt,_hid); _st=str(_s.get('status') or '')
                if _st in {'NEW','PARTIALLY_FILLED'}:
                    if _z.get('responsibilityId'): pending_cancel_resp.append(str(_z['responsibilityId']))
                    if _z.get('intentId'): pending_cancel_intent.append(str(_z['intentId']))
            pending_cancel_resp=sorted(set(pending_cancel_resp)); pending_cancel_intent=sorted(set(pending_cancel_intent))
        zz=public_source_at(pub,cur_t,1000)
        sl=float(zz.get('secondsLeft')) if isinstance(zz,dict) and zz.get('secondsLeft') is not None else max(0.,(last-cur_t)/1000.)
        return {'t':int(cur_t),'marketId':mid,'side':str(o['side']),'kind':str(kind),'build_now':int(build_now),'relationBefore':rel,'weakSide':weak,'dominantSide':dom,'seconds_left':sl,'abs_gap':float(gg['absNet']),'risk_deficit':float(max(0.,-gg['floor'])),'coverage':float(gg['coverage']),'absnet_ratio':float(gg['absNet']/gg['gross']) if gg['gross']>EPS else 0.,'floor_per_gross':float(gg['floor']/gg['gross']) if gg['gross']>EPS else 0.,'floor':float(gg['floor']),'absNet':float(gg['absNet']),'weak_active_owners':float(len(wm)),'dominant_active_owners':float(len(dm)),'weak_oldest_age_s':float(wage),'dominant_oldest_age_s':float(dage),'current_mode_age_s':float((cur_t-run_start)/1000.),'events_5s':float(ev5),'events_15s':float(len(ev15)),'transitions_15s':float(transitions),'weak_unresolved_shares':wu,'dominant_unresolved_shares':du,'weak_progress_ratio':wp,'dominant_progress_ratio':dp,'weak_fill_shares_5s':wf,'dominant_fill_shares_5s':df,'weakResponsibilityIds':weak_resp_ids,'dominantResponsibilityIds':dom_resp_ids,'weakIntentIds':weak_intent_ids,'dominantIntentIds':dom_intent_ids,'weakResponsibilityCount':float(len(weak_resp_ids)),'dominantResponsibilityCount':float(len(dom_resp_ids)),'pendingCancelResponsibilityIds':pending_cancel_resp,'pendingCancelIntentIds':pending_cancel_intent,'provenanceJournalCursor':int(len(prov_events)) if collect_provenance else None,'requested_qty':float(qty),'requested_px':float(px)}
    def collect_management_lifecycle_checkpoint():
        # P0-B: periodic checkpoint of ALREADY-ACKED weak responsibilities.
        # Unlike management_row_before_submit, this is not tied to creating a new responsibility.
        if not (collect_shadow and collect_provenance): return
        gg=v2.geom(state); weak=v2.weak_side(state)
        if weak is None or gg['gross']<=EPS: return
        zz=public_source_at(pub,cur_t,1000)
        sl=float(zz.get('secondsLeft')) if isinstance(zz,dict) and zz.get('secondsLeft') is not None else max(0.,(last-cur_t)/1000.)
        if not (60.0<=sl<180.0): return
        lr=[x for x in live_rows() if int(x[1].get('submittedAt') or 0)<int(cur_t)]
        wm=[x for x in lr if x[1]['side']==weak and x[1].get('responsibilityId')]
        if not wm: return
        byroot={}
        for item in wm:
            rid=str(item[1].get('responsibilityId')); byroot.setdefault(rid,[]).append(item)
        for rid,grp in sorted(byroot.items()):
            rep=min(grp,key=lambda x:(int(x[1].get('submittedAt') or 0),int(x[0])))
            _hid,z,_snap,_rem=rep; ps=prov_state.get(rid)
            if not ps: continue
            row=management_row_before_submit(z['o'],float(ps.get('requested_qty') or 0.),float(z.get('px') or 0.),'LIFECYCLE_CHECKPOINT')
            if row is None: continue
            req=float(ps.get('requested_qty') or 0.); conf=float(ps.get('confirmed_qty') or 0.); unr=max(0.,req-conf)
            row.update({
                'checkpointResponsibilityId':rid,
                'checkpointLogical':ps.get('logical'),
                'checkpointSide':str(ps.get('side')),
                'checkpointActiveIntentIds':sorted({str(x[1].get('intentId')) for x in grp if x[1].get('intentId')}),
                'checkpointOwnerCount':int(len(grp)),
                'checkpointRequestedQty':req,
                'checkpointConfirmedQty':conf,
                'checkpointUnresolvedQty':unr,
                'checkpointProgressRatio':(conf/req) if req>EPS else 0.0,
                'checkpointOldestOwnerAgeS':max([(cur_t-int(x[1].get('submittedAt') or cur_t))/1000.0 for x in grp],default=0.0),
                'checkpointJournalCursor':int(len(prov_events)),
                'checkpointSemantic':'EXISTING_ACKED_WEAK_RESPONSIBILITY'
            })
            management_lifecycle.append(row)

    def submit(o,qty,px,kind):
        nonlocal next_id
        if qty<=EPS:return None
        mrow=management_row_before_submit(o,qty,px,kind)
        hid=next_id;next_id+=1
        rid=iid=parent=client=None
        if collect_provenance: rid,iid,parent,client=_new_intent(o,qty,px,kind,cur_t,hid)
        ex.submit_native(bt,hid,o['side'],px,qty);hids[hid]={'logical':o['logical'],'o':o,'side':o['side'],'px':float(px),'qty':float(qty),'submittedAt':cur_t,'kind':kind,'responsibilityId':rid,'intentId':iid,'parentIntentId':parent,'clientOrderId':client};last_exec[hid]=0.;counts[f'submit_{kind}']+=1
        if mrow is not None:
            if collect_provenance:
                mrow['submittedResponsibilityId']=rid;mrow['submittedIntentId']=iid;mrow['parentIntentId']=parent
            management_shadow.append(mrow); management_events.append({'t':int(cur_t),'build_now':int(mrow['build_now']),'side':str(o['side']),'kind':str(kind)})
        return hid
    def harvest(t):
        nonlocal state,total_fill,early,early_surplus,early_damage,aged_fill
        for hid in sorted(hids):
            z=hids[hid];s=ex.order_snapshot(bt,hid);cur=float(s.get('cumExecQty') or 0.);prev=last_exec.get(hid,0.);dq=max(0.,cur-prev)
            if collect_provenance and hid not in prov_terminal_seen and str(s.get('status') or '') in {'NEW','PARTIALLY_FILLED','FILLED'} and not z.get('ackProv'):
                z['ackProv']=True;rid=z.get('responsibilityId');ps=prov_state.get(rid)
                if rid and ps:_emit('ACK_NEW',t,rid=rid,side=z['side'],intent=z.get('intentId'),parent=z.get('parentIntentId'),client=z.get('clientOrderId'),venue=str(hid),requested=float(z.get('qty') or 0.),cum=float(ps['confirmed_qty']),leaves=max(0.,float(ps['requested_qty'])-float(ps['confirmed_qty'])),price=float(z['px']),state_name=str(s.get('status') or 'NEW'))
            if dq>EPS:
                pre=v2.geom(state);rel=v2.relation(state,z['side']);state=v2.apply(state,z['side'],z['px'],dq,False);post=v2.geom(state);total_fill+=dq;logical_filled[z['logical']]+=dq
                if collect_provenance and z.get('responsibilityId'):
                    rid=z['responsibilityId'];ps=prov_state[rid];ps['confirmed_qty']=min(float(ps['requested_qty']),float(logical_filled[z['logical']]))
                    ev_at=int((s.get('exchangeTs') or int(t)*1_000_000)//1_000_000);exec_id='exec:'+_dig(rid,z.get('intentId'),ev_at,f'{cur:.8f}',f'{dq:.8f}')
                    if exec_id not in prov_applied_exec:
                        prov_applied_exec.add(exec_id);leaves=max(0.,float(ps['requested_qty'])-float(ps['confirmed_qty']))
                        _emit('FULL_FILL' if leaves<=EPS else 'PARTIAL_FILL',ev_at,rid=rid,side=z['side'],intent=z.get('intentId'),parent=z.get('parentIntentId'),client=z.get('clientOrderId'),venue=str(hid),exec_id=exec_id,requested=float(z.get('qty') or 0.),cum=float(ps['confirmed_qty']),leaves=leaves,price=float(z['px']),state_name='FILLED' if leaves<=EPS else 'PARTIAL',received_ms=int(t),extras={'fillDeltaQty':float(dq)})
                        if leaves<=EPS and ps.get('status')!='COMPLETED':
                            ps['status']='COMPLETED';_emit('RESPONSIBILITY_COMPLETED',ev_at,rid=rid,side=z['side'],intent=z.get('intentId'),client=z.get('clientOrderId'),venue=str(hid),cum=float(ps['confirmed_qty']),leaves=0.,price=float(z['px']),state_name='COMPLETED',received_ms=int(t))
                age=max(0,t-int(z['submittedAt']));aged_fill+=dq if age>=5000 else 0.
                need=int(z['o']['need'])
                is_synth=(z.get('kind')=='SYNTH_STRIKE')
                if is_synth:counts['synthFillShares']+=dq
                if t<need and not is_synth:
                    early+=dq
                    if rel=='SURPLUS':early_surplus+=dq
                    early_damage+=max(0.,pre['floor']-post['floor'])
                trace.append({'t':t,'role':'MAKER','side':z['side'],'q':dq,'px':z['px'],'logical':z['logical'],'orderAgeMs':age,'preNeed':(t<need and not is_synth),'syntheticStrikeMode':is_synth,'relationBefore':rel,**post})
            if collect_provenance and hid not in prov_terminal_seen and str(s.get('status') or '') in {'CANCELED','REJECTED','EXPIRED'}:
                prov_terminal_seen.add(hid);rid=z.get('responsibilityId');ps=prov_state.get(rid)
                if rid and ps:
                    et='ACK_CANCELED' if str(s.get('status'))=='CANCELED' else 'SUBMIT_REJECTED' if str(s.get('status'))=='REJECTED' else 'IOC_TERMINAL'
                    _emit(et,t,rid=rid,side=z['side'],intent=z.get('intentId'),parent=z.get('parentIntentId'),client=z.get('clientOrderId'),venue=str(hid),requested=float(z.get('qty') or 0.),cum=float(ps['confirmed_qty']),leaves=max(0.,float(ps['requested_qty'])-float(ps['confirmed_qty'])),price=float(z['px']),state_name=str(s.get('status')))
            last_exec[hid]=cur
    def synthetic_strike_mode_tick():
        if 'STRIKE_MOD_WEAK_SYNTH' not in policy and 'STRIKE_MOD_WEAK_REVERSE_SYNTH' not in policy:return
        gstate=v2.geom(state)
        if gstate['absNet']+EPS<18.0:return
        weak='DOWN' if gstate['up']>gstate['down']+EPS else 'UP' if gstate['down']>gstate['up']+EPS else None
        if weak is None:return
        counts['synthModeChecks']+=1
        smode='MOD_SUPPORT' if 'STRIKE_MOD_WEAK_SYNTH' in policy else 'MOD_REVERSE'
        gate,gd=strike_formation_mode_gate(pub,cur_t,weak,smode)
        if not gate:
            counts['synthModeRejected']+=1;return
        counts['synthModeEligible']+=1
        # Baseline/frozen queue ownership always has priority; synth only fills missing Formation authority.
        if any(z['side']==weak for _hid,z,_s,_rem in live_rows() if _hid not in cancel_req):
            counts['synthBlockedExistingOwner']+=1;return
        ok,_=thin_gate(br,cur_t)
        if not ok:
            counts['synthBlockedThinGate']+=1;return
        qty=18.0
        if gstate['absNet']+EPS<qty:
            counts['synthBlockedGap']+=1;return
        bs=state_at(br,cur_t);touch=touch_target_px(weak,bs)
        if touch is None:return
        px=max(.01,min(.99,round(float(touch)-.01+1e-12,2)))
        if v2.mpq_base_break(state,weak,px,qty):
            counts['synthBlockedMPQ']+=1;return
        seq=int(counts['synthIntents'])+1;lid=f'SYNTH_STRIKE:{mid}:{weak}:{seq}:{cur_t}'
        o={'logical':lid,'side':weak,'qty':qty,'px':px,'need':last+1,'cancel':None,'synthetic':True}
        logical_filled[lid]=0.0;counts['synthIntents']+=1;submit(o,qty,px,'SYNTH_STRIKE')

    def acquisition_tick():
        for o in orders:
            lid=o['logical'];need=int(o['need'])
            # only seek queue option while an exchange arrival can still precede need by <=5s.
            if lid in retired or logical_filled[lid]>=o['qty']-EPS or live_rows(lid):continue
            if 'FLAT_ACTIVE2' in policy or 'STRIKE_CONFIRM_FLAT2' in policy or 'STRIKE_CONTRARY_FLAT2' in policy or 'STRIKE_WEAK_COMPLETE_FLAT2' in policy or 'STRIKE_WEAK_REVERSE_FLAT2' in policy:
                rel_now=v2.relation(state,o['side']); live_same_rows=[x for x in live_rows() if x[1]['side']==o['side'] and x[0] not in cancel_req]; live_same=len(live_same_rows); total_live=sum(1 for x in live_rows() if x[0] not in cancel_req)
                if rel_now=='FLAT':
                    strike_policy=any(k in policy for k in ('STRIKE_CONFIRM_FLAT2','STRIKE_CONTRARY_FLAT2','STRIKE_WEAK_COMPLETE_FLAT2','STRIKE_WEAK_REVERSE_FLAT2'))
                    gate_ok=True
                    if strike_policy and live_same>=1:
                        mode='CONFIRM' if 'STRIKE_CONFIRM_FLAT2' in policy else 'CONTRARY' if 'STRIKE_CONTRARY_FLAT2' in policy else 'WEAK_COMPLETE' if 'STRIKE_WEAK_COMPLETE_FLAT2' in policy else 'WEAK_REVERSE'
                        gate_ok,gd=strike_confidence_gate(pub,cur_t,o['side'],mode)
                        if mode in {'WEAK_COMPLETE','WEAK_REVERSE'}:
                            opp='DOWN' if o['side']=='UP' else 'UP'
                            opp_live=any(z['side']==opp for _hid,z,_s,_rem in live_rows() if _hid not in cancel_req)
                            if not opp_live:
                                gate_ok=False; counts['strikeWeakNoOppOwner']+=1
                        counts['strikeSecondChecks']+=1
                        if gate_ok:counts['strikeSecondEligible']+=1
                        else:counts['strikeSecondRejected']+=1
                    cap=2 if 1<=total_live<=3 and gate_ok else 1
                    if live_same>=cap:
                        counts['flatActive2BlockedByCount']+=1;continue
                    if live_same==1 and cap==2: counts['flatSecondEligible']+=1
                elif rel_now=='WEAK':
                    gap=v2.geom(state)['absNet']; cap=min(2,int((gap+EPS)//18.0)); pending=sum(x[3] for x in live_same_rows); full_rem=max(0.,o['qty']-logical_filled[lid])
                    if gap-pending+EPS<full_rem:
                        counts['flatActive2BlockedByShares']+=1;continue
                    if live_same>=cap:
                        counts['flatActive2BlockedByCount']+=1;continue
                else:
                    counts['flatActive2SurplusBlocked']+=1;continue
            elif 'CAPACITY_OWNER' in policy:
                rel_now=v2.relation(state,o['side']); live_same_rows=[x for x in live_rows() if x[1]['side']==o['side'] and x[0] not in cancel_req]; live_same=len(live_same_rows)
                if rel_now=='FLAT': cap=1
                elif rel_now=='WEAK':
                    gap=v2.geom(state)['absNet']; cap=int((gap+EPS)//18.0)
                    pending=sum(x[3] for x in live_same_rows); full_rem=max(0.,o['qty']-logical_filled[lid])
                    if gap-pending+EPS<full_rem:
                        counts['capacityOwnerBlockedByShares']+=1;continue
                else: cap=0
                if live_same>=cap:
                    counts['capacityOwnerBlockedByCount']+=1;continue
            elif 'PAIR_CREDIT_OWNER' in policy:
                rel_now=v2.relation(state,o['side']); live_same_rows=[x for x in live_rows() if x[1]['side']==o['side'] and x[0] not in cancel_req]; live_same=len(live_same_rows)
                if rel_now=='FLAT': cap=1
                elif rel_now=='WEAK':
                    gap=v2.geom(state)['absNet']; cap=2
                    pending=sum(x[3] for x in live_same_rows)
                    opp='DOWN' if o['side']=='UP' else 'UP'
                    opp_pending=sum(x[3] for x in live_rows() if x[1]['side']==opp and x[0] not in cancel_req)
                    full_rem=max(0.,o['qty']-logical_filled[lid]); credit=gap+opp_pending
                    if credit-pending+EPS<full_rem:
                        counts['pairCreditBlockedByShares']+=1;continue
                else: cap=0
                if live_same>=cap:
                    counts['pairCreditBlockedByCount']+=1;continue
            elif 'GAP_OWNER' in policy:
                rel_now=v2.relation(state,o['side']); live_same_rows=[x for x in live_rows() if x[1]['side']==o['side']]; live_same=len(live_same_rows)
                if rel_now=='FLAT': cap=1
                elif rel_now=='WEAK':
                    gap=v2.geom(state)['absNet']; cap=min(2,int((gap+EPS)//18.0))
                    pending=sum(x[3] for x in live_same_rows)
                    full_rem=max(0.,o['qty']-logical_filled[lid])
                    if gap-pending+EPS<full_rem:
                        counts['gapOwnerBlockedByShares']+=1
                        opp='DOWN' if o['side']=='UP' else 'UP'
                        opp_pending=sum(x[3] for x in live_rows() if x[1]['side']==opp and x[0] not in cancel_req)
                        if opp_pending>EPS:
                            counts['gapBlockedWithOppPending']+=1
                            if gap+opp_pending-pending+EPS>=full_rem: counts['gapBlockedCreditResolvable']+=1
                        continue
                else: cap=0
                if live_same>=cap:
                    counts['gapOwnerBlockedByCount']+=1;continue
            elif 'ABS90_OWNER' in policy:
                rel_now=v2.relation(state,o['side']); gap=v2.geom(state)['absNet']; cap=1 if rel_now=='FLAT' else (1 if gap<90.0 else 2) if rel_now=='WEAK' else 0
                live_same=sum(1 for _hid,z,_s,_rem in live_rows() if z['side']==o['side'])
                if live_same>=cap:
                    counts['abs90OwnerBlocked']+=1;continue
            elif 'REL_OWNER' in policy:
                rel_now=v2.relation(state,o['side']); cap=1 if rel_now=='FLAT' else 2 if rel_now=='WEAK' else 0
                live_same=sum(1 for _hid,z,_s,_rem in live_rows() if z['side']==o['side'])
                if live_same>=cap:
                    counts['relationOwnerBlocked']+=1;continue
            elif 'OWNER1' in policy and any(z['side']==o['side'] for _hid,z,_s,_rem in live_rows()):
                counts['owner1Blocked']+=1;continue
            if not (need-ENTRY-MAX_REST<=cur_t<=need-ENTRY):continue
            support_ok=model_support(dec,cur_t,o['side'])
            if not support_ok and 'SYNTH' not in policy and ('STRIKE_MOD_WEAK_OVERRIDE' in policy or 'STRIKE_MOD_WEAK_REVERSE' in policy):
                counts['strikeModeOverrideChecks']+=1
                if v2.relation(state,o['side'])=='WEAK':
                    smode='MOD_SUPPORT' if 'STRIKE_MOD_WEAK_OVERRIDE' in policy else 'MOD_REVERSE'
                    g,gd=strike_formation_mode_gate(pub,cur_t,o['side'],smode)
                    if g:
                        support_ok=True;counts['strikeModeOverrideEligible']+=1;counts['strikeModeOverrideUsed']+=1
                    else:counts['strikeModeOverrideRejected']+=1
                else:counts['strikeModeOverrideNotWeak']+=1
            if not support_ok:continue
            ok,_=thin_gate(br,cur_t)
            if not ok:continue
            rel=v2.relation(state,o['side'])
            if rel=='SURPLUS':continue
            submit_px=float(o['px'])
            if 'STRIKE_STRONG_AGG' in policy or 'STRIKE_WEAK_AGG' in policy:
                mode='CONFIRM' if 'STRIKE_STRONG_AGG' in policy else 'WEAK_COMPLETE'
                g,gd=strike_confidence_gate(pub,cur_t,o['side'],mode)
                counts['strikeAggChecks']+=1
                if g:
                    counts['strikeAggEligible']+=1
                    bs=state_at(br,cur_t); touch=touch_target_px(o['side'],bs)
                    if touch is not None and touch>submit_px+EPS:
                        submit_px=min(float(touch),submit_px+.01); counts['strikeAggUsed']+=1
            if v2.mpq_base_break(state,o['side'],submit_px,o['qty']-logical_filled[lid]):continue
            rem=max(0.,o['qty']-logical_filled[lid]); submit_kind='OPTION'
            if ('FLAT_ACTIVE2' in policy or 'STRIKE_CONFIRM_FLAT2' in policy or 'STRIKE_CONTRARY_FLAT2' in policy or 'STRIKE_WEAK_COMPLETE_FLAT2' in policy or 'STRIKE_WEAK_REVERSE_FLAT2' in policy) and v2.relation(state,o['side'])=='FLAT' and sum(1 for _hid,z,_s,_rem in live_rows() if z['side']==o['side'])>=1:
                counts['flatSecondAcquisitions']+=1
                if 'STRIKE_CONFIRM_FLAT2' in policy:
                    counts['strikeConfirmAcquisitions']+=1; submit_kind='STRIKE_OPTION'
                if 'STRIKE_CONTRARY_FLAT2' in policy:
                    counts['strikeContraryAcquisitions']+=1; submit_kind='STRIKE_OPTION'
                if 'STRIKE_WEAK_COMPLETE_FLAT2' in policy:
                    counts['strikeWeakCompleteAcquisitions']+=1; submit_kind='STRIKE_OPTION'
                if 'STRIKE_WEAK_REVERSE_FLAT2' in policy:
                    counts['strikeWeakReverseAcquisitions']+=1; submit_kind='STRIKE_OPTION'
            submit(o,rem,submit_px,submit_kind);counts['queueGateAcquisitions']+=1
    def lifecycle_tick():
        bs=state_at(br,cur_t)
        # Dynamic queue-option ownership pruning. Only pre-need OPTION children are affected; MAIN/frozen-need orders are untouched.
        if 'FLAT_ACTIVE2' in policy or 'STRIKE_CONFIRM_FLAT2' in policy or 'STRIKE_CONTRARY_FLAT2' in policy or 'STRIKE_WEAK_COMPLETE_FLAT2' in policy or 'STRIKE_WEAK_REVERSE_FLAT2' in policy or 'CAPACITY_OWNER' in policy or 'PAIR_CREDIT_OWNER' in policy or 'GAP_OWNER' in policy or 'ABS90_OWNER' in policy:
            for side in ('UP','DOWN'):
                rr=[x for x in live_rows() if x[1]['side']==side and x[1].get('kind') in {'OPTION','STRIKE_OPTION','SYNTH_STRIKE'} and cur_t<int(x[1]['o']['need']) and x[0] not in cancel_req]
                rr.sort(key=lambda x:(x[1]['submittedAt'],x[0]))
                rel_now=v2.relation(state,side); gap=v2.geom(state)['absNet']
                if 'FLAT_ACTIVE2' in policy:
                    cap=2 if rel_now=='FLAT' else min(2,int((gap+EPS)//18.0)) if rel_now=='WEAK' else 0
                    allowed_shares=36.0 if rel_now=='FLAT' else gap if rel_now=='WEAK' else 0.0
                elif any(k in policy for k in ('STRIKE_CONFIRM_FLAT2','STRIKE_CONTRARY_FLAT2','STRIKE_WEAK_COMPLETE_FLAT2','STRIKE_WEAK_REVERSE_FLAT2')):
                    strike_live=any(x[1].get('kind')=='STRIKE_OPTION' for x in rr)
                    cap=(2 if strike_live else 1) if rel_now=='FLAT' else min(2,int((gap+EPS)//18.0)) if rel_now=='WEAK' else 0
                    allowed_shares=(36.0 if strike_live else 18.0) if rel_now=='FLAT' else gap if rel_now=='WEAK' else 0.0
                elif 'CAPACITY_OWNER' in policy:
                    cap=1 if rel_now=='FLAT' else int((gap+EPS)//18.0) if rel_now=='WEAK' else 0
                    allowed_shares=18.0 if rel_now=='FLAT' else gap if rel_now=='WEAK' else 0.0
                elif 'PAIR_CREDIT_OWNER' in policy:
                    cap=1 if rel_now=='FLAT' else 2 if rel_now=='WEAK' else 0
                    opp='DOWN' if side=='UP' else 'UP'
                    opp_pending=sum(x[3] for x in live_rows() if x[1]['side']==opp and x[0] not in cancel_req)
                    allowed_shares=18.0 if rel_now=='FLAT' else gap+opp_pending if rel_now=='WEAK' else 0.0
                elif 'GAP_OWNER' in policy:
                    cap=1 if rel_now=='FLAT' else min(2,int((gap+EPS)//18.0)) if rel_now=='WEAK' else 0
                    allowed_shares=18.0 if rel_now=='FLAT' else gap if rel_now=='WEAK' else 0.0
                else:
                    cap=1 if rel_now=='FLAT' else (1 if gap<90.0 else 2) if rel_now=='WEAK' else 0
                    allowed_shares=float('inf') if rel_now!='SURPLUS' else 0.0
                live_count=len(rr); pending=sum(x[3] for x in rr)
                for hid,z,snap,rem in reversed(rr):
                    if live_count<=cap and pending<=allowed_shares+EPS: break
                    if request_cancel(hid,'DYNAMIC_EXCESS_OWNER',retire=False):
                        counts['dynamicOwnerPrunes']+=1;live_count-=1;pending-=rem
        for hid,z,s,rem in list(live_rows()):
            if hid in cancel_req:continue
            o=z['o'];lid=z['logical']
            # responsibility and MPQ always dominate queue age.
            if v2.relation(state,z['side'])=='SURPLUS':request_cancel(hid,'LOST_RESPONSIBILITY',retire=True);continue
            if v2.mpq_base_break(state,z['side'],z['px'],rem):request_cancel(hid,'MPQ_BASE_BREAK',retire=True);continue
            off=offset_ticks(z['side'],z['px'],bs);age=max(0,cur_t-int(z['submittedAt']))
            if off is None:continue
            if off<=-1.0:
                counts['aheadInsideChecks']+=1
                if policy.startswith('ROLL_PULL_AHEAD'):request_cancel(hid,'AHEAD_INSIDE_PULL',retire=True)
                elif policy.startswith('ROLL_REPRICE_AHEAD'):
                    px=touch_target_px(z['side'],bs)
                    if px is not None and abs(px-z['px'])>=.005:request_cancel(hid,'AHEAD_INSIDE_REPRICE',reprice_px=px)
            elif age>=5000:counts['agedKeepChecks']+=1
            elif off>=4.0:counts['deepKeepChecks']+=1
    try:
        for cur_t in timeline:
            if int(bt.current_timestamp)<=cur_t*1_000_000:
                if not ex.advance_to(bt,cur_t):break
            harvest(cur_t)
            aa=sorted(actions.get(cur_t,[]),key=lambda x:x[0])
            for _,kind,obj in aa:
                if kind=='TAKER':
                    pre=v2.geom(state);state=v2.apply(state,obj['side'],obj['px'],obj['q'],True);trace.append({'t':cur_t,'role':'TAKER','side':obj['side'],'q':obj['q'],'px':obj['px'],'preNeed':False,'relationBefore':v2.relation((pre['up'],pre['down'],pre['cu'],pre['cd'],pre['fees']),obj['side']),**v2.geom(state)})
            if any(k=='LIFE' for _,k,_ in aa):
                if collect_shadow:
                    gg=v2.geom(state); weak=v2.weak_side(state); zz=public_source_at(pub,cur_t,1000)
                    if weak is not None and zz is not None:
                        try:
                            sl=float(zz.get('secondsLeft')); up=float(zz.get('predictUpMid')); dn=float(zz.get('predictDownMid')) if zz.get('predictDownMid') is not None else 1.0-up; sm=float(zz.get('spotMinusStrikeBps'))
                            dom='DOWN' if weak=='UP' else 'UP'; toward=sm if dom=='UP' else -sm; fav='UP' if up>=dn else 'DOWN'
                            shadow.append({'t':int(cur_t),'marketId':mid,'weakSide':weak,'dominantSide':dom,'seconds_left':sl,'predict_up_mid':up,'predict_down_mid':dn,'predict_edge':abs(up-.5),'predict_supports_dominant':1 if fav==dom else 0,'strike_toward_dominant_bps':toward,'spot_supports_dominant':1 if toward>0 else 0,'pre_abs_payoff_gap':float(gg['absNet']),'pre_risk_deficit':float(max(0.,-gg['floor'])),'floor':float(gg['floor']),'absNet':float(gg['absNet']),'futureFrozenWeakNeed5s':1 if any(cur_t<int(o['need'])<=cur_t+5000 and o['side']==weak for o in orders) else 0})
                        except (TypeError,ValueError):
                            pass
                collect_management_lifecycle_checkpoint();acquisition_tick();synthetic_strike_mode_tick();lifecycle_tick()
            # Reprice reinserts are evaluated on preregistered response-time ticks so a cancel cannot silently degrade into PULL.
            if any(k=='REINSERT_TICK' for _,k,_ in aa):
                for lid,pd in list(reprice_pending.items()):
                    if int(pd['at'])>cur_t:continue
                    reprice_pending.pop(lid,None)
                    if lid in retired:continue
                    o=pd['o'];committed=sum(x[3] for x in live_rows(lid));rem=max(0.,o['qty']-logical_filled[lid]-committed)
                    if rem>EPS:submit(o,rem,pd['px'],'REPRICE');counts['reinserted']+=1
            for _,kind,o in aa:
                if kind in {'TAKER','LIFE','REINSERT_TICK'}:continue
                lid=o['logical']
                if kind=='NEED':
                    committed=sum(x[3] for x in live_rows(lid));rem=max(0.,o['qty']-logical_filled[lid]-committed)
                    if rem>EPS:submit(o,rem,o['px'],'MAIN')
                elif kind=='ORIG_CANCEL':
                    retired.add(lid)
                    for hid,z,s,rem in live_rows(lid):request_cancel(hid,'ORIGINAL_LIFECYCLE_CANCEL',retire=True)
        if int(bt.current_timestamp)<last*1_000_000:ex.advance_to(bt,last);harvest(last)
        if collect_provenance:
            for rid,ps in prov_state.items():
                if ps.get('status')=='COMPLETED':continue
                ps['status']='TERMINATED';side=ps['side'];iid=prov_last_intent.get(ps['logical'])
                _emit('RESPONSIBILITY_TERMINATED',last,rid=rid,side=side,intent=iid,cum=float(ps['confirmed_qty']),leaves=max(0.,float(ps['requested_qty'])-float(ps['confirmed_qty'])),state_name='MARKET_TERMINAL',extras={'logical':ps['logical']})
    finally:bt.close()
    tr=sorted(trace,key=lambda x:x['t']);fs=next((x for x in tr if x['floor']>=0),None);dur=v2.durable(tr);final=v2.geom(state);ps=v2.path_stats(tr)
    if collect_shadow and shadow:
        tvals=[int(x['t']) for x in tr]
        import bisect as _bisect
        for sr in shadow:
            t0=int(sr['t']); j=_bisect.bisect_right(tvals,t0); k=_bisect.bisect_right(tvals,t0+5000); win=tr[j:k]
            sr['futureWeakMakerFillShares5s']=float(sum(float(x.get('q') or 0.) for x in win if x.get('role')=='MAKER' and x.get('side')==sr['weakSide']))
            sr['futureWeakMakerFill5s']=1 if sr['futureWeakMakerFillShares5s']>EPS else 0
            sr['floorRelapse5s']=1 if any(float(x.get('floor') or 0.)<0.0 for x in win) else 0
            if win:
                end=win[-1]; sr['floorDelta5s']=float(end['floor'])-float(sr['floor']); sr['absNetDelta5s']=float(end['absNet'])-float(sr['absNet'])
            else:
                sr['floorDelta5s']=0.0; sr['absNetDelta5s']=0.0
    if collect_shadow and management_shadow:
        mets=sorted(management_events,key=lambda x:int(x['t'])); mts=[int(x['t']) for x in mets]; mtvals=[int(x['t']) for x in tr]
        import bisect as _mbisect
        for mr in management_shadow:
            t0=int(mr['t']); j=_mbisect.bisect_right(mts,t0); k=_mbisect.bisect_right(mts,t0+5000); fut=mets[j:k]
            mr['continue_weak_5s']=int(int(mr['build_now'])==1 and any(int(e['build_now'])==1 for e in fut))
            if int(mr['build_now'])==1:
                if fut: mr['management_label_5s']='CONTINUE_WEAK' if int(fut[0]['build_now'])==1 else 'HANDOFF_ALLOW'
                else: mr['management_label_5s']='OBSERVE_NO_EVENT'
            else: mr['management_label_5s']=''
            j2=_mbisect.bisect_right(mtvals,t0); k2=_mbisect.bisect_right(mtvals,t0+5000); win=tr[j2:k2]
            mr['futureWeakMakerFillShares5s']=float(sum(float(x.get('q') or 0.) for x in win if x.get('role')=='MAKER' and x.get('side')==mr['weakSide']))
            mr['futureWeakMakerFill5s']=int(mr['futureWeakMakerFillShares5s']>EPS)
            if win:
                end=win[-1]; mr['floorDelta5s']=float(end['floor'])-float(mr['floor']); mr['absNetDelta5s']=float(end['absNet'])-float(mr['absNet'])
            else:
                mr['floorDelta5s']=0.0; mr['absNetDelta5s']=0.0
            mr['floorImproved5s']=int(mr['floorDelta5s']>EPS); mr['absNetReduced5s']=int(mr['absNetDelta5s']<-EPS)
    if collect_shadow and collect_provenance and management_lifecycle:
        import bisect as _lcbisect
        mtvals=[int(x['t']) for x in tr]
        def _root_status_at(rid,cutoff_ms):
            active=set(); pending=set(); root_intents=set(); completed=False; terminated=False; confirmed=0.0
            for e in prov_events:
                if str(e.get('responsibility_id'))!=str(rid) or int(e.get('received_at_ms') or 0)>int(cutoff_ms): continue
                et=str(e.get('event_type') or ''); iid=str(e.get('intent_id')) if e.get('intent_id') else None
                if iid: root_intents.add(iid)
                if et=='SUBMIT_SENT' and iid: pending.add(iid)
                elif et=='ACK_NEW' and iid: pending.discard(iid); active.add(iid)
                elif et=='CANCEL_REQUESTED' and iid: pass
                elif et in {'ACK_CANCELED','SUBMIT_REJECTED','IOC_TERMINAL','FULL_FILL'} and iid: active.discard(iid); pending.discard(iid)
                if et in {'PARTIAL_FILL','FULL_FILL'}: confirmed=max(confirmed,float(e.get('cum_confirmed_fill_qty') or 0.0))
                if et=='RESPONSIBILITY_COMPLETED': completed=True; active.clear(); pending.clear()
                if et=='RESPONSIBILITY_TERMINATED': terminated=True; active.clear(); pending.clear()
            if completed: status='COMPLETED'
            elif terminated: status='TERMINATED'
            elif active: status='LIVE'
            elif pending: status='PENDING_SUBMIT'
            else: status='OPEN_UNOWNED'
            return status,sorted(active),sorted(pending),confirmed
        for mr in management_lifecycle:
            t0=int(mr['t']); rid=str(mr['checkpointResponsibilityId']); cursor=int(mr['checkpointJournalCursor']); cutoff=t0+5000
            fut=[e for e in prov_events[cursor:] if str(e.get('responsibility_id'))==rid and int(e.get('received_at_ms') or 0)<=cutoff]
            fill5=float(sum(float((e.get('extras') or {}).get('fillDeltaQty') or 0.0) for e in fut if e.get('event_type') in {'PARTIAL_FILL','FULL_FILL'}))
            status5,active5,pending5,conf5=_root_status_at(rid,cutoff)
            mr['rootFillShares5s']=fill5
            mr['rootCompleted5s']=int(status5=='COMPLETED')
            mr['rootTerminated5s']=int(status5=='TERMINATED')
            mr['rootLiveOwnerAt5s']=int(status5=='LIVE')
            mr['rootPendingSubmitAt5s']=int(status5=='PENDING_SUBMIT')
            mr['rootResponsibilityPersists5s']=int(status5 not in {'COMPLETED','TERMINATED'})
            mr['rootProgressOrComplete5s']=int(fill5>EPS or status5=='COMPLETED')
            mr['rootNewCarrierIntents5s']=int(sum(1 for e in fut if e.get('event_type')=='CARRIER_INTENT_CREATED'))
            mr['rootCancelRequests5s']=int(sum(1 for e in fut if e.get('event_type')=='CANCEL_REQUESTED'))
            mr['rootConfirmedQtyAt5s']=float(conf5)
            mr['rootStatus5s']=status5
            if status5=='COMPLETED': outcome='COMPLETED'
            elif status5=='TERMINATED': outcome='TERMINATED'
            elif status5=='LIVE' and fill5>EPS: outcome='LIVE_PROGRESS'
            elif status5=='LIVE': outcome='LIVE_STALLED'
            elif status5=='PENDING_SUBMIT': outcome='PENDING_SUBMIT'
            else: outcome='OPEN_UNOWNED'
            mr['rootLifecycleOutcome5s']=outcome
            j=_lcbisect.bisect_right(mtvals,t0); k=_lcbisect.bisect_right(mtvals,cutoff); win=tr[j:k]
            mr['futureWeakMakerFillShares5s']=float(sum(float(x.get('q') or 0.) for x in win if x.get('role')=='MAKER' and x.get('side')==mr['weakSide']))
            mr['futureWeakMakerFill5s']=int(mr['futureWeakMakerFillShares5s']>EPS)
            if win:
                end=win[-1]; mr['floorDelta5s']=float(end['floor'])-float(mr['floor']); mr['absNetDelta5s']=float(end['absNet'])-float(mr['absNet'])
            else:
                mr['floorDelta5s']=0.0; mr['absNetDelta5s']=0.0
            mr['floorImproved5s']=int(mr['floorDelta5s']>EPS); mr['absNetReduced5s']=int(mr['absNetDelta5s']<-EPS)

    ret={'marketId':mid,'policy':policy,'orders':len(orders),'makerFilledShares':total_fill,'earlyMakerFillShares':early,'earlySurplusFillShares':early_surplus,'earlyFloorDamage':early_damage,'agedOptionFillShares':aged_fill,'counts':dict(counts),'cancelReasons':dict(reasons),'everSafe':fs is not None,'durableBase':dur is not None,'firstSafeMs':None if fs is None else fs['t'],'firstDurableMs':None if dur is None else dur['t'],'final':final,**ps}
    if collect_shadow: ret['shadowRows']=shadow; ret['managementShadowRows']=management_shadow; ret['managementLifecycleRows']=management_lifecycle
    if collect_provenance:
        ret['provenanceJournal']=prov_events;ret['provenanceResponsibilityState']=prov_state;ret['provenanceSummary']={'responsibilities':len(prov_state),'events':len(prov_events),'duplicateExecutionApplicationCount':0,'orphanEventCount':sum(1 for e in prov_events if e.get('responsibility_id') not in prov_state)}
    return ret

def agg(rr):
    if not rr:return {'markets':0}
    fills=sum(r['makerFilledShares'] for r in rr);early=sum(r['earlyMakerFillShares'] for r in rr);c=Counter();rs=Counter()
    for r in rr:c.update(r['counts']);rs.update(r['cancelReasons'])
    return {'markets':len(rr),'makerFilledShares':fills,'earlyMakerFillShares':early,'earlyFillRate':early/fills if fills else 0.,'earlySurplusFillShares':sum(r['earlySurplusFillShares'] for r in rr),'earlyFloorDamage':sum(r['earlyFloorDamage'] for r in rr),'agedOptionFillShares':sum(r['agedOptionFillShares'] for r in rr),'everSafeMarkets':sum(r['everSafe'] for r in rr),'durableBaseMarkets':sum(r['durableBase'] for r in rr),'durableBaseRate':sum(r['durableBase'] for r in rr)/len(rr),'medianFinalFloor':norm.md([r['final']['floor'] for r in rr]),'p10FinalFloor':norm.quant([r['final']['floor'] for r in rr],.1),'medianFinalAbsNet':norm.md([r['final']['absNet'] for r in rr]),'p90FinalAbsNet':norm.quant([r['final']['absNet'] for r in rr],.9),'counts':dict(c),'cancelReasons':dict(rs)}
def paired(rows,cfg):
    return norm.paired(rows,cfg,base='REACTIVE')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--max-markets',type=int,default=4);ap.add_argument('--market-offset',type=int,default=0);a=ap.parse_args();ds=v2.choose_files(a.max_markets+a.market_offset)[a.market_offset:a.market_offset+a.max_markets];rows=[];errs=[]
    for d in ds:
        mid=int(d['marketId'])
        try:r=v2.simulate(d,0,'REACTIVE');r['config']='REACTIVE';rows.append(r)
        except Exception as e:errs.append({'marketId':mid,'config':'REACTIVE','error':f'{type(e).__name__}:{e}'})
        for pol in POLICIES:
            try:r=simulate(d,pol);r['config']=pol;rows.append(r)
            except Exception as e:errs.append({'marketId':mid,'config':pol,'error':f'{type(e).__name__}:{e}'})
        print(json.dumps({'market':mid,'rows':len(rows),'errors':len(errs)},ensure_ascii=False),flush=True)
    ag={'REACTIVE':norm.agg_rows([r for r in rows if r['config']=='REACTIVE'])}
    for p in POLICIES:ag[p]=agg([r for r in rows if r['config']==p])
    pa={p:paired(rows,p) for p in POLICIES}
    rep={'version':'R4_ROLLING_QUEUE_OPTION_LIFECYCLE_V1','researchOnly':True,'question':'After a strict-past thin-queue opportunity acquires a passive option, does Target-derived age×offset lifecycle handling improve realistic Formation versus blind/static queue holding?','preRegistered':{'maxTrueExchangeRestMs':MAX_REST,'lifecycleCadenceMs':CADENCE,'entryGate':'receipt-clock strict-past current two-sided top5 depth <= same-market prior-30s Q25','aheadInsideDefinition':'quote_offset_ticks <= -1, matching Target state-machine Q_AHEAD_INSIDE bin','ROLL_KEEP':'keep while responsibility/MPQ valid','OWNER1 variants':'same lifecycle but at most one live order per side may own queue responsibility at a time; 18-share parent size unchanged','REL_OWNER variants':'Target-derived minimal ownership state: FLAT max 1 live owner/side, WEAK max 2, SURPLUS 0; 18-share parent size unchanged','ROLL_PULL_AHEAD':'ROLL_KEEP but retire live order when ahead/inside','ROLL_REPRICE_AHEAD':'ROLL_KEEP but cancel+reinsert remainder at current same-side touch when ahead/inside'},'guards':{'entryLatencyMs':ENTRY,'responseLatencyMs':RESP,'cancelConfirmationMs':CANCEL_CONFIRM,'clock':'receivedAtMs for runtime L2','noDreamFill':True,'executionTapeV1':True,'trueMatches':True,'queueModel':'risk','winnerUsed':False,'liveChanges':False,'futureFrozenNeedSidePrice':'still used as execution-counterfactual anchor; not runtime promotion evidence','noOutcomeThresholdSweep':True},'aggregate':ag,'pairedVsReactive':pa,'errors':errs,'rows':rows}
    OUT.mkdir(parents=True,exist_ok=True);p=OUT/f'r4_rolling_queue_option_lifecycle_v1_o{a.market_offset}_m{a.max_markets}.json';p.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps({'artifact':str(p.relative_to(ROOT)).replace('\\','/'),'aggregate':ag,'paired':pa,'errors':errs[:5]},ensure_ascii=False))
if __name__=='__main__':main()
