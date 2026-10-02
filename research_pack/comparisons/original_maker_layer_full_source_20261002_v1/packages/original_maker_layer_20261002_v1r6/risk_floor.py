"""Post-observed-FLIP reservation-aware loss bound. No future outcome inputs."""
import atexit,gzip,json,os
from copy import deepcopy
from pathlib import Path

EPS=1e-8
GUARD=None

def bounds(state):
    return {s:float(state['payoff'][s])-float(state['pending_cash']['DOWN' if s=='UP' else 'UP']) for s in ('UP','DOWN')}

def decide(state,side,price,quantity,floor):
    assert side in ('UP','DOWN') and 0<float(price)<1 and float(quantity)>0
    before=bounds(state);after=dict(before);other='DOWN' if side=='UP' else 'UP'
    cost=float(price)*float(quantity);after[other]-=cost
    allowed=min(after.values())>=floor-EPS
    return dict(side=side,price=float(price),proposed_qty=float(quantity),quantity=float(quantity) if allowed else 0.,
                allowed=allowed,reason='RESERVED_WORST_FLOOR' if not allowed else 'WITHIN_RESERVED_WORST_FLOOR',
                floor=floor,reserved_before=before,reserved_after=after,quoted_cost=cost,
                available=before[other]-floor,protected_branch=other)

class Guard:
    def __init__(self,ctx,roles):
        self.ctx=ctx;self.roles=roles;self.mode=os.environ.get('V12G_POSTFLIP_RISK_FLOOR','OFF')
        assert self.mode in ('OFF','ON')
        self.floor=None;self.initial=None;self.last_index=None;self.last_t=None
        self.decisions=[];self.plans=[];self.frames=[]

    def active(self,frame):
        if self.mode=='OFF' or not any(e['kind']=='FLIP' for e in getattr(self.roles,'v12g_events',[])):return False
        index=int(frame['index']);t=int(frame['t'])
        if index!=self.last_index:
            assert self.last_index is None or index>self.last_index
            state=self.ctx.snapshot(frame,frame['ledger']);b=bounds(state);worst=min(b.values())
            assert self.floor is None or worst>=self.floor-EPS,('RESERVED_FLOOR_BREACH',index,self.floor,b,state)
            previous=self.floor;self.floor=worst if previous is None else max(previous,worst)
            entry=dict(t=t,index=index,previous_floor=previous,floor=self.floor,reserved=b,state=deepcopy(state),
                       observed_flips=[dict(e) for e in self.roles.v12g_events if e['kind']=='FLIP'])
            if self.initial is None:self.initial=deepcopy(entry)
            self.frames.append(entry);self.last_index=index;self.last_t=t
        else:assert t==self.last_t
        return True

    def quantity(self,state,side,price,quantity,route,origin,frame=None):
        frame=self.ctx.frame if frame is None else frame
        if frame is None or not self.active(frame):return float(quantity)
        row=decide(state,side,price,quantity,self.floor)
        self.decisions.append(dict(t=int(frame['t']),index=int(frame['index']),origin=origin,route=route,state=deepcopy(state),**row))
        return row['quantity']

    def row(self,row,state,side,price,route,origin):
        if not row.get('eligible'):return row
        q=float(row.get('quantity',15.))
        if self.quantity(state,side,price,q,route,origin)<=0:
            row.update(eligible=False,reason='POSTFLIP_RESERVED_WORST_FLOOR',risk_floor_proposed_quantity=q)
        return row

    def verify(self,frame,producer,ops):
        if self.mode=='OFF':return ops
        enabled=self.active(frame);state=self.ctx.snapshot(frame,frame['ledger']);start=deepcopy(state);births=[]
        for op in ops:
            if op['kind']!='NEW':continue
            if enabled:
                check=decide(state,op['side'],op['price'],op['qty'],self.floor)
                assert check['allowed'],('RISK_FLOOR_COVERAGE_GAP',frame['index'],op,check,state)
            expected=f"{op['side']}_{frame['own_view']['n']+len(births)}"
            assert op['key']==expected,('NONCONTIGUOUS_BIRTH',op,expected)
            state['pending_cash'][op['side']]+=float(op['qty'])*float(op['price'])
            state['pending_qty'][op['side']]+=float(op['qty']);births.append(deepcopy(op))
        self.plans.append(dict(t=int(frame['t']),index=int(frame['index']),enabled=enabled,floor=self.floor,
            state=start,reserved_after=bounds(state),operations=deepcopy(ops),checked_new=len(births)))
        return ops

    def save(self):
        out=os.environ.get('BTC5M_LAN_RESULT_DIR')
        if out and Path(out).is_dir():
            payload=dict(mode=self.mode,initial=self.initial,floor=self.floor,frames=self.frames,decisions=self.decisions,plans=self.plans)
            with gzip.GzipFile(filename=str(Path(out)/'risk_floor_trace.json.gz'),mode='wb',mtime=0) as f:
                f.write(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode())

def install(ctx,roles):
    global GUARD
    assert GUARD is None;GUARD=Guard(ctx,roles);atexit.register(GUARD.save);return GUARD

def verify_plan(frame,producer,ops):
    assert GUARD is not None
    return GUARD.verify(frame,producer,ops)
