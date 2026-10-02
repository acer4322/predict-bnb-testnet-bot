from __future__ import annotations
import json,os,re,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'; OUT=P/'r4_completion_horizon_v5_formal10_preregistered.json'
contract=json.loads((P/'r4_completion_horizon_admission_v5_formal_contract.json').read_text())
assert contract['status']=='FROZEN_BEFORE_COHORT_SELECTION'
# Exclude recent live-R3 chronology by rowid tail only; no outcomes.
live=set(); lc=sqlite3.connect(ROOT/'data/strategy_r3s_r31_echtgeld_v1.db'); mx=lc.execute('select max(rowid) from our_decisions').fetchone()[0] or 0
live={int(r[0]) for r in lc.execute('select distinct market_id from our_decisions where rowid>?',(max(0,mx-20000),))}; lc.close()
# Availability sources.
sc=sqlite3.connect(ROOT/'data/strategy_target_compare_v1.db'); bc=sqlite3.connect(ROOT/'data/wallet_maker_book_inference.db'); tc=sqlite3.connect(ROOT/'data/target_wallet_official_v1.db')
VERSION='UNIFIED_PROMOTED_OWNSTATE_V4_R2_RESIDUAL_FORWARD_PAPER'
settled={int(r[0]) for r in tc.execute("select market_id from target_market_results where asset='BTC' order by resolved_at_ms desc limit 3000")}
entries=[]
for e in os.scandir(ROOT/'data/execution_tape_v1/markets'):
 if not e.is_file() or not e.name.endswith('.json.xz'):continue
 try:m=int(e.name[:-8])
 except:continue
 entries.append(m)
entries.sort(reverse=True)
avail=[]
for m in entries:
 if m in live or m not in settled:continue
 if not sc.execute('select 1 from our_decisions where strategy_version=? and market_id=? limit 1',(VERSION,m)).fetchone():continue
 if not bc.execute('select 1 from maker_book_inference_updates where market_id=? limit 1',(m,)).fetchone():continue
 avail.append(m)
 if len(avail)>=160:break
sc.close();bc.close();tc.close()
need=set(avail); used=set(); scanned=0
pat=re.compile(rb'(?<!\d)(\d{7})(?!\d)')
for p in P.iterdir():
 if not p.is_file() or p==OUT or p.suffix.lower() not in {'.json','.md','.txt','.csv'}:continue
 try:data=p.read_bytes()
 except Exception:continue
 scanned+=len(data)
 for mm in pat.finditer(data):
  x=int(mm.group(1))
  if x in need:used.add(x)
sel=[x for x in avail if x not in used][:10]
if len(sel)<10:raise RuntimeError(f'only {len(sel)} unused candidates; avail={len(avail)} used={len(used)}')
out={'version':'R4_COMPLETION_HORIZON_V5_FORMAL10_PREREGISTERED','researchOnly':True,'selectionAfterFrozenContract':True,'selection':'First 10 newest execution-tape markets with R2 source snapshot, book-inference and settlement availability, absent from recent live-R3 chronology and absent as exact digit-bounded market IDs from all pre-existing P0 text artifacts. Availability only; winner/PNL/trigger values never read.','cohort':sel,'recentLiveExcludedCount':len(live),'availableCandidateCount':len(avail),'usedCandidateCount':len(used),'bytesScanned':scanned,'contract':'r4_completion_horizon_admission_v5_formal_contract.json'}
OUT.write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))
