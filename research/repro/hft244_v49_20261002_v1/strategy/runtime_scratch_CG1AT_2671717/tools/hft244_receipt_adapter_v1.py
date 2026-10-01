"""Opt-in research receipt reader; no controller/market-data dependencies."""
import ctypes as c
import math


class Receipt(c.Structure):
    _fields_=[(k,t) for k,t in [
        ('sequence',c.c_uint64),('generation',c.c_uint64),('order_id',c.c_uint64),
        ('receive_ts',c.c_int64),('exchange_ts',c.c_int64),('side',c.c_int64),('maker',c.c_uint64),
        ('qty',c.c_double),('price',c.c_double),('fee',c.c_double),
        ('cumulative_qty',c.c_double),('leaves_qty',c.c_double),('status',c.c_uint64)]]


class Reader:
    def __init__(self,bt,binary,capacity=4096,asset=0):
        self.bt=bt;self.asset=asset;self.capacity=capacity;self.lib=c.CDLL(str(binary))
        self.lib.hashmapbt_receipt_size_v1.restype=c.c_size_t
        assert self.lib.hashmapbt_receipt_size_v1()==c.sizeof(Receipt)==104
        self.enable=self.lib.hashmapbt_enable_receipts_v1;self.enable.argtypes=[c.c_void_p,c.c_size_t,c.c_size_t];self.enable.restype=c.c_bool
        self.get=self.lib.hashmapbt_receipts_v1;self.get.argtypes=[c.c_void_p,c.c_size_t,c.POINTER(c.c_size_t)];self.get.restype=c.c_void_p
        self.ack_fn=self.lib.hashmapbt_ack_receipts_v1;self.ack_fn.argtypes=[c.c_void_p,c.c_size_t,c.c_uint64];self.ack_fn.restype=c.c_bool
        assert self.enable(bt.ptr,asset,capacity), 'receipt API unavailable or enabled too late'

    def peek(self):
        n=c.c_size_t();ptr=self.get(self.bt.ptr,self.asset,c.byref(n))
        assert n.value<=self.capacity, 'unsupported or invalid journal'
        if not n.value:return []
        raw=c.string_at(ptr,n.value*c.sizeof(Receipt))
        owned=(Receipt*n.value).from_buffer_copy(raw)
        rows=[{k:getattr(row,k) for k,_ in Receipt._fields_} for row in owned]
        assert all(r['receive_ts']<=int(self.bt.current_timestamp) for r in rows)
        return rows

    def ack(self,rows):
        if rows:assert self.ack_fn(self.bt.ptr,self.asset,rows[-1]['sequence']), 'journal changed; do not silently discard'


class Ledger:
    def __init__(self):
        self.seen={};self.owners={};self.sequence=0;self.inv={'UP':0.,'DOWN':0.};self.cost=0.
        self.native=dict(position=0.,balance=0.,trading_volume=0.,trading_value=0.,num_trades=0.,fee=0.)

    def consume(self,rows):
        # Validate all receipt identities and ownership before changing ledger state.
        seq=self.sequence;pending={};owners={k:dict(v) for k,v in self.owners.items()}
        for r in rows:
            number=r['sequence'];assert number>0
            if number in self.seen:assert self.seen[number]==r,'conflicting duplicate';continue
            if number in pending:assert pending[number]==r,'conflicting batch duplicate';continue
            assert number==seq+1,'receipt sequence gap'
            assert r['side'] in (1,-1) and r['maker'] in (0,1) and r['status'] in (3,5)
            assert r['generation']>0 and 0<r['qty'] and r['leaves_qty']>=0
            assert all(math.isfinite(r[k]) for k in ['qty','price','fee','cumulative_qty','leaves_qty'])
            assert 0<r['price']<1 and r['exchange_ts']<=r['receive_ts']
            key=(r['order_id'],r['generation']);o=owners.setdefault(key,dict(qty=0.,value=0.,side=r['side']))
            assert o['side']==r['side']
            assert abs(o['qty']+r['qty']-r['cumulative_qty'])<=1e-10*max(1.,r['cumulative_qty']), 'owner cumulative mismatch'
            o['qty']+=r['qty'];o['value']+=r['qty']*r['price'];pending[number]=dict(r);seq=number
        for r in pending.values():
            q=r['qty'];v=q*r['price'];side=r['side'];self.inv['UP' if side==1 else 'DOWN']+=q
            self.cost+=v if side==1 else q-v
            self.native['position']+=side*q;self.native['balance']-=side*v
            self.native['trading_volume']+=q;self.native['trading_value']+=v
            self.native['num_trades']+=1;self.native['fee']+=r['fee']
        self.seen.update(pending);self.owners=owners;self.sequence=seq

    def reconcile(self,state):
        for k,v in self.native.items():
            assert abs(v-float(getattr(state,k)))<1e-9*max(1.,abs(v)), ('receipt/native mismatch',k,v,getattr(state,k))
