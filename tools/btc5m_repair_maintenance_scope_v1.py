"""V27: equalize DOWN Passive stale-price scope after the same Active receipt."""
MODE='RAW'


def decide(raw,ask,limit,threshold,is_extra,legal_quote,tick=.01):
    original=legal_quote(raw,ask,tick) if is_extra else dict(price=raw)
    quote=legal_quote(raw,ask,tick,enabled=MODE=='LEGAL')
    old=abs(limit-original['price'])>threshold+1e-9
    new=abs(limit-quote['price'])>threshold+1e-9
    return dict(mode=MODE,quote=quote,original_price=original['price'],original_stale=old,
        price=quote['price'],stale=new,changed_price=quote['price']!=original['price'],changed_stale=new!=old)


def make_probe(base):
    class MaintenanceScope(base.CommitmentRepairProbe):
        def __init__(self,snapshot):
            super().__init__(snapshot);self.scope_rows=[];self.coordinator=None

        def maintenance(self,frame,key,owner,raw_price,stale,threshold):
            submits=self.coordinator.submissions if self.coordinator is not None else []
            active=frame['ledger'].carriers.get(submits[0]['key']) if len(submits)==1 else None
            ready=active is not None and active.state=='TERMINAL' and float(active.filled)>1e-8
            if not ready or owner.route!='PASSIVE':
                return super().maintenance(frame,key,owner,raw_price,stale,threshold)
            is_extra=key in {s['key'] for s in self.submissions}
            ask=((frame.get('quotes') or {}).get('DOWN') or {}).get('ask')
            row=decide(raw_price,ask,float(owner.limit),threshold,is_extra,base.legal_quote,frame['world_profile']['tick'])
            row.update(t=int(frame['t']),key=key,limit=float(owner.limit),threshold=threshold,
                is_extra=is_extra,owner_route=owner.route,owner_state=owner.state,active_key=submits[0]['key'],
                active_state=active.state,active_filled=float(active.filled),raw_stale=stale)
            self.scope_rows.append(row)
            return row['price'],row['stale']
    return MaintenanceScope


def instrument(source,replace):
    marker=';self.coordination=_CoordinationProbe(_OWN_SNAPSHOT)'
    return replace(source,marker,marker+';self.commitment_repair.coordinator=self.coordination')


def self_test(base):
    from types import SimpleNamespace
    global MODE
    saved=MODE
    try:
        for MODE in ('RAW','LEGAL'):
            a=decide(.06,.08,.08,.01474,False,base.legal_quote)
            b=decide(.06,.08,.08,.01474,True,base.legal_quote)
            assert (a['price'],a['stale'])==(b['price'],b['stale'])
            assert a['stale']==(MODE=='RAW')
            assert decide(.05,.07,.08,.01474,True,base.legal_quote)['stale']
            assert decide(.08,.10,.08,.01474,True,base.legal_quote)['price']==.08
            obj=make_probe(base)(None);obj.submissions=[dict(key='extra')]
            obj.coordinator=SimpleNamespace(submissions=[dict(key='active')])
            active=SimpleNamespace(key='active',state='SUBMITTED',filled=130.45)
            owner=SimpleNamespace(key='extra',state='CANCEL_PENDING',limit=.08,route='PASSIVE')
            frame=dict(t=1,ledger=SimpleNamespace(carriers=dict(active=active)),quotes=dict(DOWN=dict(ask=.08)),world_profile=dict(tick=.01))
            assert obj.maintenance(frame,'extra',owner,.06,True,.01474)==(.07,False)
            assert not obj.scope_rows, 'A receipt without canonical terminal must not activate the change'
            active.state='UNKNOWN';obj.maintenance(frame,'extra',owner,.06,True,.01474);assert not obj.scope_rows
            active.state='TERMINAL';p,s=obj.maintenance(frame,'extra',owner,.06,True,.01474)
            assert (p,s)==((.06,True) if MODE=='RAW' else (.07,False))
            assert owner.state=='CANCEL_PENDING', 'Maintenance must not release a pending owner'
            n=len(obj.scope_rows);owner.route='ACTIVE';obj.maintenance(frame,'other',owner,.06,True,.01474)
            assert len(obj.scope_rows)==n, 'Active owner never uses Passive quote maintenance'
        return dict(status='PASS',same_price_for_all_passive_roles=True,canonical_terminal_gate=True,
            active_excluded=True,no_owner_mutation=True,no_new_order_rule_change=True)
    finally:MODE=saved
