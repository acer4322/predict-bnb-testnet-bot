"""Whole-episode fixed-UP diagnostic using finite OWN-state repair work items."""
import importlib.util
import math
from pathlib import Path


def load_file(name, staged, local):
    path=Path(__file__).with_name(staged)
    if not path.exists():path=Path(__file__).with_name(local)
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


old=load_file('finite_goal_reference','persistent_gate.py','btc5m_persistent_repair_demand_v1.py')
parallel=load_file('parallel_capacity_reference','parallel_gate.py','btc5m_parallel_payoff_gate_v1.py')
MODES=('LEGACY_PERSIST','AUTO_CAP_ONLY','AUTO_REPAIR')
snapshot=parallel.snapshot


class WholeCapacity(parallel.PayoffRepairGate):
    def __init__(self,selection,mode,episode_mode):
        super().__init__(selection,mode)
        self.episode_mode=episode_mode

    def on_frame(self,frame,producer):
        if self.episode_mode=='LEGACY_PERSIST':
            return super().on_frame(frame,producer)
        if not self.started:
            self.started=True
            self.events.append(dict(t=int(frame['t']),reason='QUANTITY_CAP_FROM_FIRST_FRAME',state=snapshot(frame,frame['ledger'])))


def economic_capacity(state,desired,price,ticket,step):
    requested=round(math.floor((ticket+1e-10)/step)*step,8)
    original_deficit=max(0.,desired['DOWN']-state['inv']['DOWN']-state['pending_qty']['DOWN'])
    original_request=round(math.floor((min(ticket,original_deficit)+1e-10)/step)*step,8)
    quantity=max(0.,state['inv']['UP']-state['inv']['DOWN']-state['pending_qty']['DOWN'])
    potential=state['payoff']['DOWN']+state['pending_qty']['DOWN']-state['pending_cash']['DOWN']-state['pending_cash']['UP']
    money=max(0.,-potential/(1.-price)) if 0<price<1 else 0.
    minimum=max(18.,1./price) if 0<price<1 else None
    okay=(minimum is not None and state['payoff']['DOWN']<0 and state['inv']['UP']>state['inv']['DOWN']
          and original_request<minimum and requested>=minimum and requested<=min(quantity,money)+1e-8)
    return dict(economically_eligible=okay,requested=requested,original_deficit=original_deficit,
                original_request=original_request,quantity_capacity=quantity,money_capacity=money,
                price=price,minimum_reference=minimum)


def full_receipts(seen,native_orders):
    mapping={o['n']:key for key,o in native_orders.items()}
    assert len(mapping)==len(native_orders), 'ambiguous native order mapping'
    assert sorted(seen)==list(range(1,len(seen)+1)), 'incomplete accumulated receipt ledger'
    out=[]
    for seq in sorted(seen):
        r=dict(seen[seq]);assert r['sequence']==seq and r['order_id'] in mapping
        out.append(dict(r,key=mapping[r['order_id']],contractPrice=r['price'] if r['side']==1 else 1.-r['price']))
    return out


