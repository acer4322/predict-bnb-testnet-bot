"""Research-process-only receipt integration; no live/global source replacement."""
import os
import copy
from .hft244_receipt_adapter_v1 import Reader,Ledger

EPS=1e-9
REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}


def physical_process(self,t):
    assert not self._receipt_invalid, 'invalid receipt integration state'
    from eof_runtime import check_process_time
    check_process_time(self.bt,t)
    self._receipt_delta_rows=[]
    try:
        batch=self._receipt_reader.peek();before_seq=self._receipt_ledger.sequence
        mapping={}
        for key,o in self.orders.items():
            assert o['n'] not in mapping, 'ambiguous native order id; explicit generation handoff required'
            mapping[o['n']]=(key,o)
        for r in batch:
            assert r['order_id'] in mapping, 'receipt has no policy owner'
            key,o=mapping[r['order_id']]
            assert o['side']==('UP' if r['side']==1 else 'DOWN'), 'owner side mismatch'
            assert o.get('_receiptGeneration',r['generation'])==r['generation'], 'owner generation mismatch'
        self._receipt_ledger.consume(batch)
        changed={}
        for r in batch:
            if r['sequence']<=before_seq:continue
            key,o=mapping[r['order_id']];q=r['qty'];p=r['price'] if r['side']==1 else 1-r['price']
            assert abs(float(o.get('cum',0.))+q-r['cumulative_qty'])<1e-9, 'policy owner quantity diverged'
            n=len(self.fillHist);self.record_fill(t,o['side'],q,p)
            # Preserve existing one-owner-per-process activity-feature clock. Actual
            # per-price receipts and FIFO costs are separately kept losslessly.
            assert len(self.fillHist)==n+1, 'unknown record_fill side effect'
            self.fillHist.pop()
            total=changed.setdefault(key,[0.,0.]);total[0]+=q;total[1]+=q*p
            o['cum']=r['cumulative_qty'];o['_receiptGeneration']=r['generation']
            self._receipt_delta_rows.append(dict(r,key=key,contractPrice=p))
        for key,o in self.orders.items():
            if key in changed:
                q,value=changed[key];self.fillHist.append((t,o['side'],q,value/q));self.fills+=1
            s=self.snap(o)
            assert abs(float(s.get('cumExecQty') or 0.)-float(o.get('cum') or 0.))<1e-9,'unrepresented order fill'
            o['status']=s.get('status')
        self._receipt_ledger.reconcile(self.bt.state_values(0))
        assert all(abs(self.inv[k]-self._receipt_ledger.inv[k])<1e-8 for k in ('UP','DOWN'))
        assert abs(self.cost-self._receipt_ledger.cost)<1e-8
        self._receipt_reader.ack(batch)
    except Exception:
        self._receipt_invalid=True
        raise


def role_process(self,t):
    old_scope=self.scopeSide;old_gen=int(self.scopeGeneration);floor_before=self._physical_floor()
    self._receipt_v2_process(self,t)
    fill_rows=[];counted=set()
    for receipt in self._receipt_delta_rows:
        key=receipt['key'];o=self.orders[key];inc=float(receipt['qty']);price=float(receipt['contractPrice'])
        role=self.key_role.get(key,'UNASSIGNED')
        if key not in counted:self.role_fills[role]+=1;counted.add(key)
        self.role_fill_qty[role]+=inc;repair_alloc=0.;overflow_fill=0.
        if role in REPAIR_ROLES:
            rem_r=max(0.,float(self.keyRepairQuotaRemaining.get(key,0.)))
            repair_alloc=min(inc,rem_r);overflow_fill=max(0.,inc-repair_alloc)
            self.keyRepairQuotaRemaining[key]=max(0.,rem_r-repair_alloc)
            rem_o=max(0.,float(self.keyOverflowQtyRemaining.get(key,0.)))
            unauthorized=max(0.,overflow_fill-rem_o)
            if unauthorized>EPS:self.unauthorizedOverflowQty+=unauthorized
            self.keyOverflowQtyRemaining[key]=max(0.,rem_o-overflow_fill)
        elif role=='SATELLITE_EXPAND':
            overflow_fill=inc
            rem_o=max(0.,float(self.keyOverflowQtyRemaining.get(key,float(o.get('qty') or 0.))))
            unauthorized=max(0.,overflow_fill-rem_o)
            if unauthorized>EPS:self.unauthorizedOverflowQty+=unauthorized
            self.keyOverflowQtyRemaining[key]=max(0.,rem_o-overflow_fill)
        self.totalRepairAllocated+=repair_alloc;self.totalOverflowRealized+=overflow_fill
        overflow_risk=overflow_fill*price
        if overflow_fill>EPS:self.totalOverflowRiskConsumed+=overflow_risk
        fill_rows.append((key,o,inc,role,repair_alloc,overflow_fill,overflow_risk,price))
        ev=dict(t=int(t),event='ROLE_FILL_SPLIT',key=key,role=role,generationAtSubmit=self.key_scope_gen.get(key),
                side=o['side'],price=price,quotePrice=float(o['price']),priceSource='NATIVE_EXECUTION_RECEIPT',
                fillInc=inc,repairAllocated=repair_alloc,overflowRealized=overflow_fill,overflowRisk=overflow_risk,
                receiptSequence=receipt['sequence'],nativeGeneration=receipt['generation'],nativeMaker=receipt['maker'],nativeFee=receipt['fee'])
        self.splitEvents.append(ev);self.slot_history.append(ev)
    if old_scope is not None:
        for key,o,inc,role,rq,oq,orisk,price in fill_rows:
            if oq>EPS and self.key_scope_gen.get(key)==old_gen:
                self.scopeRiskCreditConsumed+=orisk
                if role=='SATELLITE_EXPAND':self.expand_consumed_keys.add(key)
    new_side=self._unmatched_scope_side();realized_repair_credit=0.
    if old_scope is not None and new_side==old_scope:
        rs='DOWN' if old_scope=='UP' else 'UP'
        for key,o,inc,role,rq,oq,orisk,price in fill_rows:
            if role in REPAIR_ROLES and rq>EPS and str(o['side'])==rs and self.key_scope_gen.get(key)==old_gen:
                realized_repair_credit+=rq*(1-price)
    self._sync_scope(int(t),new_side,floor_before)
    if realized_repair_credit>EPS and self.scopeSide==old_scope and self.scopeGeneration==old_gen:
        self.scopeRepairProgressClocks+=1;self.totalRepairProgressClocks+=1
        self.totalRepairCreditValue+=realized_repair_credit;self.scopeRiskCreditTotal+=realized_repair_credit
        ev=dict(t=int(t),event='CONFIRMED_REPAIR_ALLOCATED_CREDIT',generation=self.scopeGeneration,
                scopeSide=self.scopeSide,creditValue=realized_repair_credit,riskCreditTotal=self.scopeRiskCreditTotal,physicalFloor=self._physical_floor())
        self.scope_credit_events.append(ev);self.slot_history.append(ev)
    self._audit_reservation()


