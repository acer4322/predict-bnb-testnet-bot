from __future__ import annotations
import gzip,hashlib,json,lzma,pathlib,shutil,sqlite3

ROOT=pathlib.Path(__file__).resolve().parents[1]
PARENTS={
 'baseline':ROOT/'.lan_worker_v1/fixed_direction_pair_v1_20260915',
 'gate':ROOT/'.lan_worker_v1/fixed_direction_failure_delta_gate_v1_20260915',
}
OUTS={
 'baseline':ROOT/'.lan_worker_v1/fixed_direction_pair_recent_failure_holdout_v1_20260915',
 'gate':ROOT/'.lan_worker_v1/fixed_direction_failure_delta_gate_recent_holdout_v1_20260915',
}
MARKETS=(2280434,2206608,2201485,2200944)
TAPE_DIR=ROOT/'data/execution_tape_v1/markets'
BOOK_DB=ROOT/'data/wallet_maker_book_inference.db'


def sha(p:pathlib.Path)->str:
 h=hashlib.sha256();
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''): h.update(b)
 return h.hexdigest()

def rebuild_books(tape):
 state={'bids':{},'asks':{}};out=[]
 for u in tape['updates']:
  source_ms,received_ms,_,is_checkpoint,bz,az,changes=u
  if int(is_checkpoint):
   state={'bids':{float(k):float(v) for k,v in (bz or {}).items()},'asks':{float(k):float(v) for k,v in (az or {}).items()}}
  else:
   for side in ('bids','asks'):
    for row in (changes or {}).get(side,[]):
     p=float(row[0]);after=float(row[2])
     if after<=1e-12: state[side].pop(p,None)
     else: state[side][p]=after
  bids=sorted(state['bids'].items(),reverse=True)[:5];asks=sorted(state['asks'].items())[:5]
  out.append(dict(source_ms=int(source_ms),received_ms=int(received_ms),best_bid=(bids[0][0] if bids else None),best_ask=(asks[0][0] if asks else None),bids=[[p,q] for p,q in bids],asks=[[p,q] for p,q in asks]))
 return out

def quality_rows():
 con=sqlite3.connect(f'file:{BOOK_DB.resolve()}?mode=ro',uri=True);con.row_factory=sqlite3.Row
 try:
  out={}
  for m in MARKETS:
   r=con.execute('select market_id,quality_status,eligible_execution_training,window_start_ms,window_end_ms,l2_rows,meta_rows,match_rows from maker_execution_market_quality_v1 where market_id=?',(m,)).fetchone()
   if r is None: raise RuntimeError(f'missing quality {m}')
   d=dict(r)
   if int(d['eligible_execution_training'] or 0)!=1: raise RuntimeError(f'not eligible {m}: {d}')
   out[m]=d
  return out
 finally:con.close()

def copy_code(parent,out):
 out.mkdir(parents=True);(out/'inputs/tapes').mkdir(parents=True)
 for p in parent.iterdir():
  if p.name in ('manifest.json','inputs','__pycache__'): continue
  if p.is_file(): shutil.copy2(p,out/p.name)

def main():
 q=quality_rows();windows=[];input_meta={}
 # prove reconstruction parity on historical frozen market before producing recent inputs.
 old=json.loads(gzip.decompress((PARENTS['baseline']/'inputs/public_2019018.json.gz').read_bytes()))
 old_tape=json.load(lzma.open(PARENTS['baseline']/'inputs/tapes/2019018.json.xz','rt'))
 assert rebuild_books(old_tape)==old['books'],'historical tape->books parity failed'
 for label,parent in PARENTS.items():
  out=OUTS[label]
  if out.exists(): raise FileExistsError(out)
  copy_code(parent,out)
  for m in MARKETS:
   tape_src=TAPE_DIR/f'{m}.json.xz'
   tape=json.load(lzma.open(tape_src,'rt'))
   qq=q[m];start=int(qq['window_start_ms']);end=int(qq['window_end_ms'])
   assert int(tape['market']['market_id'])==m and int(tape['market']['window_end_ms'])==end
   books=rebuild_books(tape)
   public=dict(
    market=dict(market_id=m,window_start_ms=start,window_end_ms=end,quality_status=qq['quality_status']),
    books=books,
    public=[],
    tape=dict(file=f'tapes/{m}.json.xz',sha256=sha(tape_src)),
    actor_input_contract='Current execution-tape-reconstructed top5 public book frames and canonical OWN only. Supplemental legacy public feature array intentionally empty because this frozen controller does not read it. Target actions/results are excluded from actor inputs.'
   )
   raw=json.dumps(public,separators=(',',':'),allow_nan=False).encode()
   (out/f'inputs/public_{m}.json.gz').write_bytes(gzip.compress(raw,mtime=0))
   shutil.copy2(tape_src,out/f'inputs/tapes/{m}.json.xz')
   if label=='baseline':
    windows.append([m,start,end]);input_meta[m]=dict(books=len(books),quality=qq['quality_status'],tape_sha256=sha(tape_src),input_sha256=sha(out/f'inputs/public_{m}.json.gz'))
  manifest=json.loads((parent/'manifest.json').read_text())
  manifest['version']=('BTC5M_FIXED_DIRECTION_PAIR_RECENT_FAILURE_HOLDOUT_V1_20260915' if label=='baseline' else 'BTC5M_FIXED_DIRECTION_FAILURE_DELTA_GATE_RECENT_HOLDOUT_V1_20260915')
  manifest['parent_manifest_sha256']=sha(parent/'manifest.json')
  manifest['market']=None;manifest['market_count']=len(MARKETS);manifest['paired_markets']=list(MARKETS)
  manifest['cohort']='POST_20260910_WRONG_BELIEF_FAILURE_MIDDLE50_TEMPORAL_HOLDOUT_V1'
  manifest['window_source_sha256']=hashlib.sha256(json.dumps(windows,separators=(',',':')).encode()).hexdigest()
  manifest['recent_input_reconstruction']='Execution tape L2 updates -> exact frozen top5 book schema; historical 2019018 parity 1440/1440 PASS; supplemental public[] unused by controller and intentionally empty.'
  manifest['files']={p.relative_to(out).as_posix():sha(p) for p in out.rglob('*') if p.is_file() and p.name!='manifest.json'}
  (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
  assert all(sha(out/n)==h for n,h in manifest['files'].items())
 result=dict(status='PASS',markets=list(MARKETS),windows=windows,input_meta=input_meta,packages={k:str(v.relative_to(ROOT)) for k,v in OUTS.items()},package_manifest_sha256={k:sha(v/'manifest.json') for k,v in OUTS.items()},historical_reconstruction_parity='2019018 books 1440/1440 exact')
 (ROOT/'data/research/BTC5M_RECENT_FAILURE_HOLDOUT_INPUT_V1_20260915.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result,indent=2))
if __name__=='__main__':main()
