"""No-fit oracle control, strict-prefix fork selection, and responsibility audit."""
import argparse
import json
from pathlib import Path
from aggregate_btc5m_causal_clock_smoke_v1 import PARITY_FIELDS, compact
from aggregate_btc5m_exposure_intent_ablation_v1 import get, STREAMS
from audit_btc5m_target_core_loop_topology_v1 import Ledger, topology, target_rows, own_rows, batches, orientation, read, sha

ROOT = Path(__file__).resolve().parents[1]; R = ROOT/'data/research'; RET = R/'lan_worker_returns'
STEM = 'BTC5M_ORACLE_REPAIR_PANEL_V1_20260913'
PACKAGE = ROOT/'.lan_worker_v1/oracle_repair_2026085_20260913_v1'


def job(tag): return RET/('oracle-repair-2026085-'+tag+'-20260913-v1')


def valid(tag):
    d, tr = get(job(tag))
    assert d['oracle'] and d['target_direction_input'] and not d['runtime_eligible']
    assert d['target_runtime_access'] and not d['target_scoring_only']
    assert d['clock_smoke']['manifest_sha256'] == sha(PACKAGE/'manifest.json')
    assert d['clock_smoke']['runtime_clock_total_frames'] == 1487
    for row in tr['intent']:
        desired = row['desired']; s = 1 if d['oracle_direction'] == 'ORACLE_UP' else -1
        assert row['applied_exposure'] == s*abs(row['legacy_exposure'])
        assert abs((desired['UP']-desired['DOWN'])/sum(desired.values())-row['applied_exposure']) < 1e-12
    return d, tr


