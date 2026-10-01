import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
"""Native V49: economically qualified early repair, with optional re-borrow protection."""
import argparse,gzip,hashlib,importlib.util,json,os,socket,sys
from pathlib import Path
from economics import proposal,admission,with_plan,self_test
from qualified_work import SERVICE as qualified_service
from repair import select as select_postflip,record as record_postflip
from preparation import projection,self_test as preparation_test
assert socket.gethostname().upper()=='DESKTOP-JIERAGF'
P=Path(__file__).resolve().parent;ap=argparse.ArgumentParser(add_help=False)
ap.add_argument('--variant',choices=['OFF','COUNT_STRICT','PREPARE'],required=True);a,rest=ap.parse_known_args()
contract=json.loads((P/'actor_contract.json').read_text());BASE=Path('C:/BTC5M-worker/.lan_worker_v1/staging')/contract['baseline_package']
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest();assert sha(BASE/'manifest.json')==contract['baseline_manifest_sha256']
# V12g queue-model switch (inactive unless V12G_QUEUE is set): wraps tools.hftbacktest_execution_shift_audit_v0.new_bt after it is
# imported by the frozen runner and forces its queue_model argument (engine default: 'risk' = risk-averse, back of queue,
# advances only on trades). Latency unchanged (250 ms entry / 250 ms response).
QUEUE=os.environ.get('V12G_QUEUE') or None
V12G_Q={'calls':0,'orig':[],'latency':[]}
if QUEUE is not None:
    assert QUEUE in ('log','risk'),QUEUE
    import builtins as _bi
    _orig_import=_bi.__import__
    def _v12g_import(name,globals=None,locals=None,fromlist=(),level=0):
        m_=_orig_import(name,globals,locals,fromlist,level)
        ex_=sys.modules.get('tools.hftbacktest_execution_shift_audit_v0')
        # r1: the module is in sys.modules while it is still executing; patch only once new_bt exists (later imports re-check).
        if ex_ is not None and not getattr(ex_,'_v12g_queue_patched',False) and hasattr(ex_,'new_bt'):
            nb0_=ex_.new_bt
            def new_bt(events,*,entry_latency_ms,response_latency_ms,queue_model):
                V12G_Q['calls']+=1;V12G_Q['orig'].append(queue_model);V12G_Q['latency'].append([int(entry_latency_ms),int(response_latency_ms)])
                return nb0_(events,entry_latency_ms=entry_latency_ms,response_latency_ms=response_latency_ms,queue_model=QUEUE)
            ex_.new_bt=new_bt;ex_._v12g_queue_patched=True
        return m_
    _bi.__import__=_v12g_import
sys.path.insert(0,str(BASE));spec=importlib.util.spec_from_file_location('preparation_immutable_parent',BASE/'money_runner.py');module=importlib.util.module_from_spec(spec);from sizing import money_source;exec(compile(money_source((BASE/'money_runner.py').read_text(encoding='utf8')),str(BASE/'money_runner.py'),'exec'),module.__dict__)

class Context:
    def __init__(self):self.peak=0.;self.first=None;self.frame=None;self.events=[];self.admissions=[];self.snapshot=None;self.producer=None;self.prepare=None;self.cancel_requests=[];self.first_active=None;self.scope_blocks=0
    def observe(self,frame):
        self.frame=frame;g=float(frame['own_view']['inv'][module.roles.strong])-float(frame['own_view']['cost']);self.peak=max(self.peak,g)
        if a.variant!='PREPARE' or self.first is not None or self.snapshot is None or not frame['start']<=frame['t']<frame['end']:return
        if self.producer is not None:
            total=len(self.producer.opportunity.submissions)+len(self.producer.coordination.submissions)+len(self.producer.general_finite_active.submissions)
            if total>=5:return
        actual=self.snapshot(frame,frame['ledger'])
        possible,removed=projection(actual,module.roles.strong,frame.get('cancellable',{}))
        if not removed:return
        bids=module.roles.weak_bid_book(frame);price=round(1.-max(bids),10) if bids else None;depth=float(bids[max(bids)]) if bids else 0.
        real=proposal(actual,module.roles.strong,price,depth,self.peak,frame['world_profile']['quantity_step'])
        projected=proposal(possible,module.roles.strong,price,depth,self.peak,frame['world_profile']['quantity_step'])
        if projected['eligible'] and not real['eligible']:
            self.prepare={'t':int(frame['t']),'index':int(frame['index']),'actual_proposal':real,'hypothetical_proposal':projected,'candidate_cancellations':removed,'actual_reservations_unchanged':True}
            self.first={'action':'PREPARE_CANCELLABLE_RISK','t':int(frame['t']),'index':int(frame['index']),'state':actual,'preparation':self.prepare}

    def check(self,state,side,p,q,origin):
        if a.variant=='OFF' or self.first is None:return True
        row=admission(state,module.roles.strong,side,p,q,self.peak,a.variant!='OFF')
        self.admissions.append({'t':int(self.frame['t']),'index':int(self.frame['index']),'origin':origin,**row})
        return row['allowed']
