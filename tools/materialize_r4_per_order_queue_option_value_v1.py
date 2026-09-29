from __future__ import annotations
import argparse,bisect,json,math,sys,sqlite3,os
from collections import Counter,deque
from pathlib import Path
from statistics import median
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name=='tools' else Path(r'C:\\BTC5M-worker')
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_preposition_responsibility_prune_v2 as v2
from tools import test_r4_queue_rest_latency_normalized_v1 as norm
from tools import hftbacktest_execution_tape_feed_v1 as tape
from tools import hftbacktest_execution_shift_audit_v0 as ex
from src.predict_bot.execution_tape_archive_v1 import load_archive

OUT=ROOT/'data/research/r4_v0/hourly'; ENTRY=1092; RESP=273; CANCEL_CONFIRM=ENTRY+RESP; MAX_REST=5000; CADENCE=500; EPS=1e-9
PUBLIC_SOURCE_DB=ROOT/'data/public_source_snapshot_archive_v2.db'
POLICIES=('FLEX_W500','FLEX_W5000')

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

def simulate(d,policy):
    mid=int(d['marketId']); events,times,meta=tape.build_archive_events(mid,trade_offset='mid'); br=book_rows(mid); pub=public_source_rows(mid)
    # prep with maximal user-side lead so each logical has a 5s true-rest opportunity window.
    orders,takers,dec=v2.prep(d,meta,ENTRY+MAX_REST); takers=[]
    bt=ex.new_bt(events,entry_latency_ms=ENTRY,response_latency_ms=RESP,queue_model='risk');ex.initialize_bt(bt)
    state=(0.,0.,0.,0.,0.);trace=[];hids={};last_exec={};logical_filled={o['logical']:0. for o in orders};logical_fill_events={o['logical']:[] for o in orders};acquisition_features={};next_id=1
    cancel_req=set(); retired=set(); reprice_pending={}; logical_reprice_count=Counter(); counts=Counter(); reasons=Counter(); early=early_surplus=early_damage=total_fill=aged_fill=0.
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
        if retire:retired.add(lid)
        if reprice_px is not None:
            reprice_pending[lid]={'oldHid':hid,'px':float(reprice_px),'at':cur_t+CANCEL_CONFIRM+1,'o':z['o']};counts['repriceRequests']+=1
        return True
    def submit(o,qty,px,kind):
        nonlocal next_id
        if qty<=EPS:return None
        hid=next_id;next_id+=1;ex.submit_native(bt,hid,o['side'],px,qty);hids[hid]={'logical':o['logical'],'o':o,'side':o['side'],'px':float(px),'submittedAt':cur_t,'kind':kind};last_exec[hid]=0.;counts[f'submit_{kind}']+=1
        return hid
    def harvest(t):
        nonlocal state,total_fill,early,early_surplus,early_damage,aged_fill
        for hid in sorted(hids):
            z=hids[hid];s=ex.order_snapshot(bt,hid);cur=float(s.get('cumExecQty') or 0.);prev=last_exec.get(hid,0.);dq=max(0.,cur-prev)
            if dq>EPS:
                pre=v2.geom(state);rel=v2.relation(state,z['side']);state=v2.apply(state,z['side'],z['px'],dq,False);post=v2.geom(state);total_fill+=dq;logical_filled[z['logical']]+=dq;logical_fill_events.setdefault(z['logical'],[]).append({'t':int(t),'q':float(dq),'kind':z.get('kind'),'px':float(z['px'])})
                age=max(0,t-int(z['submittedAt']));aged_fill+=dq if age>=5000 else 0.
                need=int(z['o']['need'])
                is_synth=(z.get('kind')=='SYNTH_STRIKE')
                if is_synth:counts['synthFillShares']+=dq
                if t<need and not is_synth:
                    early+=dq
                    if rel=='SURPLUS':early_surplus+=dq
                    early_damage+=max(0.,pre['floor']-post['floor'])
                trace.append({'t':t,'role':'MAKER','side':z['side'],'q':dq,'px':z['px'],'logical':z['logical'],'orderAgeMs':age,'preNeed':(t<need and not is_synth),'syntheticStrikeMode':is_synth,'relationBefore':rel,**post})
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
            prearm_window = 500 if policy=='FLEX_W500' else 1000 if policy=='FLEX_W1000' else 2000 if policy=='FLEX_W2000' else 5000
            if not (need-ENTRY-prearm_window<=cur_t<=need-ENTRY):continue
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
            if lid not in acquisition_features:
                bs0=state_at(br,cur_t); ok0,thin0=thin_gate(br,cur_t); g0=v2.geom(state)
                live0=live_rows()
                same0=[x for x in live0 if x[1]['side']==o['side'] and x[0] not in cancel_req]
                opp0=[x for x in live0 if x[1]['side']!=o['side'] and x[0] not in cancel_req]
                acquisition_features[lid]={
                    'marketId':mid,'logical':lid,'side':o['side'],'needMs':need,'acquireMs':int(cur_t),'leadToNeedMs':int(need-cur_t),
                    'remainingQty':float(rem),'relation':rel,'floor':float(g0['floor']),'upside':float(g0['upside']),'absNet':float(g0['absNet']),'coverage':float(g0['coverage']),'edge':float(g0['edge']),
                    'sameSideLiveOwners':len(same0),'oppSideLiveOwners':len(opp0),'quoteOffsetTicks':offset_ticks(o['side'],submit_px,bs0),
                    'top5':None if not thin0 else thin0.get('top5'),'q25Top5':None if not thin0 else thin0.get('q25Top5'),'churn1s':None if not thin0 else thin0.get('churn1s'),
                    'thinGate':bool(ok0),'policy':policy}
            submit(o,rem,submit_px,submit_kind);counts['queueGateAcquisitions']+=1
    def lifecycle_tick():
        bs=state_at(br,cur_t)
        for hid,z,s,rem in list(live_rows()):
            if hid in cancel_req: continue
            o=z['o']; lid=z['logical']
            if v2.relation(state,z['side'])=='SURPLUS': request_cancel(hid,'LOST_RESPONSIBILITY',retire=True); continue
            if v2.mpq_base_break(state,z['side'],z['px'],rem): request_cancel(hid,'MPQ_BASE_BREAK',retire=True); continue
            off=offset_ticks(z['side'],z['px'],bs); age=max(0,cur_t-int(z['submittedAt']))
            if off is None: continue
            if policy.startswith('FLEX_W') and cur_t>=int(o['need']) and age>=1000 and off>=2.0:
                g=v2.geom(state); veto=bool(g['edge']>0.0+EPS and g['absNet']<=18.0+EPS)
                if veto:
                    counts['economicVeto_SMALLGAP']+=1
                else:
                    touch=touch_target_px(z['side'],bs)
                    if touch is not None:
                        ticks_back=1 if logical_reprice_count[lid]==0 else 0
                        px=max(.01,min(.99,round(float(touch)-.01*ticks_back+1e-12,2)))
                        if abs(px-z['px'])>=.005:
                            if request_cancel(hid,f'{policy}_REPRICE',reprice_px=px):
                                logical_reprice_count[lid]+=1; counts['staleFarRepriceRequests']+=1; counts[f'reprice_{policy}']+=1; continue
            if off<=-1.0: counts['aheadInsideChecks']+=1
            elif age>=5000: counts['agedKeepChecks']+=1
            elif off>=4.0: counts['deepKeepChecks']+=1
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
                acquisition_tick();synthetic_strike_mode_tick();lifecycle_tick()
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
    finally:bt.close()
    tr=sorted(trace,key=lambda x:x['t']);fs=next((x for x in tr if x['floor']>=0),None);dur=v2.durable(tr);final=v2.geom(state);ps=v2.path_stats(tr)
    logical_summary={}
    for o in orders:
        lid=o['logical']; need=int(o['need']); ev=logical_fill_events.get(lid,[])
        def qwin(lo,hi):
            return sum(float(e['q']) for e in ev if int(lo)<=int(e['t'])<=int(hi))
        logical_summary[lid]={
            'side':o['side'],'needMs':need,'qty':float(o['qty']),
            'preNeedFillShares':sum(float(e['q']) for e in ev if int(e['t'])<need),
            'fill1sAfterNeed':qwin(need,need+1000),'fill3sAfterNeed':qwin(need,need+3000),'fill5sAfterNeed':qwin(need,need+5000),
            'finalFillShares':sum(float(e['q']) for e in ev),'firstFillMs':min([int(e['t']) for e in ev],default=None)}
    return {'marketId':mid,'policy':policy,'orders':len(orders),'makerFilledShares':total_fill,'earlyMakerFillShares':early,'earlySurplusFillShares':early_surplus,'earlyFloorDamage':early_damage,'agedOptionFillShares':aged_fill,'counts':dict(counts),'cancelReasons':dict(reasons),'everSafe':fs is not None,'durableBase':dur is not None,'firstSafeMs':None if fs is None else fs['t'],'firstDurableMs':None if dur is None else dur['t'],'final':final,'logicalSummary':logical_summary,'acquisitionFeatures':acquisition_features,**ps}

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
    rep={'version':'R4_ROLLING_QUEUE_OPTION_LIFECYCLE_V1','researchOnly':True,'question':'Zero-Taker Maker Flex prearm-window test: keep dynamic D2 post-need reprice and small-gap positive-edge veto fixed, vary only queue-option prearm window 0.5/1/2/5s.','preRegistered':{'maxTrueExchangeRestMs':MAX_REST,'lifecycleCadenceMs':CADENCE,'entryGate':'receipt-clock strict-past current two-sided top5 depth <= same-market prior-30s Q25','aheadInsideDefinition':'quote_offset_ticks <= -1, matching Target state-machine Q_AHEAD_INSIDE bin','ROLL_KEEP':'keep while responsibility/MPQ valid','OWNER1 variants':'same lifecycle but at most one live order per side may own queue responsibility at a time; 18-share parent size unchanged','REL_OWNER variants':'Target-derived minimal ownership state: FLAT max 1 live owner/side, WEAK max 2, SURPLUS 0; 18-share parent size unchanged','ROLL_PULL_AHEAD':'ROLL_KEEP but retire live order when ahead/inside','ROLL_REPRICE_AHEAD':'ROLL_KEEP but cancel+reinsert remainder at current same-side touch when ahead/inside'},'guards':{'entryLatencyMs':ENTRY,'responseLatencyMs':RESP,'cancelConfirmationMs':CANCEL_CONFIRM,'clock':'receivedAtMs for runtime L2','noDreamFill':True,'noTaker':True,'executionTapeV1':True,'trueMatches':True,'queueModel':'risk','winnerUsed':False,'liveChanges':False,'futureFrozenNeedSidePrice':'still used as execution-counterfactual anchor; not runtime promotion evidence','noOutcomeThresholdSweep':True},'aggregate':ag,'pairedVsReactive':pa,'errors':errs,'rows':rows}
    OUT.mkdir(parents=True,exist_ok=True); p=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if os.environ.get('BTC5M_LAN_RESULT_DIR') else OUT/f'r4_maker_only_d2_timing_v1_o{a.market_offset}_m{a.max_markets}.json'; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8'); print(json.dumps({'artifact':str(p),'aggregate':ag,'paired':pa,'errors':errs[:5]},ensure_ascii=False))
if __name__=='__main__':main()