from eof_runtime import advance_to as strict_advance_to


class PreviewReceiptAccess:
    """A preview has no authority to drain or acknowledge the real native journal."""
    def __init__(self,projection):self.projection=copy.deepcopy(projection)
    def peek(self):raise RuntimeError('preview forbids native receipt access')
    def ack(self,rows):raise RuntimeError('preview forbids native receipt acknowledgement')


def install_preview_codec(lab,expected_handler):
    assert not getattr(lab,'_receipt_codec_installed',False)
    original_state=lab.policy_state
    def reader_state(reader):
        if isinstance(reader,PreviewReceiptAccess):return copy.deepcopy(reader.projection)
        return dict(format='RECEIPT_READER_V1',asset=reader.asset,capacity=reader.capacity,pending=reader.peek())
    def policy_state(sim):
        state=original_state(sim)
        ledger=state.get('_receipt_ledger')
        if ledger is not None:
            assert isinstance(ledger,Ledger)
            assert set(vars(ledger))=={'seen','owners','sequence','inv','cost','native'},'unknown ledger state cannot be excluded'
            state['_receipt_ledger']=dict(format='RECEIPT_LEDGER_V1',state=copy.deepcopy(vars(ledger)))
            state['_receipt_reader']=reader_state(sim._receipt_reader)
            assert state['_receipt_v2_process'] is expected_handler,'unknown policy handler'
            state['_receipt_v2_process']=dict(module=expected_handler.__module__,qualname=expected_handler.__qualname__)
        return state
    def preview_copy(sim):
        projected=policy_state(sim);other=object.__new__(type(sim));other.__dict__.update(copy.deepcopy(projected))
        for k in lab.INPUTS:
            if hasattr(sim,k):other.__dict__[k]=getattr(sim,k)
        other.bt=lab.ReadOnlyBackend(sim.bt)
        if hasattr(sim,'_receipt_ledger'):
            other._receipt_ledger=copy.deepcopy(sim._receipt_ledger)
            other._receipt_reader=PreviewReceiptAccess(projected['_receipt_reader'])
            other._receipt_v2_process=expected_handler
        return other
    lab.policy_state=policy_state;lab.preview_copy=preview_copy;lab._receipt_codec_installed=True


def install(base,v2,v8,ex,binary,preview_lab):
    assert 'BTC5M_LAN_RESULT_DIR' in os.environ,'research LAN process only'
    assert v2.base is base and v8.v2 is v2
    assert not getattr(base.Sim,'_receipt_installed',False)
    original_init=base.Sim.__init__
    def initialize(self,*args,**kwargs):
        original_init(self,*args,**kwargs)
        self._receipt_reader=Reader(self.bt,binary);self._receipt_ledger=Ledger()
        self._receipt_invalid=False;self._receipt_delta_rows=[]
        # Store on instance as a plain function to avoid an extra implicit binding.
        self._receipt_v2_process=v2.TargetGroundedDistinctSlotSim.process
    base.Sim.__init__=initialize;base.Sim.process=physical_process
    v8.RepairOverflowSplitSim.process=role_process
    ex.advance_to=strict_advance_to;base.Sim._receipt_installed=True
    install_preview_codec(preview_lab,v2.TargetGroundedDistinctSlotSim.process)
