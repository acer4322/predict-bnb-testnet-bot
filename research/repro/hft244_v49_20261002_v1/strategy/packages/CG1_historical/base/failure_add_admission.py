from __future__ import annotations
from roles_runtime import roles
EPS=1e-8
FEATURES=['gap_frac','repair_roi','recovery','risk_product','recent5','recent10','belief_avg_price','repair_avg_price','belief_taker_frac20','repair_taker_frac20']
MEAN=[-0.004423084003900535,0.02876529705899295,0.7966370760364816,0.04291699167204125,0.5266163267680738,0.52714166306069,0.43952710160418695,0.5262046071374797,0.29726477230836573,0.28074377771800124]
SCALE=[0.2338511112540464,0.2305075272255614,0.31661810652010275,0.12349179259826108,0.37925128263491537,0.3101097907975143,0.12645234599866917,0.11996493840955466,0.27153655039666,0.28585039315782285]
COEF=[0.03005458070099116,0.061433962015588504,0.10011166342193657,0.052246716068613204,-0.31126757775630026,0.014327873829424501,0.07238248105665222,0.06566956104851299,-0.015731672457035436,-0.028146175004841362]
INTERCEPT=-0.006182478811119101

def opening_relative_confidence(opening_oriented,current_oriented,pair_sum):
    deterioration=max(0.,min(1.,(float(opening_oriented)-float(current_oriented))/2.))
    pair_damage=max(0.,min(1.,float(pair_sum)-1.))
    pressure=(deterioration*pair_damage)**0.5 if deterioration>0. and pair_damage>0. else 0.
    return max(0.,min(1.,1.-pressure)),deterioration,pair_damage,pressure

