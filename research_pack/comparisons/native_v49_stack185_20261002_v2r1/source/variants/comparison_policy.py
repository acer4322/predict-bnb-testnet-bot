"""Frozen comparison intents, independent of engine and settlement labels.

This producer replaces alpha decisions, not the native owner/receipt consumer.
The 12 + 2k decision grid belongs to section B. Section A is unchanged lab code.
"""
from collections import Counter
import math

RV_THRESHOLD = 3.1834e-05
STRATEGIES = ('FAV_TAKER','UNDER_TAKER','V1_SWITCH','V2_THROTTLE')

class ComparisonPolicy:
    provenance = 'NATIVE_ENGINE_COMPARISON_SPEC_20261002_FROZEN_CAUSAL_INTENTS'

    def __init__(self, strategy, rv_5m=None):
        assert strategy in STRATEGIES
        self.strategy = strategy
        if strategy in ('V1_SWITCH','V2_THROTTLE'):
            assert rv_5m is not None and math.isfinite(rv_5m) and rv_5m >= 0, 'required pre-window rv_5m'
            self.effective = 'UNDER_TAKER' if rv_5m >= RV_THRESHOLD else ('FAV_TAKER' if strategy=='V1_SWITCH' else 'NO_TRADE')
        else:
            self.effective = strategy
        self.policy_id = 'COMPARISON_V49_' + strategy
        self.continuation_id = self.policy_id + ':FROZEN_RULES'
        self.fav = None
        self.frozen = False
        self.last_decision_ms = None
        self.counts = Counter()
        self.decisions = []

    def intent(self, *, t, start, end, bid, ask, inventory, pending):
        assert t >= start and end-start == 300000
        if not (math.isfinite(bid) and math.isfinite(ask) and 0 < bid < ask < 1):
            self.counts['INVALID_BOOK'] += 1
            return None
        age = t-start
        mid = (bid+ask)/2
        if age >= 12000 and self.fav is None:
            self.fav = 'UP' if mid >= .5 else 'DOWN'
        fmid = mid if self.fav == 'UP' else 1-mid
        if self.effective == 'FAV_TAKER' and self.fav is not None and age > 12000 and fmid <= .4:
            self.frozen = True
        if age < 12000 or (age-12000)%2000 != 0 or self.last_decision_ms == t:
            return None
        self.last_decision_ms = t
        self.counts['DECISION_CLOCKS'] += 1
        reason = None
        owned = sum(inventory.values()) + sum(pending.values())
        current = 'UP' if mid >= .5 else 'DOWN'
        current_mid = mid if current=='UP' else 1-mid
        side = None
        if self.effective=='NO_TRADE': reason='V2_LOW_RV_NO_TRADE'
        elif owned >= 300: reason='OWNED_GE_300'
        elif self.effective=='FAV_TAKER':
            if self.frozen: reason='FAV_PERMANENT_STOP'
            elif age >= 270000: reason='FAV_STOP270'
            elif not .55 <= current_mid <= .70: reason='FAV_MID_OUTSIDE_BAND'
            else: side=current
        else:
            if age >= 290000: reason='UNDER_STOP290'
            elif current_mid < .75: reason='UNDER_MID_BELOW_THRESHOLD'
            else: side='DOWN' if current=='UP' else 'UP'
        row={'t':t,'seconds':age/1000,'initial_fav':self.fav,'up_mid':mid,'owned':owned,'effective_strategy':self.effective,'reason':reason}
        if side is not None:
            row.update(side=side,price=round(ask if side=='UP' else 1-bid,10),qty=15.,route='ACTIVE')
            self.counts['INTENTS'] += 1
        else:
            self.counts[reason] += 1
        self.decisions.append(row)
        return row if side is not None else None

def self_test():
    def frame(t,bid=.59,ask=.61,inventory=None,pending=None):
        return dict(t=t,start=0,end=300000,bid=bid,ask=ask,inventory=inventory or {'UP':0.,'DOWN':0.},pending=pending or {'UP':0.,'DOWN':0.})
    p=ComparisonPolicy('FAV_TAKER')
    assert p.intent(**frame(10000)) is None
    assert p.intent(**frame(12000))['side']=='UP'
    assert p.intent(**frame(12000)) is None
    assert p.intent(**frame(14000,.39,.41)) is None and p.frozen
    assert p.intent(**frame(16000)) is None
    p=ComparisonPolicy('FAV_TAKER')
    assert p.intent(**frame(270000)) is None
    p=ComparisonPolicy('UNDER_TAKER')
    assert p.intent(**frame(10000,.79,.81)) is None
    assert p.intent(**frame(12000,.79,.81))['side']=='DOWN'
    assert p.intent(**frame(14000,.19,.21))['side']=='UP'
    assert p.intent(**frame(290000,.79,.81)) is None
    p=ComparisonPolicy('UNDER_TAKER')
    assert p.intent(**frame(12000,.79,.81,inventory={'UP':290.,'DOWN':0.},pending={'UP':10.,'DOWN':0.})) is None
    assert ComparisonPolicy('V1_SWITCH',RV_THRESHOLD).effective=='UNDER_TAKER'
    assert ComparisonPolicy('V1_SWITCH',0).effective=='FAV_TAKER'
    assert ComparisonPolicy('V2_THROTTLE',0).effective=='NO_TRADE'
    try: ComparisonPolicy('V1_SWITCH')
    except AssertionError: pass
    else: raise AssertionError('Missing rv must not silently select an arm')
    return {'status':'PASS','engine_executed':0,'fits':0,'coverage':['decision_grid','permanent_flip_stop','270/290_exclusive','side_own_price','pending_in_owned_cap','switch_boundary','missing_rv_rejected']}

if __name__=='__main__':
    import json
    print(json.dumps(self_test()))
