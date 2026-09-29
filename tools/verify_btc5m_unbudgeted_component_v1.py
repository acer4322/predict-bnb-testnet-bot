"""Nonnative checks for both-route cash-gate OFF and the existing mixed seam."""
import inspect
import json
from types import SimpleNamespace
import verify_btc5m_active_opportunity_component_v1 as previous
from prepare_btc5m_unbudgeted_opportunity_v1 import ROOT,R,PACKAGE,BASE,STEM,load,sha,once
from audit_btc5m_target_core_loop_topology_v1 import read


def main():
    source=inspect.getsource(previous.main)
    source=once(source,'from btc5m_active_repair_opportunity_v1 import ActiveOpportunity, self_test',
                "module=load('no_cash_component',PACKAGE/'active_opportunity.py');ActiveOpportunity=module.ActiveOpportunity;self_test=module.self_test")
    source=once(source,'money_gate=SimpleNamespace(retention=.5)','money_gate=SimpleNamespace(retention=0.)')
    source=source.replace('BTC5M_ACTIVE_REPAIR_OPPORTUNITY_V1_20260913_COMPONENT.json',STEM+'_COMPONENT.json')
    source=source.replace("'quantity/cash/payoff/depth bounds'", "'quantity/payoff/depth bounds with cash gate OFF'")
    ns=dict(previous.__dict__,PACKAGE=PACKAGE)
    exec(compile(source,'unbudgeted_component_bindings','exec'),ns)
    ns['main']()
    module=load('cash_off_component',PACKAGE/'cash_budget.py')
    class Base:
        def __init__(self,*args): pass
        def cap(self,*args):return 15.
    controller=module.make_capacity(Base)({},'PARALLEL_QUANTITY','AUTO_REPAIR',0.)
    draft=SimpleNamespace(grants={1:SimpleNamespace(side='UP'),2:SimpleNamespace(side='DOWN')},
        carriers={'u':SimpleNamespace(parent_id=1,filled=100.,payment=80.,fees=0.,qty=100.,limit=.8,state='TERMINAL'),
                  'd':SimpleNamespace(parent_id=2,filled=40.,payment=30.,fees=0.,qty=45.,limit=.75,state='CANCEL_PENDING')})
    frame=dict(t=1,world_profile=dict(quantity_step=.01))
    assert controller.cap(frame,'DOWN',.07,15.,draft)==15.
    assert controller.cap(frame,'UP',.9,15.,draft)==15.
    row=controller.budget_rows[0]
    assert row['available_cash'] is None and row['quantity_cap'] is None and not row['cash_budget_enabled']
    assert row['paid']['DOWN']>row['confirmed_up_gross']
    assert abs(row['pending_down_cash']-3.75)<1e-9
    # Baseline was already executed with Passive gate disabled, no second control job.
    tr=read(BASE/'clock_trace.json.gz')
    assert all(r['retention']==0. and r['admitted']==r['old_quantity_cap'] for r in tr['profit_budget_rows'])
    out=read(R/(STEM+'_COMPONENT.json'))
    out.update(explicit_cash_gate_off_both_routes=True,hidden_100percent_cap_rejected=True,
               pending_accounting_retained=True,baseline_disabled_rows=len(tr['profit_budget_rows']),
               baseline_native_replays_added=0,
               reuse_component_sha256=sha(ROOT/'tools/verify_btc5m_active_opportunity_component_v1.py'))
    (R/(STEM+'_COMPONENT.json')).write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='PASS',no_cash_cap=True,pending_retained=True,native_jobs=0)))


if __name__=='__main__':main()
