from __future__ import annotations
from dataclasses import dataclass,replace
from typing import Any
import math,queue,threading,time
EPS=1e-9

@dataclass(frozen=True)
class ChildState:
    clientOrderId:str
    side:str
    role:str
    requestedQty:float
    confirmedFilledQty:float
    remainingQty:float
    requestedPrice:float
    cancelPending:bool=False
    terminal:bool=False

@dataclass(frozen=True)
class ReducerSnapshot:
    ingestRevision:int=0
    engineEventSeq:int=0
    bookUpdateId:int=0
    executionRevision:int=0
    bookRevision:int=0
    obligationRevision:int=0
    carrierRevision:int=0
    makerUp:float=0.0
    makerDown:float=0.0
    takerUp:float=0.0
    takerDown:float=0.0
    exactGap:float=0.0
    weakSide:str|None=None
    upBid:float|None=None
    downBid:float|None=None
    upAsk:float|None=None
    downAsk:float|None=None
    children:tuple[ChildState,...]=()
    lastAcceptedKind:str|None=None
    lastAcceptedAtMs:int|None=None
    duplicateEvents:int=0
    backwardWatermarkEvents:int=0

@dataclass(frozen=True)
class ActionBasis:
    executionRevision:int
    obligationRevision:int
    carrierRevision:int
    bookRevision:int

class ContinuousMakerReducerV2:
    """Research-only deterministic reducer. Single writer; snapshots are immutable."""
    def __init__(self): self.s=ReducerSnapshot(); self._seen_local:set[str]=set()
    def snapshot(self)->ReducerSnapshot:return self.s
    @staticmethod
    def _gap(s:ReducerSnapshot):
        net=(s.makerUp+s.takerUp)-(s.makerDown+s.takerDown)
        return abs(net),('DOWN' if net>EPS else 'UP' if net<-EPS else None)
    @staticmethod
    def _children_map(s):return {c.clientOrderId:c for c in s.children}
    def _publish(self,*,kind:str,at_ms:int,**kw):
        ns=replace(self.s,ingestRevision=self.s.ingestRevision+1,lastAcceptedKind=kind,lastAcceptedAtMs=int(at_ms),**kw)
        gap,weak=self._gap(ns)
        if abs(gap-self.s.exactGap)>EPS or weak!=self.s.weakSide:
            ns=replace(ns,exactGap=gap,weakSide=weak,obligationRevision=ns.obligationRevision+1)
        else: ns=replace(ns,exactGap=gap,weakSide=weak)
        self.s=ns;return ns
    def _dup(self):self.s=replace(self.s,duplicateEvents=self.s.duplicateEvents+1);return self.s
    def _back(self):self.s=replace(self.s,backwardWatermarkEvents=self.s.backwardWatermarkEvents+1);return self.s
    def apply(self,e:dict[str,Any])->ReducerSnapshot:
        kind=str(e.get('kind') or '').upper();at=int(e.get('atMs') or 0)
        source=str(e.get('source') or 'LOCAL').upper()
        if source=='ENGINE':
            seq=int(e.get('engineSeq') or 0)
            if seq<=0: raise ValueError('ENGINE event requires positive engineSeq')
            if seq==self.s.engineEventSeq:return self._dup()
            if seq<self.s.engineEventSeq:return self._back()
            # gaps are accepted but visible to caller; adapter is responsible for backfill before promotion.
            engine_seq=seq
        else: engine_seq=self.s.engineEventSeq
        if source=='BOOK':
            bid=int(e.get('bookId') or 0)
            if bid<=0: raise ValueError('BOOK event requires positive bookId')
            if bid==self.s.bookUpdateId:return self._dup()
            if bid<self.s.bookUpdateId:return self._back()
            book_id=bid
        else: book_id=self.s.bookUpdateId
        if source=='LOCAL':
            key=str(e.get('eventKey') or '')
            if key:
                if key in self._seen_local:return self._dup()
                self._seen_local.add(key)
        ch=self._children_map(self.s); exec_rev=self.s.executionRevision;book_rev=self.s.bookRevision;car_rev=self.s.carrierRevision
        vals={'engineEventSeq':engine_seq,'bookUpdateId':book_id}
        if kind=='BOOK_UPDATE':
            top=(e.get('upBid'),e.get('downBid'),e.get('upAsk'),e.get('downAsk'));old=(self.s.upBid,self.s.downBid,self.s.upAsk,self.s.downAsk)
            if top!=old:book_rev+=1
            vals.update(upBid=e.get('upBid'),downBid=e.get('downBid'),upAsk=e.get('upAsk'),downAsk=e.get('downAsk'),bookRevision=book_rev)
        elif kind in {'MAKER_INTENT','TAKER_INTENT'}:
            cid=str(e['clientOrderId']);q=float(e.get('qty') or 0);c=ChildState(cid,str(e.get('side') or '').upper(),'MAKER' if kind=='MAKER_INTENT' else 'TAKER',q,0.0,q,float(e.get('price') or 0))
            ch[cid]=c;car_rev+=1;vals.update(children=tuple(ch.values()),carrierRevision=car_rev)
        elif kind=='CANCEL_REQUESTED':
            cid=str(e['clientOrderId']);c=ch.get(cid)
            if c and not c.terminal and not c.cancelPending:
                ch[cid]=replace(c,cancelPending=True);car_rev+=1
            vals.update(children=tuple(ch.values()),carrierRevision=car_rev)
        elif kind=='FILL_DELTA':
            q=float(e.get('qty') or 0);side=str(e.get('side') or '').upper();role=str(e.get('role') or '').upper();cid=str(e.get('clientOrderId') or '')
            if q>EPS:
                if role=='MAKER':
                    vals['makerUp']=self.s.makerUp+(q if side=='UP' else 0);vals['makerDown']=self.s.makerDown+(q if side=='DOWN' else 0)
                elif role=='TAKER':
                    vals['takerUp']=self.s.takerUp+(q if side=='UP' else 0);vals['takerDown']=self.s.takerDown+(q if side=='DOWN' else 0)
                exec_rev+=1
                if cid in ch:
                    c=ch[cid];fq=min(c.requestedQty,c.confirmedFilledQty+q);ch[cid]=replace(c,confirmedFilledQty=fq,remainingQty=max(0.0,c.requestedQty-fq));car_rev+=1
            vals.update(executionRevision=exec_rev,children=tuple(ch.values()),carrierRevision=car_rev)
        elif kind in {'ORDER_FILLED','ORDER_CANCELED','ORDER_REJECTED','ORDER_EXPIRED','ORDER_FAILED'}:
            cid=str(e.get('clientOrderId') or '');c=ch.get(cid)
            if c and not c.terminal:
                ch[cid]=replace(c,terminal=True,cancelPending=False,remainingQty=0.0);car_rev+=1;exec_rev+=1
            vals.update(executionRevision=exec_rev,children=tuple(ch.values()),carrierRevision=car_rev)
        elif kind in {'MARKET_ROLLOVER','PROTECTION_PREEMPTION'}:
            # Audit event only in shadow core. Unresolved children are intentionally retained.
            pass
        else: raise ValueError(f'unknown kind {kind}')
        return self._publish(kind=kind,at_ms=at,**vals)

