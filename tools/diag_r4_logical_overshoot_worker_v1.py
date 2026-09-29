from __future__ import annotations
import json,lzma,sys
from pathlib import Path
ROOT=Path(r'C:\BTC5M-worker') if Path(__file__).resolve().parent.name!='tools' else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_preposition_responsibility_prune_v2 as v2
from tools import hftbacktest_execution_tape_feed_v1 as tape
MID=1753396
p=ROOT/'data/hft_forward_paper_v1/markets'/f'{MID}.json.xz'
print('path',p,'exists',p.exists())
if not p.exists():
    hits=list((ROOT/'data/hft_forward_paper_v1/markets').glob(f'*{MID}*.json.xz'))
    print('hits',hits)
    if not hits: raise SystemExit(2)
    p=hits[0]
with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
events,times,meta=tape.build_archive_events(MID,trade_offset='mid')
orders,takers,dec=v2.prep(d,meta,1092+5000)
print('orders',len(orders))
seen=set()
for o in orders:
    print(json.dumps({'logical':o['logical'],'oid':o['oid'],'side':o['side'],'qty':o['qty'],'need':o['need'],'px':o['px'],'cancel':o.get('cancel')},ensure_ascii=False))
    if o['logical'] in seen: print('DUP_LOGICAL',o['logical'])
    seen.add(o['logical'])
print('unique',len(seen))