GROSS=float(os.environ['V12G_GROSS']) if os.environ.get('V12G_GROSS') else None
FALLBACK_S=float(os.environ.get('V12G_FALLBACK_S') or 30.)
FLIP_DELTA=float(os.environ['V12G_FLIP']) if os.environ.get('V12G_FLIP') else None
NEUTRAL_DELTA=float(os.environ['V12G_NEUTRAL']) if os.environ.get('V12G_NEUTRAL') else None
FALL_DROP=float(os.environ['V12G_FALL_DROP']) if os.environ.get('V12G_FALL_DROP') else None
FALL_WIN_S=float(os.environ.get('V12G_FALL_WIN_S') or 2.);FALL_PAUSE_S=float(os.environ.get('V12G_FALL_PAUSE_S') or 2.)
V12G_FALL={'triggers':{'UP':0,'DOWN':0},'vetoes':0,'cancels':0,'frames':0,'apply_calls':0}
BURST_RISE=float(os.environ['V12G_BURST_RISE']) if os.environ.get('V12G_BURST_RISE') else None
BURST_WIN_S=float(os.environ.get('V12G_BURST_WIN_S') or 2.);BURST_HOLD_S=float(os.environ.get('V12G_BURST_HOLD_S') or 5.);BURST_GAP=float(os.environ.get('V12G_BURST_GAP') or 0.10)
V12G_BURST={'starts':{'UP':0,'DOWN':0},'ends_time':0,'ends_gap':0,'events':[]}
LCR_PX=float(os.environ['V12G_LCR_PX']) if os.environ.get('V12G_LCR_PX') else None
LCR_START_S=float(os.environ.get('V12G_LCR_START_S') or 180.);LCR_STOP_S=float(os.environ.get('V12G_LCR_STOP_S') or 10.);LCR_EVERY_S=float(os.environ.get('V12G_LCR_EVERY_S') or 2.);LCR_QTY=float(os.environ.get('V12G_LCR_QTY') or 15.)
V12G_LCR={'orders':0,'qty':0.,'rejected':0,'crossing_skips':0,'last_t':-10**15,'first_t':None,'events':[]}
FLIPCAP=float(os.environ['V12G_FLIPCAP']) if os.environ.get('V12G_FLIPCAP') else None
V12G_FLIPCAP={'vetoes':0,'flips':0,'refs':[]}
LEANCAP=float(os.environ['V12G_LEANCAP']) if os.environ.get('V12G_LEANCAP') else None
V12G_LEANCAP={'vetoes':0,'max_rel_gap':0.}
MAXFLIP_MODE=os.environ.get('V12G_MAXFLIP_MODE') or None
ADDCAP=float(os.environ['V12G_ADDCAP']) if os.environ.get('V12G_ADDCAP') else None
ADDCAP_FROM=os.environ.get('V12G_ADDCAP_FROM') or 'DECIDE'
assert ADDCAP_FROM in ('DECIDE','FLIP')
V12G_ADDCAP={'vetoes':0,'vetoes_after_flip':0}
PACE=float(os.environ['V12G_PACE']) if os.environ.get('V12G_PACE') else None
V12G_PACE={'vetoes':0,'cancels':0,'max_filled_gap':0.,'frames_over':0}
AREP_PX=float(os.environ['V12G_AREP_PX']) if os.environ.get('V12G_AREP_PX') else None
AREP_RISE=float(os.environ.get('V12G_AREP_RISE') or 0.02);AREP_WIN_S=float(os.environ.get('V12G_AREP_WIN_S') or 5.)
V12G_AREP={'orders':0,'rejected':0,'crossing_skips':0,'last_t':-10**15,'hist':[],'events':[]}
DEFENSE=bool(os.environ.get('V12G_DEFENSE'))
ADDGAP=float(os.environ['V12G_ADDGAP']) if os.environ.get('V12G_ADDGAP') else None
V12G_ADDGAP={'vetoes':0,'allowed':0,'last_t':-10**15}
WBUDGET=float(os.environ['V12G_WBUDGET']) if os.environ.get('V12G_WBUDGET') else None
V12G_WB={'vetoes':0,'padd_vetoes':0,'min_proj':0.}
PLEAN=float(os.environ['V12G_PLEAN']) if os.environ.get('V12G_PLEAN') else None
DRIVER=bool(os.environ.get('V12G_DRIVER'))
assert not DRIVER, 'V53 excludes optional DRIVER branch'
V12G_PL={'vetoes':0,'padd_vetoes':0,'max_lead':0.}
V12G_DRV={'keys':[],'orders':{'UP':0,'DOWN':0},'requotes':0,'rejected':0,'crossing_skips':0,'to_chosen':0,'to_weak':0}
def v12g_plean_cap(frame,side):
    # V12g price-scaled lean cap: allowed share lead of the chosen side = PLEAN * (chosen-side mid - 0.5), never negative.
    b_=frame.get('book') or {};bb_=b_.get('bids') or {};aa_=b_.get('asks') or {}
    if not bb_ or not aa_:return None
    um_=(max(bb_)+min(aa_))/2.;ms_=um_ if side=='UP' else 1.-um_
    return max(0.,PLEAN*(ms_-0.5))
