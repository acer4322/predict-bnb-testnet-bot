"""Two independent repair-only corrections, with the V24 demand kept pinned."""
import importlib.util
import math
from pathlib import Path

CONCURRENT = False
LEGAL_QUOTE = False
EPS = 1e-8


def load_base():
    path=Path(__file__).with_name('commitment_base.py')
    if not path.exists():path=Path(__file__).with_name('btc5m_commitment_repair_probe_v1.py')
    spec=importlib.util.spec_from_file_location('pinned_commitment_base',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


base=load_base()


def legal_quote(raw,ask,tick=.01,ticket=15.,enabled=True):
    minimum=round(math.ceil((1./ticket-EPS)/tick)*tick,10)
    price=raw;reason='UNCHANGED'
    if enabled and raw is not None and raw<minimum-EPS:
        if ask is not None and minimum<ask-EPS:
            price=minimum;reason='MINIMUM_LEGAL_PASSIVE_TICKET'
        else:reason='NO_LEGAL_PASSIVE_PRICE'
    return dict(raw_price=raw,price=price,ask=ask,minimum_legal_price=minimum,
                changed=price!=raw,reason=reason,enabled=enabled)


def decide(state,operations,raw_price,ask,active_confirmed,outstanding,crossing,tick=.01):
    quote=legal_quote(raw_price,ask,tick,enabled=LEGAL_QUOTE and active_confirmed)
    known={o['key'] for o in state['owners']}
    missing=[k for k in outstanding if k not in known]
    gate_owners=[] if CONCURRENT else outstanding
    row=base.decide(state,operations,quote['price'],ask,active_confirmed,gate_owners,crossing)
    row.update(outstanding=list(outstanding),missing_reserved_owners=missing,
               concurrent_repair=CONCURRENT,quote=quote)
    if active_confirmed and missing:
        row.update(eligible=False,reason='WAIT_FOR_OBSERVABLE_OWNER_RESERVATION')
    return row


class CommitmentRepairProbe(base.CommitmentRepairProbe):
    def __init__(self,snapshot):
        super().__init__(snapshot);self.maintenance_rows=[]

    def maintenance(self,frame,key,owner,raw_price,stale,threshold):
        if not LEGAL_QUOTE or key not in {s['key'] for s in self.submissions}:
            return raw_price,stale
        ask=((frame.get('quotes') or {}).get('DOWN') or {}).get('ask')
        quote=legal_quote(raw_price,ask,frame['world_profile']['tick'])
        revised=abs(float(owner.limit)-quote['price'])>threshold+1e-9
        self.maintenance_rows.append(dict(t=int(frame['t']),key=key,owner_state=owner.state,
            limit=float(owner.limit),quote=quote,threshold=threshold,old_stale=stale,new_stale=revised))
        return quote['price'],revised

    def apply(self,frame,producer,operations,validate,crossing):
        if not frame['start']<=frame['t']<frame['end']:return operations
        if not producer.demand.rows or producer.demand.rows[-1]['t']!=frame['t']:return operations
        ledger=frame['ledger'];active=producer.opportunity.submissions
        owner=ledger.carriers.get(active[0]['key']) if len(active)==1 else None
        confirmed=owner is not None and owner.state=='TERMINAL' and float(owner.filled)>EPS
        outstanding=[o['key'] for o in self.submissions if o['key'] not in ledger.carriers
                     or ledger.carriers[o['key']].state!='TERMINAL']
        state=self.snapshot(frame,ledger);raw=producer.demand.rows[-1]['eligibility']['price']
        ask=((frame.get('quotes') or {}).get('DOWN') or {}).get('ask')
        row=decide(state,operations,raw,ask,confirmed,outstanding,crossing,frame['world_profile']['tick'])
        row.update(t=int(frame['t']),state=state,original_operations=[dict(o) for o in operations],
                   gateway_state_id=frame['gateway_state_id'])
        price=row['price']
        if row['eligible']:
            count=len(state['owners'])+sum(o['kind']=='NEW' for o in operations)
            if count>=frame['world_profile']['max_live_owners']:
                row.update(eligible=False,reason='RESOURCE_OWNER_LIMIT')
            else:
                validate(frame['world_profile']['asset'],'PASSIVE',price,15.,quantity_step=frame['world_profile']['quantity_step'])
                assert abs(price/frame['world_profile']['tick']-round(price/frame['world_profile']['tick']))<EPS
        self.rows.append(row)
        if not row['eligible']:return operations
        if self.first is None:self.first=row
        index=frame['own_view']['n']+sum(o['kind']=='NEW' for o in operations)
        op=dict(kind='NEW',key=f'DOWN_{index}',parent_id=2,side='DOWN',route='PASSIVE',price=price,
                qty=15.,role='PASSIVE_CURRENT_COMMITMENT_REPAIR')
        producer.passive_births+=1;self.submissions.append(dict(t=int(frame['t']),**op))
        return [*operations,op]


def instrument(source,replace):
    source=base.instrument(source,replace)
    if LEGAL_QUOTE:
        marker='     self.demand.maintenance(f,k,s,prices[s],stale,surplus)'
        source=replace(source,marker,
            "     maintenance_price=prices[s]\n"
            "     if s=='DOWN':maintenance_price,stale=self.commitment_repair.maintenance(f,k,c,prices[s],stale,tick*(1.+softplus(w[11])))\n"
            "     self.demand.maintenance(f,k,s,maintenance_price,stale,surplus)")
    return source


def self_test(crossing):
    from copy import deepcopy
    global CONCURRENT,LEGAL_QUOTE
    saved=CONCURRENT,LEGAL_QUOTE
    try:
        s=dict(inv=dict(UP=100.,DOWN=80.),payoff=dict(UP=15.,DOWN=-5.),
            pending_qty=dict(UP=30.,DOWN=15.),pending_cash=dict(UP=27.,DOWN=1.05),
            owners=[dict(key='u',side='UP',state='SUBMITTED',qty=30.,limit=.90),
                    dict(key='d',side='DOWN',state='CANCEL_PENDING',qty=15.,limit=.07)])
        CONCURRENT=True;LEGAL_QUOTE=False
        r=decide(s,[],.07,.09,True,['d'],crossing)
        assert r['eligible'] and r['pending_qty']['DOWN']==15.
        assert abs(r['deterioration_debt']-13.05)<EPS
        assert not decide(s,[],.07,.09,True,['missing'],crossing)['eligible']
        c=deepcopy(s);c['pending_qty']['DOWN']=30.;c['pending_cash']['DOWN']=2.1
        c['owners'].append(dict(key='d2',side='DOWN',state='UNKNOWN',qty=15.,limit=.07))
        assert not decide(c,[],.07,.09,True,['d','d2'],crossing)['eligible'], 'All pending supply already covers this debt'
        # Cancellation never opens capacity before the canonical terminal release.
        assert not decide(c,[dict(kind='CANCEL',key='d2')],.07,.09,True,['d','d2'],crossing)['eligible']
        assert decide(s,[],.07,.09,True,['d'],crossing)['eligible']
        CONCURRENT=False
        assert not decide(s,[],.07,.09,True,['d'],crossing)['eligible']
        for raw,ask,price in ((.06,.09,.07),(.05,.08,.07),(.06,.07,.06),(.08,.10,.08),(None,.09,None),(0.,.09,.07),(-.01,.09,.07)):
            assert legal_quote(raw,ask)['price']==price
        assert legal_quote(.04,.06,.01,30.)['price']==.04, 'Floor comes from quantity and tick, not a hardcoded .07'
        LEGAL_QUOTE=True
        assert decide(s,[],.05,.09,True,[],crossing)['eligible']
        assert not decide(s,[],.05,.07,True,[],crossing)['eligible']
        assert decide(s,[],.05,.09,False,[],crossing)['price']==.05, 'Existing Active selection remains untouched'
        c=deepcopy(s);c['owners'][0]['limit']=.93
        assert not decide(c,[dict(kind='CANCEL',key='u')],.05,.09,True,[],crossing)['eligible']
        assert not decide(s,[dict(kind='NEW',key='new',side='UP',qty=15.,price=.93)],.05,.09,True,[],crossing)['eligible']
        from types import SimpleNamespace
        obj=CommitmentRepairProbe(None);obj.submissions=[dict(key='extra')]
        frame=dict(t=1,quotes=dict(DOWN=dict(ask=.09)),world_profile=dict(tick=.01))
        owner=SimpleNamespace(limit=.07,state='SUBMITTED')
        assert obj.maintenance(frame,'extra',owner,.05,True,.011)==(.07,False)
        assert obj.maintenance(frame,'original',owner,.05,True,.011)==(.05,True)
        frame['quotes']['DOWN']['ask']=.07
        assert obj.maintenance(frame,'extra',owner,.05,True,.011)==(.05,True)
        return dict(status='PASS',pending_supply_counted=True,missing_owner_blocks=True,release_only_terminal=True,
            fixed_ticket_legal_quote=True,current_ask_and_cross_checked=True,maintenance_uses_same_quote=True,active_selection_unchanged=True)
    finally:CONCURRENT,LEGAL_QUOTE=saved
