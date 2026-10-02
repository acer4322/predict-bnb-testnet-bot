"""Build strategy_lab fixture directories from research-data event batches:  <out>/<market_id>/events.npz (symlink or copy) + FIXTURE.json {"conversion_info": {"firstReceivedMs": ...}} from each market's META.json.
usage: python tools/hft_repro/make_fixture_dirs.py EVENTS_BATCH_DIR OUT_DIR [--copy]   (EVENTS_BATCH_DIR contains markets/<id>/{events.npz,META.json})"""
import sys, json, shutil, os
from pathlib import Path
src, out = Path(sys.argv[1]) / 'markets', Path(sys.argv[2]); copy = '--copy' in sys.argv; n = 0
for p in sorted(src.iterdir()):
    meta = json.load(open(p / 'META.json')); d = out / p.name; d.mkdir(parents=True, exist_ok=True)
    t = d / 'events.npz'
    if not t.exists():
        if copy or os.name == 'nt': shutil.copy2(p / 'events.npz', t)
        else: os.symlink(p / 'events.npz', t)
    (d / 'FIXTURE.json').write_text(json.dumps({'conversion_info': {'firstReceivedMs': meta['first_full_checkpoint_received_ms']}})); n += 1
print('fixtures written:', n, '->', out)