V12G_DEF={'entered_t':None,'vetoes':0}
assert MAXFLIP_MODE in (None,'NEUTRAL','HOLD')
PADD_PX=float(os.environ['V12G_PADD_PX']) if os.environ.get('V12G_PADD_PX') else None
PREP_PX=float(os.environ['V12G_PREP_PX']) if os.environ.get('V12G_PREP_PX') else None
PAY_QTY=float(os.environ.get('V12G_PAY_QTY') or 15.);PAY_EVERY_S=float(os.environ.get('V12G_PAY_EVERY_S') or 2.);PAY_STOP_S=float(os.environ.get('V12G_PAY_STOP_S') or 10.)
V12G_PAY={'orders':{'PADD':0,'PREP':0},'ordered_qty':{'PADD':0.,'PREP':0.},'last_t':{'PADD':-10**15,'PREP':-10**15},'rejected':0,'crossing_skips':0,'events':[]}
if GROSS is not None:
    # V12g: balanced heavy opening, direction decided when gross inventory reaches GROSS (inactive unless V12G_GROSS is set).
    def gross_observe(self,frame):
        b=frame.get('book') or {};bids=b.get('bids') or {};asks=b.get('asks') or {}
        mid=(max(bids)+min(asks))/2. if bids and asks else None
        if not hasattr(self,'v12g_events'):self.v12g_events=[]
        inv=frame['own_view']['inv'];gross=float(inv['UP'])+float(inv['DOWN'])
        if FALL_DROP is not None and mid is not None:
            # V12g fall guard: block a side whose public mid fell >= FALL_DROP below its max over the last FALL_WIN_S seconds.
            V12G_FALL['frames']+=1;h=self.__dict__.setdefault('v12g_hist',[]);h.append((int(frame['t']),mid))
            while h and h[0][0]<int(frame['t'])-FALL_WIN_S*1000.:h.pop(0)
            blk=self.__dict__.setdefault('v12g_block',{'UP':0,'DOWN':0})
            for s_ in ('UP','DOWN'):
                vals=[m_ if s_=='UP' else 1.-m_ for _,m_ in h]
                if vals[-1]<=max(vals)-FALL_DROP+1e-9:
                    if blk[s_]<=int(frame['t']):V12G_FALL['triggers'][s_]+=1
                    blk[s_]=int(frame['t']+FALL_PAUSE_S*1000.)
        if BURST_RISE is not None and mid is not None:
            # V12g burst: balanced by default; short burst toward the side that just rose (gap-capped).
            hb=self.__dict__.setdefault('v12g_bhist',[]);hb.append((int(frame['t']),mid))
            while hb and hb[0][0]<int(frame['t'])-BURST_WIN_S*1000.:hb.pop(0)
            gap_to=lambda s_:((float(inv[s_])-float(inv['DOWN' if s_=='UP' else 'UP']))/gross) if gross>0 else 0.
            bend=self.__dict__.get('v12g_burst_end')
            if self.side is not None and bend is not None:
                if int(frame['t'])>=bend or gap_to(self.side)>=BURST_GAP-1e-9:
                    V12G_BURST['ends_time' if int(frame['t'])>=bend else 'ends_gap']+=1
                    V12G_BURST['events'].append(dict(kind='END',t=int(frame['t']),side=self.side,gap=gap_to(self.side)));self.side=None;self.v12g_burst_end=None
            if self.side is None:
                for s_ in ('UP','DOWN'):
                    vals=[m_ if s_=='UP' else 1.-m_ for _,m_ in hb]
                    if vals[-1]>=min(vals)+BURST_RISE-1e-9 and gap_to(s_)<BURST_GAP-1e-9:
                        self.side=s_;self.v12g_burst_end=int(frame['t']+BURST_HOLD_S*1000.);V12G_BURST['starts'][s_]+=1
                        if self.birth is None:self.birth=dict(t=frame['t'],index=frame['index'],side=s_,inv=dict(inv),mid=mid,provenance='V12G_BURST')
                        V12G_BURST['events'].append(dict(kind='START',t=int(frame['t']),side=s_,mid=mid,gap=gap_to(s_)));break
            self.rows.append(dict(t=frame['t'],index=frame['index'],side=self.side,inv=dict(inv)));return
        if getattr(self,'v12g_neutral_locked',False):
            pass
        elif self.side is None:
            ready=gross>=GROSS-1e-9 or (frame['t']-frame['start'])>=FALLBACK_S*1000.
            if ready and mid is not None and abs(mid-0.5)>1e-9:
                self.side='UP' if mid>0.5 else 'DOWN'
                self.birth=dict(t=frame['t'],index=frame['index'],side=self.side,inv=dict(inv),mid=mid,provenance='V12G_GROSS_DECISION')
                self.v12g_events.append(dict(kind='DECIDE',t=int(frame['t']),side=self.side,mid=mid,gross=gross,by='GROSS' if gross>=GROSS-1e-9 else 'FALLBACK'))
        elif NEUTRAL_DELTA is not None and mid is not None and (mid if self.side=='UP' else 1.-mid)<=0.5-NEUTRAL_DELTA+1e-9:
            old=self.side;self.side=None;self.v12g_neutral_locked=True
            self.v12g_events.append(dict(kind='NEUTRAL',t=int(frame['t']),frm=old,mid=mid))
        elif DEFENSE and self.__dict__.get('v12g_defense'):
            pass
        elif FLIP_DELTA is not None and mid is not None and DEFENSE and ((mid if self.side=='UP' else 1.-mid)<=0.5-FLIP_DELTA+1e-9):
            self.v12g_defense=True;V12G_DEF['entered_t']=int(frame['t'])
            self.v12g_events.append(dict(kind='DEFENSE',t=int(frame['t']),side=self.side,mid=mid))
        elif FLIP_DELTA is not None and mid is not None:
            strong_mid=mid if self.side=='UP' else 1.-mid
            if strong_mid<=0.5-FLIP_DELTA+1e-9 and MAXFLIP_MODE is not None and self.__dict__.get('v12g_nflips',0)>=1:
                if MAXFLIP_MODE=='NEUTRAL':
                    old=self.side;self.side=None;self.v12g_neutral_locked=True
                    self.v12g_events.append(dict(kind='NEUTRAL_AFTER_FLIP',t=int(frame['t']),frm=old,mid=mid))
                elif not self.__dict__.get('v12g_hold_logged'):
                    self.v12g_hold_logged=True;self.v12g_events.append(dict(kind='HOLD_IGNORED',t=int(frame['t']),side=self.side,mid=mid))
            elif strong_mid<=0.5-FLIP_DELTA+1e-9:
                old=self.side;self.side='DOWN' if old=='UP' else 'UP';self.v12g_nflips=self.__dict__.get('v12g_nflips',0)+1
                self.v12g_events.append(dict(kind='FLIP',t=int(frame['t']),frm=old,to=self.side,mid=mid))
                if FLIPCAP is not None:
                    self.v12g_flip_ref=dict(side=self.side,base=float(inv[self.side]),gross=gross,t=int(frame['t']));V12G_FLIPCAP['flips']+=1
                    if len(V12G_FLIPCAP['refs'])<50:V12G_FLIPCAP['refs'].append(dict(self.v12g_flip_ref))
        self.rows.append(dict(t=frame['t'],index=frame['index'],side=self.side,inv=dict(inv)))
    import types as _types
    module.roles.observe=_types.MethodType(gross_observe,module.roles)  # instance only: V49 self_test uses fresh Roles()
from risk_floor import install as install_risk_floor
from governor import install as install_governor
ctx=Context();governor=install_governor(ctx,module.roles);risk_guard=install_risk_floor(ctx,module.roles);checks={'economics':self_test(),'preparation':preparation_test()};original_exposure=module.ExposureIntent
class ObservedExposure(original_exposure):
    def apply(self,legacy,own_net,frame):
        value=super().apply(legacy,own_net,frame);ctx.observe(frame);return value
module.ExposureIntent=ObservedExposure;old_load=module.load