def parity(left, lt, right, rt):
    p = {k:left[k]==right[k] for k in PARITY_FIELDS}
    p.update({k:lt[k]==rt[k] for k in STREAMS}); assert all(p.values()), p
    return p


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('stage', choices=('control','select','passive','finish')); args = ap.parse_args()
    if args.stage == 'control':
        old, ot = get(RET/'exposure-intent-2026085-latch-20260913-v1'); new, nt = valid('down-control')
        out = dict(status='PASS', parity=parity(old,ot,new,nt), result_sha256=sha(job('down-control')/'result.json'))
        (R/(STEM+'_CONTROL.json')).write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps(out)); return
    up, ut = valid('up-baseline')
    if args.stage == 'select':
        rows = []
        for row in up['repair_capacity_frontier_rows']:
            if row['side'] != 'DOWN' or not (0 < row['qty'] <= row['atomic_repair_need']+1e-9): continue
            st = next(x for x in ut['intent'] if x['t']==row['t'])
            if st['inv']['UP'] <= st['inv']['DOWN']: continue
            rows.append((row,st))
        assert rows, 'No eligible prefix; do not fabricate a checkpoint'
        row, state = min(rows,key=lambda pair:pair[0]['t'])
        ledger = Ledger()
        for ev in up['atomic_responsibility_events']:
            if ev['t'] > row['t']: break
            ledger.process_batch(ev['t'],ev['fill_up'],ev['fill_down'])
        assert abs(ledger.outstanding('UP')-row['atomic_repair_need']) < 1e-7
        selection = dict(status='SELECTED_NOT_EXECUTED', t=row['t'], side='DOWN', frontier=row, state=state,
            atomic_queues=ledger.q, eligible_count=len(rows), baseline_result_sha256=sha(job('up-baseline')/'result.json'),
            rule='Earliest own UP-surplus DOWN repair frontier. Future outcome not used to choose.', oracle=True, runtime_eligible=False)
        path = R/(STEM+'_SELECTION.json'); assert not path.exists(); path.write_text(json.dumps(selection,indent=2)+'\n')
        for tag, route in [('passive','PASSIVE'),('active','ACTIVE')]:
            wave = dict(progress_artifact='data/research/ORACLE_REPAIR_'+tag.upper()+'_PROGRESS_20260913.json',
                jobs=[dict(job_id=job(tag).name,argv=['.venv/Scripts/python.exe','.lan_worker_v1/staging/'+PACKAGE.name+'/oracle_runner.py',
                    '--mode','ORACLE_UP','--repair-route',route,'--repair-time',str(row['t']),'--repair-side','DOWN'],
                    cwd='.',max_threads=4,min_free_ram_gb=6,max_start_cpu_pct=90,required_artifact='result.json')])
            (R/('oracle_repair_'+tag+'_wave_20260913.json')).write_text(json.dumps(wave,indent=2)+'\n')
        print(json.dumps(selection)); return
    selection = read(R/(STEM+'_SELECTION.json')); passive, pt = valid('passive')
    pg = parity(up,ut,passive,pt)
    assert len(passive['frontier_exact_hits']) == 1
    if args.stage == 'passive':
        out = dict(status='PASS',parity=pg,hit=passive['frontier_exact_hits'][0]);print(json.dumps(out))
        (R/(STEM+'_PASSIVE_CONTROL.json')).write_text(json.dumps(out,indent=2)+'\n'); return
    active, at = valid('active'); t = selection['t']
    prefix = {k:[x for x in pt[k] if x['t']<t]==[x for x in at[k] if x['t']<t] for k in STREAMS}; assert all(prefix.values())
    left = passive['frontier_exact_hits'][0]; right = active['frontier_exact_hits'][0]
    shared = {k:left[k]==right[k] for k in left if k not in ('exec_route','exec_price')}; assert all(shared.values()), shared
    sp = [x for x in pt['intent'] if x['t']==t]; sa = [x for x in at['intent'] if x['t']==t]; assert sp==sa
    source = read(ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz')
    end = source['market']['window_end_ms']; target = target_rows(source)
    old = {r['id']:r for r in selection['atomic_queues']['UP']}; initial = sum(r['remaining'] for r in old.values())
    out = dict(version=STEM,status='COMPLETE',selection_execution_status='BOTH_ARMS_COMPLETE',oracle=True,runtime_eligible=False,target_runtime_access=True,
        control_parity=pg,prefix_parity=prefix,shared_checkpoint=shared,selection=selection,arms={},provenance=[])
    for tag,d,tr in [('passive',passive,pt),('active',active,at)]:
        payments = [(e['t'],p) for e in d['atomic_responsibility_events'] if e['t']>t for p in e['payments'] if p['responsibility_id'] in old]
        paid = sum(p['qty'] for _,p in payments); assert paid <= initial+1e-7
        remaining = initial; previous = t; area = 0.
        for when,p in payments:
            assert previous <= when <= end
            area += remaining*(when-previous)/1000; remaining=max(0.,remaining-p['qty']); previous=when
        area += remaining*(end-previous)/1000
        completed = {p['responsibility_id']:when for when,p in payments if p['remaining_after']<=1e-8}
        carrier = d['exact_frontier_carrier']; own = batches(own_rows(d),1000)
        child_lower = child_upper = 0.
        for ev in d['atomic_responsibility_events']:
            if ev['t'] <= t: continue
            old_paid = sum(p['qty'] for p in ev['payments'] if p['responsibility_id'] in old)
            child_qty = sum(fr['fill_increment'] for fr in ev['fill_rows'] if fr['key']==carrier['key'])
            # Conservative attribution when one observation contains multiple fills.
            child_lower += max(0.,old_paid-(ev['fill_down']-child_qty))
            child_upper += min(old_paid,child_qty)
        assert -1e-7 <= child_lower <= child_upper+1e-7 and child_upper <= carrier['filled']+1e-7
        out['arms'][tag] = dict(metrics=compact(d),topology=topology(own)['metrics'],target_orientation=orientation(target,own),
            cohort=dict(initial_qty=initial,initial_lots=len(old),paid=paid,remaining=remaining,completed=len(completed),
                first_payment_delay_ms=payments[0][0]-t if payments else None,
                all_paid_delay_ms=max(completed.values())-t if len(completed)==len(old) else None,
                debt_area_share_seconds=area,
                selected_carrier_old_payment_bounds=[child_lower,child_upper],
                other_carrier_old_payment_bounds=[paid-child_upper,paid-child_lower],
                completion_at_eof_upper_bound=bool(completed and max(completed.values())==end)),
            repair_carrier={k:carrier[k] for k in ['key','route','qty','filled','fill_fraction','first_fill_latency_ms','terminal_latency_ms','state']},
            subsequent_repair_frontiers=sum(r['t']>t for r in d['repair_capacity_frontier_rows']),
            subsequent_fresh_frontiers=sum(r['t']>t for r in d['fresh_capacity_frontier_rows']))
        out['provenance'].append(dict(job=job(tag).name,result_sha256=sha(job(tag)/'result.json'),trace_sha256=sha(job(tag)/'clock_trace.json.gz')))
    down, dt = valid('down-control')
    out['direction_controls'] = {}
    for name,d in [('down-control',down),('up-baseline',up)]:
        own = batches(own_rows(d),1000)
        out['direction_controls'][name] = dict(metrics=compact(d),topology=topology(own)['metrics'],target_orientation=orientation(target,own))
    p = out['arms']['passive']; a = out['arms']['active']
    out['contrast'] = dict(debt_area_reduction_fraction=1-a['cohort']['debt_area_share_seconds']/p['cohort']['debt_area_share_seconds'],
        total_cost_delta=a['metrics']['cost']-p['metrics']['cost'],
        completion_time_equal=a['cohort']['all_paid_delay_ms']==p['cohort']['all_paid_delay_ms'],
        final_inventory_equal_within_1e_7=all(abs(a['metrics'][k]-p['metrics'][k])<1e-7 for k in ('inventory_up','inventory_down')))
    out['limitations']=['One oracle-conditioned market and one fork. Not a learned trigger or full Target repair-loop recovery.',
        'Only route at selected frontier changes; later own inventory, Fresh orders and fills are endogenous.',
        'Atomic FIFO cohort measures observed economic payment, not Target private lot identity.',
        'Any EOF-only completion is bounded by last observation, not precise exchange event timing.']
    (R/(STEM+'_RESULT.json')).write_text(json.dumps(out,indent=2,allow_nan=False)+'\n'); print(json.dumps(out))


if __name__=='__main__': main()
