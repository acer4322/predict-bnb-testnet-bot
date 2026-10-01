"""Freeze gate (inactive unless V12G_FREEZE_GATE=ON): final plan pass inserted in envelope before the own-cross resolve and the
governor/risk verification. While the strategy's freeze predicate holds (CG1 low-mode freeze, or CG4 chop freeze if enabled), every
NEW order of the plan is dropped, whatever channel produced it (closes the channels that bypass the CG1 veto sites). CANCEL/KEEP
operations are untouched. Drops are counted by channel (role, else route) and written to freeze_gate_trace.json."""
import atexit, json, os
from pathlib import Path

ENABLED = os.environ.get('V12G_FREEZE_GATE', 'OFF') == 'ON'
PRED = None   # registered by run_variant: callable -> bool
STATS = dict(enabled=ENABLED, frames=0, dropped=0, by_channel={}, first_t=None, last_t=None)


def apply(f, ops):
    if not ENABLED or PRED is None or not PRED(): return ops
    news = [o for o in ops if o['kind'] == 'NEW']
    if not news: return ops
    STATS['frames'] += 1; STATS['dropped'] += len(news); t = int(f['t'])
    if STATS['first_t'] is None: STATS['first_t'] = t
    STATS['last_t'] = t
    for o in news:
        ch = str(o.get('role') or o.get('route')); STATS['by_channel'][ch] = STATS['by_channel'].get(ch, 0) + 1
    return [o for o in ops if o['kind'] != 'NEW']


def _save_exit():
    out = os.environ.get('BTC5M_LAN_RESULT_DIR')
    if out and Path(out).is_dir(): (Path(out) / 'freeze_gate_trace.json').write_text(json.dumps(STATS), encoding='utf-8')


atexit.register(_save_exit)
