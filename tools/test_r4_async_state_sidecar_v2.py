from __future__ import annotations
import json,time,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.r4_async_state_sidecar_v2 import ContinuousMakerReducerV2,AsyncReducerServiceV2,action_basis,fence

def check(name,cond,detail,rows):rows.append({'name':name,'pass':bool(cond),'detail':detail})

def main():
    rows=[];r=ContinuousMakerReducerV2()
    s=r.apply({'source':'BOOK','bookId':1,'kind':'BOOK_UPDATE','atMs':1000,'upBid':.45,'downBid':.54,'upAsk':.46,'downAsk':.55});check('book_accept',s.bookRevision==1 and s.bookUpdateId==1,s.__dict__,rows)
    s0=s;r.apply({'source':'BOOK','bookId':1,'kind':'BOOK_UPDATE','atMs':1001,'upBid':.45,'downBid':.54,'upAsk':.46,'downAsk':.55});s=r.snapshot();check('duplicate_book_idempotent',s.ingestRevision==s0.ingestRevision and s.duplicateEvents==1,s.__dict__,rows)
    r.apply({'source':'BOOK','bookId':0,'kind':'BOOK_UPDATE','atMs':1002,'upBid':.40,'downBid':.59,'upAsk':.41,'downAsk':.60}) if False else None
    # backwards positive id after moving to id=2
    r.apply({'source':'BOOK','bookId':2,'kind':'BOOK_UPDATE','atMs':1003,'upBid':.46,'downBid':.53,'upAsk':.47,'downAsk':.54});before=r.snapshot();r.apply({'source':'BOOK','bookId':1,'kind':'BOOK_UPDATE','atMs':1004,'upBid':.1,'downBid':.1,'upAsk':.2,'downAsk':.2});s=r.snapshot();check('backward_book_quarantined',s.bookUpdateId==2 and s.bookRevision==before.bookRevision and s.backwardWatermarkEvents==1,s.__dict__,rows)
    r.apply({'source':'LOCAL','eventKey':'m1','kind':'MAKER_INTENT','atMs':1010,'clientOrderId':'m1','side':'DOWN','qty':10,'price':.52});s=r.snapshot();check('maker_intent_reserves',s.carrierRevision==1 and len(s.children)==1,s.__dict__,rows)
    basis=action_basis(s)
    r.apply({'source':'BOOK','bookId':3,'kind':'BOOK_UPDATE','atMs':1011,'upBid':.47,'downBid':.52,'upAsk':.48,'downAsk':.53});s=r.snapshot();check('book_change_requires_revalidate_only',fence(basis,s)=='BOOK_REVALIDATE',{'fence':fence(basis,s),'state':s.__dict__},rows)
    basis=action_basis(s)
    r.apply({'source':'ENGINE','engineSeq':1,'kind':'FILL_DELTA','atMs':1020,'clientOrderId':'x','role':'MAKER','side':'UP','qty':4});s=r.snapshot();check('fill_immediate_obligation',s.executionRevision==1 and s.obligationRevision==1 and s.exactGap==4 and s.weakSide=='DOWN',s.__dict__,rows);check('fill_hard_invalidates_old_action',fence(basis,s)=='HARD_INVALIDATE_RECONCILE',{'fence':fence(basis,s)},rows)
    before=s;r.apply({'source':'ENGINE','engineSeq':1,'kind':'FILL_DELTA','atMs':1021,'clientOrderId':'x','role':'MAKER','side':'UP','qty':4});s=r.snapshot();check('duplicate_engine_idempotent',s.executionRevision==before.executionRevision and s.duplicateEvents==2,s.__dict__,rows)
    r.apply({'source':'ENGINE','engineSeq':2,'kind':'FILL_DELTA','atMs':1030,'clientOrderId':'m1','role':'MAKER','side':'DOWN','qty':2});s=r.snapshot();check('carrier_progress_updates_immediately',s.executionRevision==2 and s.carrierRevision==2 and s.exactGap==2 and s.weakSide=='DOWN',s.__dict__,rows)
    r.apply({'source':'LOCAL','eventKey':'c1','kind':'CANCEL_REQUESTED','atMs':1031,'clientOrderId':'m1'});s=r.snapshot();c=[x for x in s.children if x.clientOrderId=='m1'][0];check('cancel_request_keeps_remaining',c.cancelPending and c.remainingQty==8,s.__dict__,rows)
    r.apply({'source':'ENGINE','engineSeq':3,'kind':'FILL_DELTA','atMs':1032,'clientOrderId':'m1','role':'MAKER','side':'DOWN','qty':1});s=r.snapshot();c=[x for x in s.children if x.clientOrderId=='m1'][0];check('fill_during_cancel_reconciles_before_release',c.cancelPending and c.remainingQty==7 and s.exactGap==1,s.__dict__,rows)
    r.apply({'source':'ENGINE','engineSeq':4,'kind':'ORDER_CANCELED','atMs':1033,'clientOrderId':'m1'});s=r.snapshot();c=[x for x in s.children if x.clientOrderId=='m1'][0];check('terminal_releases_only_after_engine_seq',c.terminal and not c.cancelPending and c.remainingQty==0,s.__dict__,rows)
    # weak side flip is immediate on next execution delta
    r.apply({'source':'ENGINE','engineSeq':5,'kind':'FILL_DELTA','atMs':1040,'clientOrderId':'x2','role':'MAKER','side':'DOWN','qty':3});s=r.snapshot();check('weak_side_flip_immediate',s.weakSide=='UP' and s.exactGap==2,s.__dict__,rows)
    # async parity with the same accepted event sequence
    evs=[
      {'source':'BOOK','bookId':1,'kind':'BOOK_UPDATE','atMs':1,'upBid':.4,'downBid':.59,'upAsk':.41,'downAsk':.60},
      {'source':'LOCAL','eventKey':'a','kind':'MAKER_INTENT','atMs':2,'clientOrderId':'a','side':'DOWN','qty':10,'price':.58},
      {'source':'ENGINE','engineSeq':1,'kind':'FILL_DELTA','atMs':3,'clientOrderId':'z','role':'MAKER','side':'UP','qty':6},
      {'source':'ENGINE','engineSeq':2,'kind':'FILL_DELTA','atMs':4,'clientOrderId':'a','role':'MAKER','side':'DOWN','qty':2},
      {'source':'BOOK','bookId':2,'kind':'BOOK_UPDATE','atMs':5,'upBid':.42,'downBid':.57,'upAsk':.43,'downAsk':.58},
    ]
    seq=ContinuousMakerReducerV2()
    for e in evs:seq.apply(e)
    svc=AsyncReducerServiceV2();svc.start()
    for e in evs:svc.submit(e)
    deadline=time.time()+2
    while svc.processed<len(evs) and time.time()<deadline:time.sleep(.001)
    async_s=svc.snapshot();svc.close();check('async_final_state_exact_parity',async_s==seq.snapshot(),{'sequential':seq.snapshot().__dict__,'async':async_s.__dict__},rows)
    out={'version':'R4_ASYNC_STATE_SIDECAR_V2_TEST','passed':sum(x['pass'] for x in rows),'total':len(rows),'checks':rows}
    print(json.dumps(out,indent=2,default=str));return 0 if out['passed']==out['total'] else 1
if __name__=='__main__':raise SystemExit(main())