class SingleRepairDemand(old.SingleRepairDemand):
    def __init__(self,selection,mode,snapshot_fn):
        assert mode in MODES
        # The automatic arms never read the diagnostic checkpoint or its OWN state.
        init=selection if mode=='LEGACY_PERSIST' else dict(t=0,state=dict(owners=[]))
        super().__init__(init,'PERSIST30',snapshot_fn)
        self.episode_mode=mode
        self.works=[];self.current_work=None;self.closed_t=None

    @property
    def visited(self):
        return super().visited if self.episode_mode=='LEGACY_PERSIST' else bool(self.works)

    def observe(self,frame):
        super().observe(frame)
        if self.episode_mode=='LEGACY_PERSIST':return
        if self.goal is not None and self.goal.status!='ACTIVE':
            state=self.snapshot(frame,frame['ledger'])
            self.current_work.update(status=self.goal.status,ended_t=int(frame['t']),end_state=state)
            self.events.append(dict(kind='END',work_id=self.current_work['id'],t=int(frame['t']),status=self.goal.status))
            self.closed_t=int(frame['t']);self.goal=None;self.current_work=None

    def apply(self,frame,desired,price,ticket,step,ask,validate,crossing):
        if self.episode_mode=='LEGACY_PERSIST':return super().apply(frame,desired)
        state=self.snapshot(frame,frame['ledger'])
        capacity=economic_capacity(state,desired,price,ticket,step)
        eligible=capacity['economically_eligible'];reason='ECONOMIC_OR_MINIMUM_CONDITIONS_NOT_MET';conflicts=[]
        if eligible:
            if ask is None or price>=ask-1e-10:
                eligible=False;reason='NOT_A_PASSIVE_QUOTE'
            else:
                validate(frame['world_profile']['asset'],'PASSIVE',price,capacity['requested'],quantity_step=step)
                conflicts=crossing('DOWN',price,[dict(key=o['key'],side=o['side'],price=o['limit']) for o in state['owners']])
                eligible=not conflicts;reason='ELIGIBLE' if eligible else 'PENDING_OWN_CROSS'
        if self.episode_mode=='AUTO_REPAIR' and self.goal is None and self.closed_t!=int(frame['t']) and eligible:
            self.goal=old.FiniteGoal(state,capacity['requested'])
            self.current_work=dict(id=len(self.works)+1,born_t=int(frame['t']),status='ACTIVE',ended_t=None,
                                   target=self.goal.target,initial_state=state,activation=capacity)
            self.works.append(self.current_work)
            self.events.append(dict(kind='BIRTH',work_id=self.current_work['id'],t=int(frame['t']),target=self.goal.target))
        effective=self.goal.desired(desired) if self.goal is not None else desired
        progress=self.goal.update(state,int(frame['t']),int(frame['end'])) if self.goal else None
        self.rows.append(dict(t=int(frame['t']),state=state,original_desired=dict(desired),effective_desired=dict(effective),
             mode=self.episode_mode,work_id=self.current_work['id'] if self.current_work else None,progress=progress,
             floor_increment=max(0.,effective['DOWN']-desired['DOWN']),eligibility=dict(capacity,eligible=eligible,reason=reason,conflicts=conflicts),
             cancel_pending_down=sum(o['qty'] for o in state['owners'] if o['side']=='DOWN' and o['state']=='CANCEL_PENDING')))
        return effective

    def on_plan(self,frame,operations):
        for o in operations:
            if o['kind']=='NEW':self.tracked.add(o['key'])
        super().on_plan(frame,operations)

    def finish(self,ledger,seen,native_orders):
        receipts=full_receipts(seen,native_orders)
        super().finish(ledger,receipts)
        self.final.update(full_raw_receipts=receipts,receipt_history_complete=True,works=self.works,
                          episode_mode=self.episode_mode,
                          all_final_carriers=[dict(key=k,side=ledger.grants[c.parent_id].side,route=c.route,
                              state=c.state,qty=float(c.qty),filled=float(c.filled),payment=float(c.payment),
                              fees=float(c.fees),limit=float(c.limit)) for k,c in ledger.carriers.items()])


def instrument(source,replace):
    source=old.instrument(source,replace)
    marker='  from tools.pair_core_asset_route_sizing_v2 import validate_size'
    source=replace(source,marker,marker+'\n  from tools.hft244_pair_route_legality_v1 import crossing_owners as _goal_crossing')
    marker='desired=self.demand.apply(f,desired)'
    price="round(math.floor((round(1.-x['up_ask'],10)-softplus(w[9])*tick+1e-10)/tick)*tick,10)"
    source=replace(source,marker,f"desired=self.demand.apply(f,desired,{price},math.exp(w[7]),step,passive_ask(f['book'],'DOWN'),validate_size,_goal_crossing)")
    return replace(source,'producer.demand.finish(actual,sim._receipt_delta_rows)',
                   'producer.demand.finish(actual,sim._receipt_ledger.seen,sim.orders)')


def self_test():
    old.self_test();parallel.self_test()
    s=dict(inv=dict(UP=100.,DOWN=40.),payoff=dict(UP=20.,DOWN=-40.),pending_qty=dict(UP=0.,DOWN=10.),pending_cash=dict(UP=0.,DOWN=3.))
    assert economic_capacity(s,dict(UP=100.,DOWN=50.53),.3,30.,.01)['economically_eligible']
    assert economic_capacity(s,dict(UP=100.,DOWN=10.),.3,30.,.01)['economically_eligible']
    assert not economic_capacity(s,dict(UP=100.,DOWN=100.),.3,30.,.01)['economically_eligible']
    assert not economic_capacity(s,dict(UP=100.,DOWN=50.53),.02,30.,.01)['economically_eligible']
    blocked=dict(s,pending_qty=dict(UP=0.,DOWN=50.))
    assert not economic_capacity(blocked,dict(UP=100.,DOWN=90.53),.3,30.,.01)['economically_eligible']
    seen={1:dict(sequence=1,order_id=7,side=-1,price=.7)}
    assert abs(full_receipts(seen,{'DOWN_7':dict(n=7)})[0]['contractPrice']-.3)<1e-12
    assert seen[1].get('key') is None
    try:full_receipts({2:dict(sequence=2,order_id=7,side=-1,price=.7)},{'DOWN_7':dict(n=7)})
    except AssertionError:pass
    else:raise AssertionError('receipt gaps must fail')


if __name__=='__main__':self_test();print('PASS')
