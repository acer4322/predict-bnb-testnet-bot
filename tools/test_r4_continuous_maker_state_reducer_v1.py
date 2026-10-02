from __future__ import annotations
import json
from dataclasses import dataclass,field
EPS=1e-9
@dataclass
class Child:
    oid:str;side:str;qty:float;filled:float=0.;cancel_pending:bool=False;terminal:bool=False
    @property
    def rem(self):return max(0.,self.qty-self.filled)
@dataclass
class State:
    maker_up:float=0.;maker_down:float=0.;taker_up:float=0.;taker_down:float=0.
    execution_revision:int=0;book_revision:int=0;obligation_revision:int=0;carrier_revision:int=0
    children:dict[str,Child]=field(default_factory=dict)
    up_bid:float|None=None;down_bid:float|None=None
    def net(self):return self.maker_up+self.taker_up-self.maker_down-self.taker_down
    def weak(self):
        n=self.net();return 'DOWN' if n>EPS else 'UP' if n<-EPS else None
    def gap(self):return abs(self.net())
    def reserve(self,side):return sum(c.rem for c in self.children.values() if c.side==side and not c.terminal and c.rem>EPS)
    def apply(self,e):
        typ=e['type']; before=(self.weak(),self.gap())
        if typ=='BOOK':
            self.up_bid=e.get('upBid',self.up_bid);self.down_bid=e.get('downBid',self.down_bid);self.book_revision+=1
        elif typ=='PLACE':
            self.children[e['oid']]=Child(e['oid'],e['side'],float(e['qty']));self.carrier_revision+=1
        elif typ=='CANCEL_REQUEST':
            c=self.children[e['oid']];c.cancel_pending=True;self.carrier_revision+=1
        elif typ=='FILL':
            q=float(e['qty']);side=e['side'];role=e['role'];oid=e.get('oid')
            if role=='MAKER':
                if side=='UP':self.maker_up+=q
                else:self.maker_down+=q
                if oid in self.children:self.children[oid].filled+=q
            else:
                if side=='UP':self.taker_up+=q
                else:self.taker_down+=q
            self.execution_revision+=1
            if oid in self.children:self.carrier_revision+=1
        elif typ=='TERMINAL':
            c=self.children[e['oid']];c.terminal=True;c.cancel_pending=False;self.execution_revision+=1;self.carrier_revision+=1
        after=(self.weak(),self.gap())
        if after!=before and typ in {'FILL'}:self.obligation_revision+=1
    def snap(self):
        return {'executionRevision':self.execution_revision,'bookRevision':self.book_revision,'obligationRevision':self.obligation_revision,'carrierRevision':self.carrier_revision,'net':self.net(),'weakSide':self.weak(),'gap':self.gap(),'reserveUp':self.reserve('UP'),'reserveDown':self.reserve('DOWN')}
def main():
    s=State();checks=[]
    def ck(name,cond,detail=None):checks.append({'name':name,'pass':bool(cond),'detail':detail})
    s.apply({'type':'BOOK','upBid':.45,'downBid':.54});ck('book_revision_only',s.book_revision==1 and s.execution_revision==0 and s.obligation_revision==0,s.snap())
    s.apply({'type':'PLACE','oid':'D1','side':'DOWN','qty':10});ck('place_reserves_without_inventory',s.reserve('DOWN')==10 and s.gap()==0,s.snap())
    s.apply({'type':'FILL','role':'MAKER','side':'UP','qty':4});ck('fill_updates_obligation_immediately',s.weak()=='DOWN' and abs(s.gap()-4)<EPS and s.execution_revision==1 and s.obligation_revision==1,s.snap())
    rev1=s.obligation_revision;s.apply({'type':'FILL','role':'MAKER','side':'UP','qty':3});ck('same_ms_style_second_fill_not_merged',s.obligation_revision==rev1+1 and abs(s.gap()-7)<EPS,s.snap())
    action={'basedOnExecutionRevision':s.execution_revision,'basedOnObligationRevision':s.obligation_revision,'basedOnCarrierRevision':s.carrier_revision}
    s.apply({'type':'CANCEL_REQUEST','oid':'D1'});ck('cancel_request_keeps_ownership',s.reserve('DOWN')==10 and s.children['D1'].cancel_pending,s.snap())
    s.apply({'type':'FILL','role':'MAKER','side':'DOWN','qty':5,'oid':'D1'});ck('fill_during_cancel_updates_remaining_and_gap',s.reserve('DOWN')==5 and abs(s.gap()-2)<EPS and s.children['D1'].cancel_pending,s.snap())
    ck('stale_action_invalidated',action['basedOnExecutionRevision']!=s.execution_revision or action['basedOnObligationRevision']!=s.obligation_revision or action['basedOnCarrierRevision']!=s.carrier_revision,{'action':action,'now':s.snap()})
    s.apply({'type':'FILL','role':'MAKER','side':'DOWN','qty':4,'oid':'D1'});ck('weak_side_flip_immediate',s.weak()=='UP' and abs(s.gap()-2)<EPS,s.snap())
    pre=s.reserve('DOWN');s.apply({'type':'TERMINAL','oid':'D1'});ck('terminal_releases_remaining_ownership',pre>0 and s.reserve('DOWN')==0,s.snap())
    s2=State();s2.apply({'type':'FILL','role':'MAKER','side':'UP','qty':10});s2.apply({'type':'FILL','role':'MAKER','side':'DOWN','qty':10});ck('obligation_zero_is_revisioned',s2.obligation_revision==2 and s2.weak() is None and abs(s2.gap())<EPS,s2.snap())
    out={'version':'R4_CONTINUOUS_MAKER_STATE_REDUCER_V1_TEST','passed':sum(x['pass'] for x in checks),'total':len(checks),'checks':checks}
    print(json.dumps(out,indent=2));raise SystemExit(0 if out['passed']==out['total'] else 1)
if __name__=='__main__':main()
