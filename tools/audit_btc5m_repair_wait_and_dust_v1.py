"""No-fit payment lineage, exact/sub-step completion, and wait-gate evaluation."""
import argparse
import json
from pathlib import Path
from aggregate_btc5m_causal_clock_smoke_v1 import PARITY_FIELDS,compact
from aggregate_btc5m_exposure_intent_ablation_v1 import get,STREAMS
from audit_btc5m_target_core_loop_topology_v1 import read,sha

ROOT=Path(__file__).resolve().parents[1];R=ROOT/'data/research';RET=R/'lan_worker_returns'
PACKAGE=ROOT/'.lan_worker_v1/repair_wait_2026085_20260913_v1'
STEM='BTC5M_REPAIR_WAIT_AND_DUST_V1_20260913'


def cohort(d,tr,selection,step):
    t=selection['t'];rid=selection['old_id'];initial=selection['initial_remaining']
    born={o['key']:row['t'] for row in tr['plans'] for o in row['operations'] if o['kind']=='NEW'}
    remain=initial;prev=t;area=0.;first=exact=substep=None;curve=[]
    lower=upper=0.;new_lower=new_upper=0.
    for e in d['atomic_responsibility_events']:
        if e['t']<=t:continue
        paid=sum(p['qty'] for p in e['payments'] if p['responsibility_id']==rid)
        area+=remain*(e['t']-prev)/1000.;prev=e['t'];remain=max(0.,remain-paid)
        if paid:
            if first is None:first=e['t']-t
            if substep is None and remain<step:substep=e['t']-t
            if exact is None and remain<=1e-9:exact=e['t']-t
            old_qty=sum(x['fill_increment'] for x in e['fill_rows'] if x['side']=='DOWN' and born[x['key']]<t)
            new_qty=sum(x['fill_increment'] for x in e['fill_rows'] if x['side']=='DOWN' and born[x['key']]>=t)
            lo=max(0.,paid-(e['fill_down']-old_qty));hi=min(paid,old_qty)
            nl=max(0.,paid-(e['fill_down']-new_qty));nh=min(paid,new_qty)
            lower+=lo;upper+=hi;new_lower+=nl;new_upper+=nh
            curve.append(dict(delay_ms=e['t']-t,paid=paid,remaining=remain,paid_fraction=1-remain/initial,
                preexisting_payment_bounds=[lo,hi],postcut_payment_bounds=[nl,nh]))
    end=1788758400000;assert prev<=end;area+=remain*(end-prev)/1000
    return dict(initial=initial,remaining=remain,first_payment_ms=first,exact_completion_ms=exact,
        substep_completion_ms=substep,quantity_step=step,debt_area_share_seconds=area,
        preexisting_payment_bounds=[lower,upper],postcut_payment_bounds=[new_lower,new_upper],curve=curve,
        exact_completion_at_eof=exact==end-t)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('stage',choices=('history','control','finish'));a=ap.parse_args()
    manifest=read(PACKAGE/'manifest.json');selection=manifest['selection']
    if a.stage=='history':
        out=dict(status='COMPLETE',analysis='Posthoc exact-versus-sub-step and service-generation audit, not strategy thresholds.',arms={})
        for tag in ('passive','active'):
            folder=RET/f'oracle-repair-2026085-{tag}-20260913-v1';d,tr=get(folder)
            out['arms'][tag]=dict(cohort=cohort(d,tr,selection,.01),source_sha256=sha(folder/'result.json'))
        (R/(STEM+'_HISTORY.json')).write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out));return
    old,ot=get(RET/'oracle-repair-2026085-up-baseline-20260913-v1')
    control,ct=get(RET/'repair-wait-2026085-control-20260913-v1')
    parity={k:old[k]==control[k] for k in PARITY_FIELDS};parity.update({k:ot[k]==ct[k] for k in STREAMS})
    assert all(parity.values()),parity
    out=dict(status='COMPLETE',control_parity=parity,oracle=True,runtime_eligible=False,arms={})
    tags=('control',) if a.stage=='control' else ('control','progress','existing')
    for tag in tags:
        folder=RET/f'repair-wait-2026085-{tag}-20260913-v1';d,tr=get(folder)
        assert d['clock_smoke']['manifest_sha256']==sha(PACKAGE/'manifest.json')
        assert d['oracle'] and not d['runtime_eligible'] and d['active_native_submits']==0
        start=next(e for e in tr['wait_events'] if e['event']=='START');assert start['t']==selection['t']
        step=start['quantity_step'];assert step==.01
        prefix={k:[r for r in tr[k] if r['t']<selection['t']]==[r for r in ct[k] if r['t']<selection['t']] for k in STREAMS};assert all(prefix.values())
        release=next((e for e in tr['wait_events'] if e['event']=='RELEASE'),None)
        stop=release['t'] if release else 1788758400000
        if tag!='control':
            forbidden=[o for p in tr['plans'] if selection['t']<=p['t']<stop for o in p['operations'] if o['kind']=='NEW' and o['side']=='DOWN']
            assert not forbidden,forbidden
        c=cohort(d,tr,selection,step)
        if tag=='progress':assert release and release['t']-selection['t']==c['first_payment_ms']
        out['arms'][tag]=dict(metrics=compact(d),cohort=c,gate_release=release,gate_pending_final=d['clock_smoke']['wait_pending_final'],
            blocked_loop_visits=len(tr['blocked']),prefix_parity=prefix,source_sha256=sha(folder/'result.json'))
    out['limitations']=['Exact and sub-step clocks are both descriptive; residual is never erased.',
        'EXISTING_ONLY is a knowingly insufficient diagnostic intervention, not a strategy candidate.',
        'Fixed oracle and numeric rules; realized UP additions remain endogenous after the fork.',
        'One consumed market/old lot; no generalized trigger or economic promotion.']
    suffix='_CONTROL.json' if a.stage=='control' else '_RESULT.json'
    (R/(STEM+suffix)).write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out))


if __name__=='__main__':main()
