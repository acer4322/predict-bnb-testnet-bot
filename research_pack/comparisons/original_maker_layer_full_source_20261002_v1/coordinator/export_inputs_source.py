"""Public numerical input packets and complete replay source; no native import."""
import argparse, gzip, hashlib, json, lzma
from pathlib import Path
from export_packets import dump, finish, public_check
P=Path(__file__).resolve().parent
W=Path('C:/Users/acer4/.codex/worktrees/original-maker-layer-replay/predict-bnb-testnet-bot')
PACKS={'BTC':'original_maker_layer_20261002_v1r6','ETH':'original_maker_layer_eth861_20261002_v1r2'}
SOURCES=['original_maker_layer_20261002_v1r5','original_maker_layer_20261002_v1r6','original_maker_layer_20261002_v1r7','original_maker_layer_eth861_20261002_v1r2']
sha=lambda b:hashlib.sha256(b).hexdigest()

def numeric_book(t):
 assert set(t)=={'marketId','market','updates'}
 assert set(t['market'])=={'market_id','window_start_ms','window_end_ms'}
 for u in t['updates']:
  assert len(u)==7 and all(isinstance(x,(int,float)) for x in u[:4])
  for side in u[4:6]:
   if side is None:
    assert not u[3]
    continue
   assert isinstance(side,dict)
   assert all(0 <= float(price) <= 1 and isinstance(qty,(int,float)) for price,qty in side.items())
  assert set(u[6])<= {'bids','asks'}
  assert all(len(l)==4 and all(isinstance(v,(int,float)) for v in l)
             for side in u[6].values() for l in side)

def inputs(asset):
 d=P/'stage'/PACKS[asset]; rows=json.loads((d/'INPUTS.json').read_bytes())[asset]
 canonical={}
 roots=[W/'research_pack/events'] if asset=='BTC' else [W/'research_pack/eth5m/events']
 for root in roots:
  for f in root.rglob('events.npz'):
   mid=int(f.parent.name); canonical.setdefault(mid,[]).append(f)
 groups=[];files={};entries=[];mapping=[]
 for row in rows:
  mid=row['market_id']; candidates=[f for f in canonical.get(mid,[]) if sha(f.read_bytes())==row['events_sha256']]
  assert candidates,('Missing frozen remote NPZ',mid)
  event=candidates[0].relative_to(W).as_posix()
  tape=d/'base/inputs/tapes'/f'{mid}.json.xz';b=tape.read_bytes();numeric_book(json.loads(lzma.decompress(b)))
  names=[f'base/inputs/tapes/{mid}.json.xz',f'base/inputs/public_{mid}.json.gz',f'META/{mid}.json',f'fixtures/{mid}/FIXTURE.json']
  pf={n:(d/n).read_bytes() for n in names}
  if files and sum(map(len,files.values()))+sum(map(len,pf.values()))>44_000_000:
   groups.append((files,entries));files={};entries=[]
  files.update(pf)
  entry=dict(market_id=mid,asset=asset,events=event,events_sha256=row['events_sha256'],files={n:sha(v) for n,v in pf.items()},
             source_tape_note='Sanitized original public L2 source/received/sequence/checkpoint/bids/asks/deltas only; no raw matches, account metadata or private tape fields')
  entries.append(entry)
 if files:groups.append((files,entries))
 receipts=[]
 for i,(files,entries) in enumerate(groups,1):
  pid=f'original_maker_layer_{asset.lower()}{len(rows)}_inputs_b{i:02d}_20261002_v1'
  index=dict(id=pid,status='FROZEN_PUBLIC_INPUTS',markets=[e['market_id'] for e in entries],entries=entries,
             package=PACKS[asset],fits=0,live_changes=0,
             clock='u[0] source Unix ms; u[1] received Unix ms. Original decision replay follows received updates. NPZ unchanged: depth replay timestamps use received, public matches second-granular +500 ms. Complete-trade coverage UNKNOWN.')
  receipts.append(finish(P/'packets'/pid,files,index))
  for e in entries:mapping.append(dict(e,packet=pid))
 (P/f'{asset}_INPUT_PACKETS.json').write_bytes(dump(receipts))
 (P/f'{asset}_PUBLIC_ASSEMBLY.json').write_bytes(dump(mapping))
 print(json.dumps(dict(asset=asset,markets=len(rows),packets=len(receipts),bytes=sum(r['bytes'] for r in receipts),FLAGGED=0)))

