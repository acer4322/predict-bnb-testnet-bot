"""Read frozen v28 traces; compare trigger booleans on the observed trajectory.

No policy imports, replay, mutation of source artifacts, or future-side labels.
The candidate changes only last_H storage to physical side keys, preserving
the original None initialization and original observed submission updates.
This is not an executed alternative trading trajectory.
"""
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
BASE = ROOT / 'data/research/lan_worker_returns/btc5m-v12g-active-repair4-small-20260927-v28/arms'
rows = []
for directory in sorted(BASE.glob('v12g28_AR4_R25_*')):
    source = directory / 'restoration_trace.json.gz'
    trace = json.loads(gzip.decompress(source.read_bytes()))
    by_side = {}
    legacy_last = None
    checked = submits = 0
    differences = []
    original_checks = []
    for event in trace['events']:
        if not event.get('eligible') and event.get('reason') != 'V12G_AR_PASSIVE_OK':
            continue
        checked += 1
        h = event['H']
        previous = by_side.get(event['side'])
        new_deficit = h <= -150 + 1e-9 and (previous is None or h < previous - 1e-9)
        old_deficit = h <= -150 + 1e-9 and (legacy_last is None or h < legacy_last - 1e-9)
        if event.get('eligible'):
            submits += 1
            trigger = event['v12g_trigger']
            assert old_deficit == trigger['deficit'], 'Legacy trigger reconstruction mismatch'
            changed_any = not (new_deficit or trigger['cheap'] or trigger['stranded'])
            if new_deficit != old_deficit or changed_any:
                differences.append(dict(t=event['t'], side=event['side'], old_deficit=old_deficit,
                                        new_deficit=new_deficit, changes_any_trigger=changed_any))
            if previous is None and legacy_last is not None:
                original_checks.append(dict(t=event['t'], side=event['side'], H=h,
                                            previous_legacy_H=legacy_last, previous_side_H=None,
                                            old_deficit=old_deficit, new_deficit=new_deficit))
            by_side[event['side']] = h
            legacy_last = h
        else:
            assert not old_deficit, 'PASSIVE_OK disagrees with legacy deficit trigger'
            if new_deficit:
                differences.append(dict(t=event['t'], side=event['side'],
                                        old_eligible=False, new_any_trigger=True))
    rows.append(dict(market=int(directory.name.rsplit('_', 1)[1]),
                     trigger_entries=checked, legacy_submissions=submits,
                     differences=differences, first_side_examples=original_checks,
                     source=str(source.relative_to(ROOT)),
                     source_sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
assert len(rows) == 10
result = dict(scope='FROZEN_OBSERVED_TRAJECTORY_ONLY', markets=10,
              trigger_entries=sum(x['trigger_entries'] for x in rows),
              legacy_submissions=sum(x['legacy_submissions'] for x in rows),
              differences=sum(len(x['differences']) for x in rows), per_market=rows,
              limitation='No native rerun or new trajectory. No peak or initialization changes.')
Path(__file__).with_name('LAST_H_CHECK.json').write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in result.items() if k != 'per_market'}))