def action_basis(s:ReducerSnapshot)->ActionBasis:return ActionBasis(s.executionRevision,s.obligationRevision,s.carrierRevision,s.bookRevision)

def fence(b:ActionBasis,s:ReducerSnapshot)->str:
    if (b.executionRevision,b.obligationRevision,b.carrierRevision)!=(s.executionRevision,s.obligationRevision,s.carrierRevision):return 'HARD_INVALIDATE_RECONCILE'
    if b.bookRevision!=s.bookRevision:return 'BOOK_REVALIDATE'
    return 'VALID'

class AsyncReducerServiceV2:
    def __init__(self):
        self.reducer=ContinuousMakerReducerV2();self.q=queue.SimpleQueue();self._stop=object();self._thread=None;self._pub=self.reducer.snapshot();self._pub_lock=threading.Lock();self.latencyUs=[];self.processed=0
    def start(self):
        if self._thread and self._thread.is_alive():return
        self._thread=threading.Thread(target=self._run,name='r4-continuous-maker-reducer-v2',daemon=True);self._thread.start()
    def submit(self,event:dict[str,Any]):self.q.put((time.perf_counter_ns(),event))
    def snapshot(self):
        with self._pub_lock:return self._pub
    def _run(self):
        while True:
            item=self.q.get()
            if item is self._stop:return
            t0,e=item;s=self.reducer.apply(e);now=time.perf_counter_ns()
            with self._pub_lock:self._pub=s
            self.processed+=1;self.latencyUs.append((now-t0)/1000.0)
    def close(self):
        self.q.put(self._stop)
        if self._thread:self._thread.join(timeout=5)
