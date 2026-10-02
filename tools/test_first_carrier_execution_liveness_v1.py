from __future__ import annotations
from tools.eth_repair_modular.first_carrier_execution_liveness import FirstCarrierExecutionLivenessPolicyV1, FirstCarrierLivenessContext

p=FirstCarrierExecutionLivenessPolicyV1()

def dec(**kw):
    base=dict(authorized_role='EXPAND',objective_id=7,terminal_confirmed=True,actual_filled=0.0,submitted_qty=3.0,active_fallback_already_attempted=False,seconds_left=240.0,live_ask=0.5)
    base.update(kw);return p.evaluate(FirstCarrierLivenessContext(**base))

cases=[]
def check(name,ok):
    cases.append((name,bool(ok)))

x=dec();check('terminal_zero_fill_allows_active',x.allow_active_fallback and not x.release_unmaterialized_builder and abs(x.active_qty-2.0)<1e-9)
check('non_expand_blocked',not dec(authorized_role='REPAIR').allow_active_fallback)
check('missing_objective_blocked',not dec(objective_id=None).allow_active_fallback)
check('partial_fill_blocks_fallback',not dec(actual_filled=0.1).allow_active_fallback)
check('live_carrier_blocks_fallback',not dec(terminal_confirmed=False).allow_active_fallback)
check('late_fence_blocks',not dec(seconds_left=180.0).allow_active_fallback)
check('invalid_ask_blocks',not dec(live_ask=None).allow_active_fallback)
check('subminimum_remainder_blocks',not dec(submitted_qty=1.0,live_ask=0.5).allow_active_fallback)
y=dec(active_fallback_already_attempted=True)
check('exhausted_active_releases_builder',y.release_unmaterialized_builder and not y.allow_active_fallback)
check('materialized_active_never_releases_builder',not dec(active_fallback_already_attempted=True,actual_filled=0.2).release_unmaterialized_builder)

failed=[n for n,ok in cases if not ok]
print({'policy':p.name,'cases':len(cases),'passed':sum(ok for _,ok in cases),'failed':failed})
raise SystemExit(1 if failed else 0)