def load(name,path):
    from sizing import adapt
    result=adapt(name,old_load(name,path))
    if name=='intent_clock_wrapper':
        from eof_runtime import instrument_wrapper
        instrument_wrapper(result)
    if name=='payoff_repair_gate':ctx.snapshot=result.snapshot
    if name=='tail_new_stop':
        inherited=result.TailNewStop;instrument=result.instrument
        class ProtectedTail(inherited):
            def adjust(self,frame,draft,side,route,qty,price):
                return governor.pre_reserve(frame,draft,side,route,qty,price)
            def veto(self,frame,side,route,qty,price,draft=None):
                gate_=ADDGAP is not None and route=='PASSIVE' and module.roles.side is not None and side==module.roles.side
                if gate_ and int(frame['t'])-V12G_ADDGAP['last_t']<ADDGAP*1000.-1e-9:
                    V12G_ADDGAP['vetoes']+=1;return True
                if PLEAN is not None and module.roles.side is not None and side==module.roles.side:
                    inv__=frame['own_view']['inv'];w__='DOWN' if side=='UP' else 'UP';g__=float(inv__['UP'])+float(inv__['DOWN']);cap__=v12g_plean_cap(frame,side)
                    try:
                        pq__=float(ctx.snapshot(frame,draft if draft is not None else frame['ledger'])['pending_qty'][side])
                    except Exception:
                        pq__=0.
                    if g__>0 and cap__ is not None:
                        lead__=(float(inv__[side])+pq__+float(qty)-float(inv__[w__]))/g__;V12G_PL['max_lead']=max(V12G_PL['max_lead'],(float(inv__[side])-float(inv__[w__]))/g__)
                        if lead__>cap__+1e-9:
                            V12G_PL['vetoes']+=1;return True
                if WBUDGET is not None and module.roles.side is not None and side==module.roles.side:
                    # V12g weak-branch budget: projected weak branch after all chosen-side resting orders and this order fill must stay >= -WBUDGET.
                    inv__=frame['own_view']['inv'];w__='DOWN' if side=='UP' else 'UP';cost__=float(frame['own_view']['cost'])
                    try:
                        pc__=float(ctx.snapshot(frame,draft if draft is not None else frame['ledger'])['pending_cash'][side])
                    except Exception:
                        pc__=0.
                    proj__=float(inv__[w__])-cost__-pc__-float(qty)*float(price);V12G_WB['min_proj']=min(V12G_WB['min_proj'],proj__)
                    if proj__<-WBUDGET-1e-9:
                        V12G_WB['vetoes']+=1;return True
                if DEFENSE and getattr(module.roles,'v12g_defense',False) and module.roles.side is not None and side==module.roles.side:
                    V12G_DEF['vetoes']+=1;return True
                if PACE is not None and module.roles.side is not None and side==module.roles.side:
                    inv__=frame['own_view']['inv'];w__='DOWN' if side=='UP' else 'UP';g__=float(inv__['UP'])+float(inv__['DOWN'])
                    try:
                        pend__=float(ctx.snapshot(frame,draft if draft is not None else frame['ledger'])['pending_qty'][side])
                    except Exception:
                        pend__=0.
                    if g__>0 and (float(inv__[side])+pend__-float(inv__[w__]))/g__>=PACE-1e-9:
                        V12G_PACE['vetoes']+=1;return True
                if ADDCAP is not None and route=='PASSIVE' and module.roles.side is not None and side==module.roles.side and float(price)>ADDCAP+1e-9:
                    nfl_=getattr(module.roles,'v12g_nflips',0)
                    if ADDCAP_FROM=='DECIDE' or nfl_>=1:
                        V12G_ADDCAP['vetoes']+=1;V12G_ADDCAP['vetoes_after_flip']+=int(nfl_>=1);return True
                if LEANCAP is not None and module.roles.side is not None and side==module.roles.side:
                    inv__=frame['own_view']['inv'];g__=float(inv__['UP'])+float(inv__['DOWN'])
                    if g__>0:
                        rel__=(float(inv__[side])-float(inv__['DOWN' if side=='UP' else 'UP']))/g__;V12G_LEANCAP['max_rel_gap']=max(V12G_LEANCAP['max_rel_gap'],rel__)
                        if rel__>=LEANCAP-1e-9:
                            V12G_LEANCAP['vetoes']+=1;return True
                ref_=getattr(module.roles,'v12g_flip_ref',None)
                if FLIPCAP is not None and ref_ is not None and side==ref_['side'] and float(frame['own_view']['inv'][side])-ref_['base']>=FLIPCAP*ref_['gross']-1e-9:
                    V12G_FLIPCAP['vetoes']+=1;return True
                if FALL_DROP is not None and route=='PASSIVE' and getattr(module.roles,'v12g_block',{}).get(side,0)>int(frame['t']):
                    V12G_FALL['vetoes']+=1;return True
                old=super().veto(frame,side,route,qty,price)
                if old or a.variant=='OFF' or ctx.first is None:res_=old
                else:
                    assert draft is not None
                    res_=not ctx.check(ctx.snapshot(frame,draft),side,price,qty,'NATIVE_PRE_RESERVE_'+route)
                if not res_ and risk_guard.active(frame):
                    assert draft is not None
                    res_=risk_guard.quantity(ctx.snapshot(frame,draft),side,price,qty,route,'NATIVE_PRE_RESERVE',frame=frame)<=0
                if gate_ and not res_:V12G_ADDGAP['last_t']=int(frame['t']);V12G_ADDGAP['allowed']+=1
                return res_
        result.TailNewStop=ProtectedTail
        def install(source,replace):
            source=instrument(source,replace);count=0
            for args in ["ss, 'PASSIVE', qty, price","ss, exec_route, qty, exec_price","side, 'ACTIVE', qty, float(ask)","s, 'PASSIVE', qty, price"]:
                old='self.tail_new.veto(f, '+args+')';count+=source.count(old);source=source.replace(old,'self.tail_new.veto(f, '+args+', draft)')
            assert count==5,count
            lines=[]; adjusted=0
            for line in source.splitlines(keepends=True):
                if 'if self.tail_new.veto(f, ' in line:
                    indent=line[:len(line)-len(line.lstrip())]
                    call=line.split('self.tail_new.veto(f, ',1)[1].split(', draft)',1)[0]
                    side,route,quantity,price=call.split(', ',3)
                    assert quantity=='qty'
                    lines.append(indent+f'qty=self.tail_new.adjust(f, draft, {side}, {route}, qty, {price})\n')
                    lines.append(indent+'if qty<=0:continue\n');adjusted+=1
                lines.append(line)
            assert adjusted==5,adjusted
            return ''.join(lines)
        result.instrument=install
    if name in ['active_opportunity','commitment_repair_probe','reexposure_coordination','general_finite_active']:
        original_decide=result.decide
        def guarded_decide(*args,**kwargs):
            row=original_decide(*args,**kwargs)
            if a.variant=='OFF' or ctx.first is None or not row.get('eligible'):return row
            if name!='commitment_repair_probe' and ctx.producer is not None:
                total=len(ctx.producer.opportunity.submissions)+len(ctx.producer.coordination.submissions)+len(ctx.producer.general_finite_active.submissions)
                if total>=5:
                    ctx.scope_blocks+=1
                    row.update(eligible=False,reason='SHARED_ORIGINAL_ACTIVE_COUNT_AUTHORITY')
                    return row
            state=args[0];operations=args[5] if name=='active_opportunity' else args[2] if name=='general_finite_active' else args[1]
            p=row.get('active_ask',row.get('price'));q=row.get('quantity',15.)
            if p is None:p=args[3] if name=='active_opportunity' else args[2] if name=='reexposure_coordination' else args[3]
            if not ctx.check(with_plan(state,operations),module.roles.weak,p,q,'ORIGINAL_'+name):row.update(eligible=False,reason='QUALIFIED_RESTORATION_RESERVE_PROTECTION')
            return row
        economic_decide=guarded_decide
        def risk_decide(*args,**kwargs):
            row=economic_decide(*args,**kwargs)
            if not row.get('eligible') or ctx.frame is None:return row
            state=args[0];operations=args[5] if name=='active_opportunity' else args[2] if name=='general_finite_active' else args[1]
            p=row.get('active_ask',row.get('price'))
            if p is None:p=args[3] if name=='active_opportunity' else args[2] if name=='reexposure_coordination' else args[3]
            governor.row(row,with_plan(state,operations),module.roles.weak,p,'PASSIVE' if name=='commitment_repair_probe' else 'ACTIVE','ORIGINAL_'+name)
            return risk_guard.row(row,with_plan(state,operations),module.roles.weak,p,'PASSIVE' if name=='commitment_repair_probe' else 'ACTIVE','ORIGINAL_'+name)
        result.decide=risk_decide
    if name=='dynamic_direction_bridge':
        bridge_instrument=result.instrument
        def checked_bridge(source,replace):
            source=bridge_instrument(source,replace)
            marker=next(line for line in source.splitlines() if line.strip()=='return _original_envelope(f, producer, ops)')
            assert source.count(marker)==1,(marker,source.count(marker))
            indent=marker[:len(marker)-len(marker.lstrip())]
            return source.replace(marker,indent+'from governor import verify_plan as govern_plan\n'+indent+'govern_plan(f, ops)\n'+indent+'from risk_floor import verify_plan\n'+indent+'verify_plan(f, producer, ops)\n'+marker,1)
        result.instrument=checked_bridge
    if name=='general_finite_active':
        inherited=result.GeneralFiniteActive
        class QualifiedActive(inherited):
            def apply(self,frame,producer,operations,validate,crossing):
                if PACE is not None and module.roles.side is not None:
                    s__=module.roles.side;w__='DOWN' if s__=='UP' else 'UP';inv__=frame['own_view']['inv'];g__=float(inv__['UP'])+float(inv__['DOWN'])
                    if g__>0:
                        fg__=(float(inv__[s__])-float(inv__[w__]))/g__;V12G_PACE['max_filled_gap']=max(V12G_PACE['max_filled_gap'],fg__)
                        if fg__>=PACE-1e-9:
                            V12G_PACE['frames_over']+=1;st__=ctx.snapshot(frame,frame['ledger']);pl__=[o['key'] for o in operations if o['kind']=='CANCEL']
                            for ow in st__['owners']:
                                if ow['side']==s__ and ow['state']!='CANCEL_PENDING' and frame.get('cancellable',{}).get(ow['key'],False) and ow['key'] not in pl__:
                                    operations=[*operations,{'kind':'CANCEL','key':ow['key'],'origin':'WHOLE_POLICY','reason':'V12G_PACE'}];pl__.append(ow['key']);V12G_PACE['cancels']+=1
                if FALL_DROP is not None:
                    V12G_FALL['apply_calls']+=1;blk_=getattr(module.roles,'v12g_block',{})
                    if any(v_>int(frame['t']) for v_ in blk_.values()):
                        st_=ctx.snapshot(frame,frame['ledger']);planned_=[o['key'] for o in operations if o['kind']=='CANCEL']
                        for ow in st_['owners']:
                            if blk_.get(ow['side'],0)>int(frame['t']) and ow['state']!='CANCEL_PENDING' and frame.get('cancellable',{}).get(ow['key'],False) and ow['key'] not in planned_:
                                operations=[*operations,{'kind':'CANCEL','key':ow['key'],'origin':'WHOLE_POLICY','reason':'V12G_FALLING_SIDE'}];planned_.append(ow['key']);V12G_FALL['cancels']+=1
                if LCR_PX is not None and frame['start']<=frame['t']<frame['end'] and (frame['t']-frame['start'])>=LCR_START_S*1000. and (frame['end']-frame['t'])>LCR_STOP_S*1000. and int(frame['t'])-V12G_LCR['last_t']>=LCR_EVERY_S*1000. and not any(o['kind']=='NEW' and o.get('route')=='ACTIVE' for o in operations):
                    # V12g late cheap repair: buy the cheap underdog while its branch is negative (inactive unless V12G_LCR_PX is set).
                    b_=frame.get('book') or {};bb_=b_.get('bids') or {};aa_=b_.get('asks') or {}
                    if bb_ and aa_:
                        dog=('DOWN' if (max(bb_)+min(aa_))/2.>=0.5 else 'UP');ask_=((frame.get('quotes') or {}).get(dog) or {}).get('ask');inv_=frame['own_view']['inv'];cost_=float(frame['own_view']['cost'])
                        if ask_ is not None and 0.<float(ask_)<=LCR_PX+1e-9 and float(inv_[dog])-cost_<0:
                            st2=ctx.snapshot(frame,frame['ledger']);owners_=[{'key':o['key'],'side':o['side'],'price':o['limit']} for o in st2['owners']]
                            if crossing(dog,float(ask_),owners_):V12G_LCR['crossing_skips']+=1
                            else:
                                try:
                                    validate(frame['world_profile']['asset'],'ACTIVE',float(ask_),LCR_QTY,quantity_step=frame['world_profile']['quantity_step']);ok_=True
                                except Exception:
                                    ok_=False;V12G_LCR['rejected']+=1
                                if ok_:
                                    n_=frame['own_view']['n']+sum(o['kind']=='NEW' for o in operations)
                                    op_={'kind':'NEW','key':f'{dog}_{n_}','parent_id':module.roles.pid(dog),'side':dog,'route':'ACTIVE','price':float(ask_),'qty':LCR_QTY,'role':'V12G_LATE_CHEAP_REPAIR'}
                                    operations=[*operations,op_];V12G_LCR['orders']+=1;V12G_LCR['qty']+=LCR_QTY;V12G_LCR['last_t']=int(frame['t'])
                                    if V12G_LCR['first_t'] is None:V12G_LCR['first_t']=int(frame['t'])
                                    if len(V12G_LCR['events'])<200:V12G_LCR['events'].append(dict(t=int(frame['t']),side=dog,ask=float(ask_),dog_branch=float(inv_[dog])-cost_))
                if (PADD_PX is not None or PREP_PX is not None) and module.roles.side is not None and frame['start']<=frame['t']<frame['end'] and (frame['end']-frame['t'])>PAY_STOP_S*1000.:
                    # V12g payoff keep-alive: PADD buys the chosen favourite while its branch is negative; PREP buys the cheap weak side while its branch is negative.
                    b_=frame.get('book') or {};bb_=b_.get('bids') or {};aa_=b_.get('asks') or {}
                    if bb_ and aa_:
                        upmid_=(max(bb_)+min(aa_))/2.;s_=module.roles.side;w_='DOWN' if s_=='UP' else 'UP';inv_=frame['own_view']['inv'];cost_=float(frame['own_view']['cost'])
                        cands_=[]
                        if PADD_PX is not None and not getattr(module.roles,'v12g_defense',False) and ((upmid_>=0.5) if s_=='UP' else (upmid_<0.5)) and float(inv_[s_])-cost_<0:cands_.append(('PADD',s_,PADD_PX,'V12G_PAYOFF_ADD'))
                        if PREP_PX is not None and float(inv_[w_])-cost_<0:cands_.append(('PREP',w_,PREP_PX,'V12G_PAYOFF_REPAIR'))
                        for kind_,side_,px_,role_ in cands_:
                            if any(o['kind']=='NEW' and o.get('route')=='ACTIVE' for o in operations):break
                            if int(frame['t'])-V12G_PAY['last_t'][kind_]<PAY_EVERY_S*1000.:continue
                            ask_=((frame.get('quotes') or {}).get(side_) or {}).get('ask')
                            if ask_ is None or not 0.<float(ask_)<=px_+1e-9:continue
                            st3=ctx.snapshot(frame,frame['ledger']);owners3=[{'key':o['key'],'side':o['side'],'price':o['limit']} for o in st3['owners']]
                            if crossing(side_,float(ask_),owners3):V12G_PAY['crossing_skips']+=1;continue
                            if PLEAN is not None and kind_=='PADD':
                                g5=float(inv_['UP'])+float(inv_['DOWN']);cap5=v12g_plean_cap(frame,side_)
                                if g5>0 and cap5 is not None and (float(inv_[side_])+float(st3['pending_qty'][side_])+PAY_QTY-float(inv_[w_]))/g5>cap5+1e-9:V12G_PL['padd_vetoes']+=1;continue
                            if WBUDGET is not None and kind_=='PADD' and float(inv_[w_])-cost_-float(st3['pending_cash'][side_])-PAY_QTY*float(ask_)<-WBUDGET-1e-9:V12G_WB['padd_vetoes']+=1;continue
                            pay_q=governor.quantity(with_plan(st3,operations),side_,float(ask_),PAY_QTY,'ACTIVE',role_)
                            if pay_q<=0:continue
                            if risk_guard.quantity(with_plan(st3,operations),side_,float(ask_),pay_q,'ACTIVE',role_)<=0:continue
                            try:
                                validate(frame['world_profile']['asset'],'ACTIVE',float(ask_),pay_q,quantity_step=frame['world_profile']['quantity_step']);ok3=True
                            except Exception:
                                ok3=False;V12G_PAY['rejected']+=1
                            if not ok3:continue
                            n3=frame['own_view']['n']+sum(o['kind']=='NEW' for o in operations)
                            operations=[*operations,{'kind':'NEW','key':f'{side_}_{n3}','parent_id':module.roles.pid(side_),'side':side_,'route':'ACTIVE','price':float(ask_),'qty':pay_q,'role':role_}]
                            V12G_PAY['orders'][kind_]+=1;V12G_PAY['ordered_qty'][kind_]+=pay_q;V12G_PAY['last_t'][kind_]=int(frame['t'])
                            if len(V12G_PAY['events'])<300:V12G_PAY['events'].append(dict(kind=kind_,t=int(frame['t']),side=side_,ask=float(ask_),branch=float(inv_[side_])-cost_))
                            break
                if DRIVER and PLEAN is not None and module.roles.side is not None and frame['start']<=frame['t']<frame['end'] and (frame['end']-frame['t'])>PAY_STOP_S*1000.:
                    # V12g mid-game driver: keep at most one passive 15-share bid per side at the best bid; buy the chosen side while its
                    # share lead (incl. resting) is below the price-scaled cap, otherwise the weak side. Stale driver bids are re-quoted.
                    b6=frame.get('book') or {};bb6=b6.get('bids') or {};aa6=b6.get('asks') or {}
                    if bb6 and aa6:
                        s6=module.roles.side;w6='DOWN' if s6=='UP' else 'UP';inv6=frame['own_view']['inv'];g6=float(inv6['UP'])+float(inv6['DOWN'])
                        st6=ctx.snapshot(frame,frame['ledger']);pq6=st6['pending_qty'];cap6=v12g_plean_cap(frame,s6)
                        lead6=(float(inv6[s6])+float(pq6[s6])-float(inv6[w6])-float(pq6[w6]))/g6 if g6>0 else 0.
                        tgt6=s6 if (cap6 is not None and lead6<cap6) else w6
                        bid6={'UP':round(max(bb6),2),'DOWN':round(1.-min(aa6),2)}
                        live6={o['key']:o for o in st6['owners'] if o['key'] in V12G_DRV['keys']}
                        planned6={o['key'] for o in operations if o['kind']=='CANCEL'}
                        for k6,o6 in live6.items():
                            if o6['state']!='CANCEL_PENDING' and k6 not in planned6 and frame.get('cancellable',{}).get(k6,False) and float(o6['limit'])<bid6[o6['side']]-1e-9:
                                operations=[*operations,{'kind':'CANCEL','key':k6,'origin':'WHOLE_POLICY','reason':'V12G_DRIVER_REQUOTE'}];planned6.add(k6);V12G_DRV['requotes']+=1
                        if not any(o6['side']==tgt6 and k6 not in planned6 for k6,o6 in live6.items()) and 0.<bid6[tgt6]<1.:
                            owners6=[{'key':o['key'],'side':o['side'],'price':o['limit']} for o in st6['owners']]
                            if crossing(tgt6,float(bid6[tgt6]),owners6):V12G_DRV['crossing_skips']+=1
                            else:
                                try:
                                    validate(frame['world_profile']['asset'],'PASSIVE',float(bid6[tgt6]),15.,quantity_step=frame['world_profile']['quantity_step']);ok6=True
                                except Exception:
                                    ok6=False;V12G_DRV['rejected']+=1
                                if ok6:
                                    n6=frame['own_view']['n']+sum(o['kind']=='NEW' for o in operations);k6=f'{tgt6}_{n6}'
                                    operations=[*operations,{'kind':'NEW','key':k6,'parent_id':module.roles.pid(tgt6),'side':tgt6,'route':'PASSIVE','price':float(bid6[tgt6]),'qty':15.,'role':'V12G_DRIVER'}]
                                    V12G_DRV['keys'].append(k6);V12G_DRV['orders'][tgt6]+=1;V12G_DRV['to_chosen' if tgt6==s6 else 'to_weak']+=1
                if AREP_PX is not None and module.roles.side is not None and frame['start']<=frame['t']<frame['end'] and (frame['end']-frame['t'])>10000.:
                    # V12g active repair: buy a rising weak side at its ask while its branch is negative (inactive unless V12G_AREP_PX is set).
                    b_=frame.get('book') or {};bb_=b_.get('bids') or {};aa_=b_.get('asks') or {}
                    if bb_ and aa_:
                        um_=(max(bb_)+min(aa_))/2.;H_=V12G_AREP['hist'];H_.append((int(frame['t']),um_))
                        while H_ and H_[0][0]<int(frame['t'])-AREP_WIN_S*1000.-1000.:H_.pop(0)
                        w_='DOWN' if module.roles.side=='UP' else 'UP';inv_=frame['own_view']['inv'];cost_=float(frame['own_view']['cost'])
                        old_=[m_ for t_,m_ in H_ if t_<=int(frame['t'])-AREP_WIN_S*1000.]
                        if old_ and float(inv_[w_])-cost_<0 and int(frame['t'])-V12G_AREP['last_t']>=2000 and not any(o['kind']=='NEW' and o.get('route')=='ACTIVE' for o in operations):
                            wm_now=um_ if w_=='UP' else 1.-um_;wm_old=old_[-1] if w_=='UP' else 1.-old_[-1]
                            ask_=((frame.get('quotes') or {}).get(w_) or {}).get('ask')
                            if wm_now-wm_old>=AREP_RISE-1e-9 and ask_ is not None and 0.<float(ask_)<=AREP_PX+1e-9:
                                st4=ctx.snapshot(frame,frame['ledger']);ow4=[{'key':o['key'],'side':o['side'],'price':o['limit']} for o in st4['owners']]
                                if crossing(w_,float(ask_),ow4):V12G_AREP['crossing_skips']+=1
                                else:
                                    try:
                                        validate(frame['world_profile']['asset'],'ACTIVE',float(ask_),15.,quantity_step=frame['world_profile']['quantity_step']);ok4=True
                                    except Exception:
                                        ok4=False;V12G_AREP['rejected']+=1
                                    if ok4:
                                        n4=frame['own_view']['n']+sum(o['kind']=='NEW' for o in operations)
                                        operations=[*operations,{'kind':'NEW','key':f'{w_}_{n4}','parent_id':module.roles.pid(w_),'side':w_,'route':'ACTIVE','price':float(ask_),'qty':15.,'role':'V12G_ACTIVE_REPAIR'}]
                                        V12G_AREP['orders']+=1;V12G_AREP['last_t']=int(frame['t'])
                                        if len(V12G_AREP['events'])<200:V12G_AREP['events'].append(dict(t=int(frame['t']),side=w_,ask=float(ask_),rise=wm_now-wm_old,branch=float(inv_[w_])-cost_))
                if a.variant=='OFF' or not frame['start']<=frame['t']<frame['end']:
                    if ctx.snapshot is not None:qualified_service.observe(frame,ctx.snapshot(frame,frame['ledger']),module.roles.weak,any(e['kind']=='FLIP' for e in getattr(module.roles,'v12g_events',[])))
                    return super().apply(frame,producer,operations,validate,crossing)
                ctx.producer=producer;ctx.observe(frame)
                state=ctx.snapshot(frame,frame['ledger'])
                if a.variant=='PREPARE' and ctx.prepare is not None:
                    planned_cancels={o['key'] for o in operations if o['kind']=='CANCEL'}
                    for owner in state['owners']:
                        if owner['side']!=module.roles.strong or 1.-3.*owner['limit']>=0:continue
                        if owner['state']=='CANCEL_PENDING' or not frame.get('cancellable',{}).get(owner['key'],False) or owner['key'] in planned_cancels:continue
                        operation={'kind':'CANCEL','key':owner['key'],'origin':'WHOLE_POLICY','reason':'QUALIFIED_RESTORATION_PREPARE'}
                        operations=[*operations,operation];planned_cancels.add(owner['key'])
                        ctx.cancel_requests.append({'t':int(frame['t']),'index':int(frame['index']),'owner':dict(owner),'actual_pending_cash':dict(state['pending_cash']),**operation})
                qualified_service.observe(frame,state,module.roles.weak,any(e['kind']=='FLIP' for e in getattr(module.roles,'v12g_events',[])))
                planned=with_plan(state,operations)
                ask=((frame.get('quotes') or {}).get(module.roles.weak) or {}).get('ask')
                bids=module.roles.weak_bid_book(frame);depth=float(bids[max(bids)]) if bids else 0.
                row=proposal(planned,module.roles.strong,ask,depth,ctx.peak,float(frame['world_profile']['quantity_step']))
                row,repair_side,rebound=select_postflip(row,planned,module.roles.strong,module.roles.weak,frame.get('quotes') or {},frame.get('book') or {},ctx.peak,float(frame['world_profile']['quantity_step']),any(e['kind']=='FLIP' for e in getattr(module.roles,'v12g_events',[])))
                if rebound:ask=row['price']
                row.update(t=int(frame['t']),index=int(frame['index']),side=repair_side,activated=ctx.first is not None)
                count=len(producer.opportunity.submissions)+len(producer.coordination.submissions)+len(self.submissions)
                if row['eligible'] and count>=5:
                    row=qualified_service.at_cap(row,frame,state,planned,operations,module.roles.weak,any(e['kind']=='FLIP' for e in getattr(module.roles,'v12g_events',[])),count-len(qualified_service.orders),float(frame['world_profile']['quantity_step']))
                if row['eligible'] and any(o['kind']=='NEW' and o.get('route')=='ACTIVE' for o in operations):row.update(eligible=False,reason='EXISTING_ACTIVE_PLAN_PRIORITY')
                if row['eligible'] and len(planned['owners'])>=frame['world_profile']['max_live_owners']:row.update(eligible=False,reason='RESOURCE_OWNER_LIMIT')
                if row['eligible']:
                    owners=[{'key':o['key'],'side':o['side'],'price':o['limit']} for o in planned['owners']]
                    conflicts=crossing(repair_side,float(ask),owners)
                    if conflicts:row.update(eligible=False,reason='PENDING_OR_PLAN_OWN_CROSS',conflicts=conflicts)
                if row.get('qualified_continuation') and row['eligible']:
                    if producer.tail_new.veto(frame,repair_side,'ACTIVE',row['quantity'],float(ask),frame['ledger']):row.update(eligible=False,reason='ORIGINAL_TAIL_OR_NEW_ADMISSION')
                    elif not ctx.check(planned,repair_side,float(ask),row['quantity'],'QUALIFIED_FINITE_CONTINUATION'):row.update(eligible=False,reason='ORIGINAL_EARNINGS_ADMISSION')
                governor.row(row,planned,repair_side,ask,'ACTIVE','QUALIFIED_REPAIR')
                record_postflip(frame,row,planned,operations)
                risk_guard.row(row,planned,repair_side,ask,'ACTIVE','ACTIVE_QUALIFIED_RATIO_RESTORATION')
                qualified_service.resolved(row)
                ctx.events.append(row)
                if not row['eligible']:return super().apply(frame,producer,operations,validate,crossing)
                q=row['quantity'];validate(frame['world_profile']['asset'],'ACTIVE',float(ask),q,quantity_step=frame['world_profile']['quantity_step'])
                assert abs(float(ask)/frame['world_profile']['tick']-round(float(ask)/frame['world_profile']['tick']))<1e-8
                n=frame['own_view']['n']+sum(o['kind']=='NEW' for o in operations)
                op={'kind':'NEW','key':f'{repair_side}_{n}','parent_id':module.roles.pid(repair_side),'side':repair_side,'route':'ACTIVE','price':float(ask),'qty':q,'role':'ACTIVE_QUALIFIED_RATIO_RESTORATION'}
                if row.get('qualified_continuation'):
                    op['role']='ACTIVE_QUALIFIED_FINITE_CONTINUATION'
                    qualified_service.accepted(frame,op,row)
                else:
                    qualified_service.birth(frame,state,planned,row,any(e['kind']=='FLIP' for e in getattr(module.roles,'v12g_events',[])),op['key'])
                if ctx.first_active is None:ctx.first_active={'t':int(frame['t']),'index':int(frame['index']),'proposal':row,'key':op['key']}
                if ctx.first is None:ctx.first={'action':a.variant,'t':int(frame['t']),'index':int(frame['index']),'state':state,'proposal':row,'key':op['key']}
                self.submissions.append({'t':int(frame['t']),'work_id':'QUALIFIED_RATIO_RESTORATION','released_reservation':0.,'unreserved_after_plan':row['net_unreserved'],**op})
                self.rows.append({'t':int(frame['t']),'eligible':True,'reason':'QUALIFIED_RATIO_RESTORATION','economic_proposal':row,'state':state})
                if producer.demand.rows:self._remember(producer.demand.rows[-1])
                return [*operations,op]
        result.GeneralFiniteActive=QualifiedActive
    from work_continuation import install_module
    install_module(name,result,module.roles,ctx,risk_guard)
    return result