class FailureAddAdmission:
    """Post-opening continuous directional confidence.

    Confidence starts at 1 and is monotone non-increasing in this V11 smoke.
    Strict-past negative ADD-authority evidence reduces confidence only to the
    degree that confirmed Repair serviceability is below natural parity.
    Repair fills never restore directional confidence.  Strong ADD placement
    is multiplied by confidence; weak-side Repair is untouched.
    """
    def __init__(self):
        self._seen_terminal=set();self._history=[];self._min_weak_payoff=0.
        self.scale=1.;self.confidence=1.;self.pred=0.;self.rows=[]
        self.serviceability=1.;self.serviceability_seen=False
        self.authority_balance=0.;self.confirmed_updates=0;self.current_t=None;self._withheld_by_t={}
        self.repair_dominant=False;self.repair_episode_qty=0.;self.repair_episode_id=0
        self.post_opening=False;self.add_credit=0.;self._ticket_decisions={};self.admission_rows=[]
        self.opening_oriented_public_score=None;self.micro_confidence_mode='V49_NATIVE_FAILURE_SHADOW_V1';self.formation_pressure_baseline=None;self.confidence_recovery_count=0
    def _observe_terminal(self,frame,ledger):
        new=[]
        for key,c in ledger.carriers.items():
            if key in self._seen_terminal or c.state!='TERMINAL':continue
            self._seen_terminal.add(key);q=float(c.filled)
            if q<=EPS:continue
            side=ledger.grants[c.parent_id].side;cash=float(c.payment)+float(c.fees)
            rec=dict(key=key,t=int(frame['t']),side=side,qty=q,cash=cash,price=(cash/q if cash>EPS else float(c.limit)),role=('TAKER' if c.route=='ACTIVE' else 'MAKER'))
            self._history.append(rec);new.append(rec)
        return new
    def observe(self,frame,producer,ledger):
        self.current_t=int(frame['t']);self._withheld_by_t[self.current_t]=0.0;self._ticket_decisions={}
        new=self._observe_terminal(frame,ledger);strong=roles.strong;weak=roles.weak;inv=frame['own_view']['inv'];cost=float(frame['own_view']['cost'])
        side_cost={'UP':0.,'DOWN':0.}
        for c in ledger.carriers.values():side_cost[ledger.grants[c.parent_id].side]+=float(c.payment)+float(c.fees)
        sq=float(inv[strong]);rq=float(inv[weak]);gross=sq+rq;rp=rq-cost;self._min_weak_payoff=min(self._min_weak_payoff,rp)
        recovery=1. if self._min_weak_payoff>=-EPS else max(0.,min(1.,(rp-self._min_weak_payoff)/(-self._min_weak_payoff)))
        gap=(sq-rq)/gross if gross>EPS else 0.
        def share(n):
            rr=self._history[-n:];den=sum(x['qty'] for x in rr);return sum(x['qty'] for x in rr if x['side']==strong)/den if den>EPS else .5
        def taker(n,sidev):
            rr=self._history[-n:];den=sum(x['qty'] for x in rr if x['side']==sidev);return sum(x['qty'] for x in rr if x['side']==sidev and x['role']=='TAKER')/den if den>EPS else 0.
        z=dict(gap_frac=gap,repair_roi=(rp/cost if cost>EPS else 0.),recovery=recovery,risk_product=max(0.,gap)*(1-recovery),recent5=share(5),recent10=share(10),belief_avg_price=(side_cost[strong]/sq if sq>EPS else 0.),repair_avg_price=(side_cost[weak]/rq if rq>EPS else 0.),belief_taker_frac20=taker(20,strong),repair_taker_frac20=taker(20,weak))
        vals=[z[f] for f in FEATURES];pred=INTERCEPT+sum(c*((x-m)/s) for c,x,m,s in zip(COEF,vals,MEAN,SCALE));pred=max(-1.,min(1.,pred))
        anchor=getattr(producer.addition_growth,'anchor',None);armed=anchor is not None and int(anchor.get('t',-1))!=int(frame['t']);self.post_opening=armed
        weak_new=[x for x in new if x['side']==weak]
        strong_new=[x for x in new if x['side']==strong]
        overrun_keys={x.get('key') for x in getattr(getattr(producer,'general_finite_active',None),'overrun_submissions',[])}
        budget_weak_new=[x for x in weak_new if x.get('key') not in overrun_keys]
        # Keep the V10 guarded over-repair episode accounting.  Overrun fills do
        # not recursively create more overrun budget.
        if strong_new:
            self.repair_episode_qty=0.;self.repair_episode_id+=1
        if budget_weak_new:
            self.repair_episode_qty+=sum(float(x['qty']) for x in budget_weak_new)
        if weak_new:
            q=sum(x['qty'] for x in weak_new);cash=sum(x['cash'] for x in weak_new)
            if cash>EPS:
                self.serviceability=max(0.,(q-cash)/cash);self.serviceability_seen=True
        if armed and new:
            self.authority_balance+=pred;self.confirmed_updates+=1
        # Micro-world candidate confidence formula. Structural exploration only;
        # no fitted weights, no fixed time/count threshold. Confidence stores the
        # historical worst observed state and never compounds repeated frames.
        raw_signal=getattr(roles,'public_signal',{}) or {}
        raw_score=raw_signal.get('score')
        sign=1. if strong=='UP' else -1.
        current_oriented=(sign*float(raw_score)) if raw_score is not None else None
        if self.opening_oriented_public_score is None and current_oriented is not None:
            self.opening_oriented_public_score=current_oriented
        pair_sum=float(z['belief_avg_price'])+float(z['repair_avg_price'])
        pair_damage=max(0.,min(1.,pair_sum-1.))
        public_deterioration=0.
        if self.opening_oriented_public_score is not None and current_oriented is not None:
            public_deterioration=max(0.,min(1.,(float(self.opening_oriented_public_score)-float(current_oriented))/2.))
        repair_weakness=max(0.,1.-min(1.,float(self.serviceability))) if self.serviceability_seen else 0.
        gap_positive=max(0.,min(1.,float(z['gap_frac'])))
        recovery_fail=max(0.,min(1.,1.-float(z['recovery'])))
        authority_withdrawal=max(0.,min(1.,-float(pred)))
        raw_pressure=(repair_weakness*recovery_fail)**0.5 if repair_weakness>0. and recovery_fail>0. else 0.
        if armed and self.formation_pressure_baseline is None:
            self.formation_pressure_baseline=max(0.,min(1.,raw_pressure))
        baseline=float(self.formation_pressure_baseline or 0.)
        excess=max(0.,raw_pressure-baseline) if armed and self.formation_pressure_baseline is not None else 0.
        directional_pressure=max(0.,min(1.,excess/max(raw_pressure,EPS))) if raw_pressure>EPS else 0.
        external_corroboration=(public_deterioration*pair_damage)**0.5 if public_deterioration>0. and pair_damage>0. else 0.
        failure_corroboration=max(authority_withdrawal,external_corroboration)
        corroborated_pressure=(directional_pressure*failure_corroboration)**0.5 if directional_pressure>0. and failure_corroboration>0. else 0.
        state_conf=max(0.,min(1.,1.-corroborated_pressure))
        previous_confidence=self.confidence
        if armed and new:
            self.confidence=state_conf
            if self.confidence>previous_confidence+EPS:self.confidence_recovery_count+=1
        self.scale=self.confidence if armed else 1.
        self.repair_dominant=bool(armed and self.confidence<1.-EPS)
        self.pred=pred
        self.rows.append(dict(t=int(frame['t']),index=int(frame['index']),strong=strong,features=z,predicted_delta=pred,authority_balance=self.authority_balance,confirmed_updates=self.confirmed_updates,serviceability=self.serviceability,serviceability_seen=self.serviceability_seen,opening_oriented_public_score=self.opening_oriented_public_score,current_oriented_public_score=current_oriented,public_deterioration=public_deterioration,pair_sum=pair_sum,pair_damage=pair_damage,repair_weakness=repair_weakness,gap_positive=gap_positive,recovery_fail=recovery_fail,authority_withdrawal=authority_withdrawal,raw_directional_pressure=raw_pressure,formation_pressure_baseline=self.formation_pressure_baseline,formation_pressure_excess=excess,directional_pressure=directional_pressure,external_corroboration=external_corroboration,failure_corroboration=failure_corroboration,corroborated_pressure=corroborated_pressure,state_confidence=state_conf,confidence_recovery_count=self.confidence_recovery_count,micro_confidence_mode=self.micro_confidence_mode,confidence_before=previous_confidence,confidence=self.confidence,scale=self.scale,armed=armed,shrink=bool(self.scale<1.-EPS),repair_dominant=self.repair_dominant,repair_episode_qty=self.repair_episode_qty,repair_episode_id=self.repair_episode_id,new_terminal_count=len(new),new_repair_count=len(weak_new),new_strong_count=len(strong_new),withheld_add_authority=0.0))
        return self.scale
    def gate_deficit(self,side,deficit):
        return max(0.,float(deficit))
    def admit_ticket(self,side,ticket,token):
        # Pure V49 shadow: observe failure state but never modify a ticket.
        t=max(0.,float(ticket)); key=(int(self.current_t or -1),str(token),str(side))
        if key in self._ticket_decisions:return self._ticket_decisions[key]
        self._ticket_decisions[key]=t
        self.admission_rows.append(dict(kind='SHADOW_ONLY',t=int(self.current_t or -1),token=str(token),side=side,ticket=t,confidence=self.confidence,post_opening=self.post_opening,allowed=t,withheld=0.0,credit_after=self.add_credit))
        return t
    def transfer_budget(self,t):
        return max(0.,float(self._withheld_by_t.get(int(t),0.0)))
    def repair_overrun_budget(self):
        return (int(self.repair_episode_id),max(0.,float(self.repair_episode_qty)))