def sources(review=False):
 files={}
 for name in SOURCES:
  d=P/'stage'/name
  for f in d.rglob('*'):
   if not f.is_file():continue
   n=f.relative_to(d).as_posix()
   if f.suffix=='.py' or (f.parent==d and f.suffix in ('.json','.md')) or n in ('base/manifest.json','overlay/actor_contract.json'):
    files[f'packages/{name}/{n}']=f.read_bytes()
 for n in ('analyze_full.py','strict_completion.py','classify_eth.py','export_packets.py','export_inputs_source.py'):
  files['coordinator/'+n]=(P/n).read_bytes()
 for asset in PACKS:files[f'{asset}_PUBLIC_ASSEMBLY.json']=(P/f'{asset}_PUBLIC_ASSEMBLY.json').read_bytes()
 for n in ('SOURCE_TRANSPORT_AUDIT.json','ETH_STRICT_INPUT_AUDIT.json','R2_SIZING_COMPONENT_SMOKE.json'):
  files['audits/'+n]=(P/n).read_bytes()
 guide='''# Original maker layer replay: reproduction inputs and evidence

The full original CG1AT alpha/theta progression/repair/passive-to-active/governor code is included. Of 79 mapped parent modules, 77 are byte-identical; patches.py adapts scratch/template/input transport and worker.py adapts orchestration. Keep mutable globals and the original quantity installer. The two sizing modules are staged before that original installer; final runtime SHA pins cover all 44 files.

For each package, copy its source tree, then assemble META, public descriptor, sanitized u7 L2 tapes and FIXTURE.json from the matching PUBLIC_ASSEMBLY mapping. Each mapping references the already published, identical events.npz. BTC R7 needs only its 382 scheduled cells; full META and mappings are retained for audits. Validate MANIFEST.json before execution. These manifests identify the exact staged source/input bytes; no event padding, trimming or reconversion is allowed.

The worker is DESKTOP-JIERAGF, Python 3.13.15. Full patched hftbacktest Python/Rust source is on code branch research/hft244-v49-repro-20261002, commit 3bf09cf645e1d83284825c32196e483294fcd182, research/repro/hft244_v49_20261002_v1. Upstream Python 2.4.4 (a244a142 lineage), Rust crate 0.9.4. Actual Windows candidate pyd SHA is 033469835b44f94f1a022e419e79be61be72a9f2501ceea11b8e255b4824d145, canonical 104-byte receipt ABI. Build provenance is in that release.

BASE uses risk-adverse queue and 250/250 ms; LOG100 and LOG500 use native log-prob queue at 100/100 and 500/500 ms. No fitted queue parameter; native model defaults are fixed by the pinned engine source. PartialFill, tick/lot 0.01, zero fees. These sensitivity arms jointly change queue and latency, and do not isolate either effect. Strategy parameters remain CG1AT frozen, including the original BTC sizing/passive quantity on ETH.

Winners/official labels and offline classifications occur only in coordinator metadata/evaluation, not the strategy input descriptor or runtime env. This is verified source-level non-access, not a physical file-access air gap. Original decisions see public sanitized L2 and OUR confirmed owner/receipt state. No Target fills/private intent or settlement are strategy inputs.

Original raw outputs are immutable on the source machine. Published result copies omit only general_finite_active_rows with omitted-row counts and original SHA. Full canonical execution clock and full decisions/native actions/direction rows are retained. Native CANCEL logging emits both an API row and keyed plan row; cancel_api_calls counts only the former. Native simulation IDs are not venue/account identifiers.

Failed transport/gate validations and EOF-censored cells are preserved in the report lineage. Completed or censored production cells are reused, not retried. EOF prefix receipts are not full-path PnL. Strict accounting gates retain the legacy active_matches_opportunity failure. No declaration that all old gates pass; raw gate booleans are authoritative over conservative orchestration metadata.

Input coverage and public-trade completeness are limited by captured public data; no missing rows are synthesized. Full-cohort EACH/bootstrap evaluation requires all fixed markets to have complete execution, official binary labels and known classification. No fits, live changes or shadow activation.
'''
 files['REPRODUCTION.md']=guide.encode()
 pid='original_maker_layer_full_source_20261002_v1'
 index=dict(id=pid,status='FROZEN_REPLAY_SOURCE',markets=sorted({r['market_id'] for asset in PACKS for r in json.loads((P/f'{asset}_PUBLIC_ASSEMBLY.json').read_bytes())}),
             packages=SOURCES,engine_code_branch='research/hft244-v49-repro-20261002',engine_code_commit='3bf09cf645e1d83284825c32196e483294fcd182',fits=0,live_changes=0)
 flags=public_check(files)
 (P/'SOURCE_FLAGGED_REVIEW.json').write_bytes(dump(flags))
 if flags and not review:
  print(json.dumps(dict(status='FLAGGED_REQUIRES_SOURCE_REVIEW',flags=flags)));return
 if flags:
  reviewed=json.loads((P/'SOURCE_FLAGGED_APPROVED.json').read_bytes())
  assert reviewed['status']=='REVIEWED_NO_PRIVATE_VALUES' and reviewed['flags']==flags
  files['INDEX.json']=dump(index)
  files['PRIVATE_DATA_CHECK.json']=dump(dict(status='PASS_MANUALLY_REVIEWED',FLAGGED=len(flags),private_identifiers=0,reviewed_flags=flags,
         review=reviewed['explanation'],schema_provenance='Public numerical book/engine source; all literal address and private identifier values absent'))
  files['SHA256SUMS.json']=dump(dict(files={n:sha(b) for n,b in files.items()}))
  dest=P/'packets'/pid;assert not dest.exists();assert sum(map(len,files.values()))<50_000_000
  dest.mkdir(parents=True)
  for n,b in files.items():f=dest/n;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b)
  rec=dict(id=pid,path=str(dest),files=len(files),bytes=sum(map(len,files.values())),FLAGGED=len(flags),manually_reviewed=len(flags))
 else:rec=finish(P/'packets'/pid,files,index)
 (P/'SOURCE_PACKET.json').write_bytes(dump(rec));print(json.dumps(rec))

if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('kind',choices=('BTC','ETH','source'));ap.add_argument('--reviewed',action='store_true');a=ap.parse_args()
 sources(a.reviewed) if a.kind=='source' else inputs(a.kind)