module.load=load;sys.argv=[str(BASE/'money_runner.py')]+rest;exitcode=0
try:module.main()
except SystemExit as error:exitcode=error.code or 0
if '--check-only' in rest:raise SystemExit(exitcode)
out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);rp=out/'result.json'
if rp.exists():
    r=json.loads(rp.read_text())
    if r.get('status')=='COMPLETE':
        payload={'events':ctx.events,'admissions':ctx.admissions,'first':ctx.first,'peak_G':ctx.peak,'checks':checks,'variant':a.variant,'preparation':ctx.prepare,'cancel_requests':ctx.cancel_requests,'first_actual_active':ctx.first_active,'scope_blocks':ctx.scope_blocks}
        with gzip.GzipFile(filename=str(out/'restoration_trace.json.gz'),mode='wb',mtime=0) as f:f.write(json.dumps(payload,sort_keys=True,separators=(',',':')).encode())
        from hard_stop import finish as finish_hard_stop
        r['v57_stop290']=finish_hard_stop(out)
        from demand_scale import finish as finish_demand_scale
        r['v56_demand_scale']=finish_demand_scale(out)
        from sizing import TICKET
        r['v53_sizing']={'passive_ticket':TICKET,'active_pay_ticket':PAY_QTY,'gross_decision_threshold':GROSS,'research_min_notional':1.,'scope':'PASSIVE ticket and dependent availability checks; active quantity rules unchanged','live_eligible':False}
        r['economic_option']={'mode':a.variant,'selection':ctx.first,'anchors':[],'modified_frames':sum(not x['allowed'] for x in ctx.admissions)+sum(x['eligible'] for x in ctx.events),'direction_writer':False,'target_loaded':False,'winner_loaded':False,'original_exit_code':exitcode,'baseline_manifest_sha256':sha(BASE/'manifest.json')}
        from collections import Counter
        r['qualified_restoration']={'variant':a.variant,'first':ctx.first,'proposal_reasons':dict(Counter(x['reason'] for x in ctx.events)),'new_qualified_active':sum(x['eligible'] for x in ctx.events),'blocked_strong':sum(not x['allowed'] and x['side']==module.roles.strong for x in ctx.admissions),'blocked_weak':sum(not x['allowed'] and x['side']==module.roles.weak for x in ctx.admissions),'peak_G':ctx.peak,'checks':checks,'trace_sha256':sha(out/'restoration_trace.json.gz'),'held_amplitude_and_side_unchanged':True,'original_combined_active_cap_retained':True}
        r['qualified_continuation']={'mode':qualified_service.mode,'extension_children':len(qualified_service.orders),'work':qualified_service.work,'original_entry_and_count_unchanged':True}
        r['qualified_restoration']['original_combined_active_cap_retained']=not bool(qualified_service.orders)
        r['qualified_restoration'].update(preparation=ctx.prepare,first_actual_active=ctx.first_active,preparation_cancel_requests=len(ctx.cancel_requests),shared_active_scope_blocks=ctx.scope_blocks)
        if LCR_PX is not None:r['v12g_lcr']=dict(px=LCR_PX,start_s=LCR_START_S,stop_s=LCR_STOP_S,every_s=LCR_EVERY_S,qty=LCR_QTY,**{k:v for k,v in V12G_LCR.items() if k not in ('last_t','qty')},ordered_qty=V12G_LCR['qty'])
        if FLIPCAP is not None:r['v12g_flipcap']=dict(cap=FLIPCAP,**V12G_FLIPCAP)
        if LEANCAP is not None:r['v12g_leancap']=dict(cap=LEANCAP,**V12G_LEANCAP)
        if ADDCAP is not None:r['v12g_addcap']=dict(cap=ADDCAP,frm=ADDCAP_FROM,**V12G_ADDCAP)
        if PACE is not None:r['v12g_pace']=dict(cap=PACE,**V12G_PACE)
        if DEFENSE:r['v12g_defense']=dict(V12G_DEF)
        if QUEUE is not None:r['v12g_queue']=dict(model=QUEUE,calls=V12G_Q['calls'],orig=V12G_Q['orig'][:5],latency=V12G_Q['latency'][:5])
        if PLEAN is not None:r['v12g_plean']=dict(k=PLEAN,**V12G_PL)
        if DRIVER:r['v12g_driver']={k:v for k,v in V12G_DRV.items() if k!='keys'}
        if WBUDGET is not None:r['v12g_wbudget']=dict(budget=WBUDGET,**V12G_WB)
        if ADDGAP is not None:r['v12g_addgap']=dict(gap_s=ADDGAP,vetoes=V12G_ADDGAP['vetoes'],allowed=V12G_ADDGAP['allowed'])
        if AREP_PX is not None:r['v12g_arep']=dict(px=AREP_PX,rise=AREP_RISE,win_s=AREP_WIN_S,orders=V12G_AREP['orders'],rejected=V12G_AREP['rejected'],crossing_skips=V12G_AREP['crossing_skips'],events=V12G_AREP['events'])
        if PADD_PX is not None or PREP_PX is not None:r['v12g_pay']=dict(padd_px=PADD_PX,prep_px=PREP_PX,pay_qty=PAY_QTY,every_s=PAY_EVERY_S,stop_s=PAY_STOP_S,orders=V12G_PAY['orders'],ordered_qty=V12G_PAY['ordered_qty'],rejected=V12G_PAY['rejected'],crossing_skips=V12G_PAY['crossing_skips'],events=V12G_PAY['events'])
        if BURST_RISE is not None:r['v12g_burst']=dict(rise=BURST_RISE,win_s=BURST_WIN_S,hold_s=BURST_HOLD_S,gap=BURST_GAP,starts=V12G_BURST['starts'],ends_time=V12G_BURST['ends_time'],ends_gap=V12G_BURST['ends_gap'],events=V12G_BURST['events'][:400],n_events=len(V12G_BURST['events']))
        if FALL_DROP is not None:r['v12g_fall']=dict(drop=FALL_DROP,win_s=FALL_WIN_S,pause_s=FALL_PAUSE_S,**V12G_FALL)
        if GROSS is not None:r['v12g']=dict(gross=GROSS,fallback_s=FALLBACK_S,flip_delta=FLIP_DELTA,neutral_delta=NEUTRAL_DELTA,events=getattr(module.roles,'v12g_events',[]),final_side=module.roles.side)
        rp.write_text(json.dumps(r,indent=2),encoding='utf-8')
        print(json.dumps({'status':'COMPLETE','variant':a.variant,'activated':ctx.first is not None,'new_qualified_active':r['qualified_restoration']['new_qualified_active'],'original_safety':r['safety_gate']['pass']}),flush=True)
if exitcode:raise SystemExit(exitcode)