def self_test():
    g=FailureAddAdmission();roles.configure('KNOWN_FINAL_DIRECTION','UP')
    g.current_t=1;g.post_opening=False;g.confidence=0.0
    assert abs(g.admit_ticket('UP',15,'open')-15)<EPS
    g.current_t=2;g.post_opening=True;g._ticket_decisions={}
    assert g.admit_ticket('DOWN',15,'repair')==15
    g.current_t=3;g._ticket_decisions={};g.confidence=1.0;g.add_credit=0.
    assert g.admit_ticket('UP',15,'full')==15
    g.current_t=4;g._ticket_decisions={};g.confidence=0.0;g.add_credit=0.
    assert g.admit_ticket('UP',15,'shadow')==15.
    return dict(status='PASS',shadow_only=True,opening_unscaled=True,repair_never_scaled=True,passive15_preserved=True,no_time_rule=True)




def instrument(source,replace):
    source=replace(source,'self.atomic=AtomicResponsibilityLedger();self.fill_seen={};','self.atomic=AtomicResponsibilityLedger();self.failure_add_admission=_FailureAddAdmission();self.fill_seen={};')
    source=replace(source,"self.calls+=1;ops=[];v=f['own_view'];ledger=f['ledger'];live=","self.calls+=1;ops=[];v=f['own_view'];ledger=f['ledger'];self.failure_add_admission.observe(f,self,ledger);live=")
    old="elif fresh_gap[ss]>1e-12:\n       raw=min(float(tickets[ss]),float(fresh_gap[ss]));kind='FRESH'\n      if raw<=1e-12:continue\n      price=prices[ss];ask=passive_ask(f['book'],ss)\n      if not tick<=price<1 or ask is None or price>=ask-1e-10:continue\n      qty=round(math.floor((raw+1e-10)/step)*step,8)"
    new="elif fresh_gap[ss]>1e-12:\n       raw=min(float(tickets[ss]),float(fresh_gap[ss]));kind='FRESH'\n      if raw<=1e-12:continue\n      price=prices[ss];ask=passive_ask(f['book'],ss)\n      if not tick<=price<1 or ask is None or price>=ask-1e-10:continue\n      if kind=='FRESH':raw=min(raw,self.failure_add_admission.admit_ticket(ss,float(tickets[ss]),'FRESH_CAPACITY_FRONTIER'))\n      if raw<=1e-12:continue\n      qty=round(math.floor((raw+1e-10)/step)*step,8)"
    source=replace(source,old,new)
    old="price=prices[s];ask=passive_ask(f['book'],s)\n     if not tick<=price<1 or ask is None or price>=ask-1e-10:continue\n     qty=max(0.,min(tickets[s],service_deficit));qty=round(math.floor((qty+1e-10)/step)*step,8)"
    new="price=prices[s];ask=passive_ask(f['book'],s)\n     if not tick<=price<1 or ask is None or price>=ask-1e-10:continue\n     allowed_ticket=self.failure_add_admission.admit_ticket(s,float(tickets[s]),'SERVICE_DEFICIT')\n     qty=max(0.,min(allowed_ticket,service_deficit));qty=round(math.floor((qty+1e-10)/step)*step,8)"
    source=replace(source,old,new)
    old="for s in sorted(pid,key=lambda z:(-deficit[z]*mid[z],z)):\n      if s in existing or deficit[s]<=0 or slots<=0:continue\n      price=prices[s];ask=passive_ask(f['book'],s)\n      if not tick<=price<1 or ask is None or price>=ask-1e-10:continue\n      qty=max(0.,min(tickets[s],deficit[s]));qty=round(math.floor((qty+1e-10)/step)*step,8)"
    new="for s in sorted(pid,key=lambda z:(-deficit[z]*mid[z],z)):\n      if s in existing or deficit[s]<=0 or slots<=0:continue\n      price=prices[s];ask=passive_ask(f['book'],s)\n      if not tick<=price<1 or ask is None or price>=ask-1e-10:continue\n      allowed_ticket=self.failure_add_admission.admit_ticket(s,float(tickets[s]),'PASSIVE_CONTINUATION')\n      qty=max(0.,min(allowed_ticket,deficit[s]));qty=round(math.floor((qty+1e-10)/step)*step,8)"
    source=replace(source,old,new)
    return source
