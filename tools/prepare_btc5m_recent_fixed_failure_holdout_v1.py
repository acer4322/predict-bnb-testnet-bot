from __future__ import annotations
import gzip,hashlib,json,lzma,shutil,sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MARKETS=(2280434,2206608,2201485,2200944)
PARENTS={
 'BASE':ROOT/'.lan_worker_v1/fixed_direction_pair_v1_20260915',
 'GATE':ROOT/'.lan_worker_v1/fixed_direction_failure_delta_gate_v1_20260915',
}
OUTS={
 'BASE':ROOT/'.lan_worker_v1/fixed_direction_recent_middle_base_v1_20260915',
 'GATE':ROOT/'.lan_worker_v1/fixed_direction_recent_middle_failure_delta_v1_20260915',
}
BOOK_DB=ROOT/'data/wallet_maker_book_inference.db'
TAPE_DIR=ROOT/'data/execution_tape_v1/markets'
TARGET_DB=ROOT/'data/target_wallet_official_v1.db'
EPS=1e-12

def sha(p:Path):
 h=hashlib.sha256();
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''): h.update(b)
 return h.hexdigest()

def apply_changes(state,changes):
 for side in ('bids','asks'):
  for row in (changes or {}).get(side,[]):
   p=float(row[0]); new=float(row[2])
   if new<=EPS: state[side].pop(p,None)
   else: state[side][p]=new

def reconstruct_books(tape, start, end):
 state={'bids':{},'asks':{}}; out=[]
 for u in tape['updates']:
  src,recv,_,chk,bids,asks,changes=u
  src=int(src);recv=int(recv)
  if int(chk):
   state={'bids':{float(k):float(v) for k,v in (bids or {}).items() if float(v)>EPS},
          'asks':{float(k):float(v) for k,v in (asks or {}).items() if float(v)>EPS}}
  else: apply_changes(state,changes)
  if not state['bids'] and not state['asks']: continue
  bb=max(state['bids']) if state['bids'] else None;ba=min(state['asks']) if state['asks'] else None
  if bb is not None and ba is not None and not (0<bb<ba<1): continue
  if src < start-5000 or src > end+5000: continue
  b5=sorted(state['bids'].items(),reverse=True)[:5]
  a5=sorted(state['asks'].items())[:5]
  out.append(dict(source_ms=src,received_ms=recv,best_bid=bb,best_ask=ba,bids=[[p,q] for p,q in b5],asks=[[p,q] for p,q in a5]))
 if not out: raise RuntimeError('no reconstructed books')
 return out

def main():
 con=sqlite3.connect(BOOK_DB);con.row_factory=sqlite3.Row
 tcon=sqlite3.connect(TARGET_DB);tcon.row_factory=sqlite3.Row
 market_meta={}
 payloads={}
 for m in MARKETS:
  q=con.execute('select * from maker_execution_market_quality_v1 where market_id=?',(m,)).fetchone()
  if q is None or q['quality_status']!='COMPLETE_FORWARD_V1' or int(q['eligible_execution_training'])!=1: raise RuntimeError((m,'quality',dict(q) if q else None))
  tape_path=TAPE_DIR/f'{m}.json.xz'
  tape=json.loads(lzma.decompress(tape_path.read_bytes()))
  start,end=int(q['window_start_ms']),int(q['window_end_ms'])
  books=reconstruct_books(tape,start,end)
  result=tcon.execute('select winner,net_pnl_usdt,buy_notional_usdt,up_position_shares,down_position_shares from target_market_results where market_id=?',(m,)).fetchone()
  if result is None: raise RuntimeError((m,'missing result'))
  market=dict(market_id=m,window_start_ms=start,window_end_ms=end,quality_status=q['quality_status'])
  p=dict(market=market,books=books,public=[],tape=dict(file=f'tapes/{m}.json.xz',sha256=sha(tape_path)),
         actor_input_contract='Current reconstructed public L2 book and canonical OWN state only; supplemental public[] is intentionally empty because this frozen controller never reads it. Execution tape is simulator input, not actor feature.')
  payloads[m]=p
  market_meta[m]=dict(quality_status=q['quality_status'],books=len(books),first_source_ms=books[0]['source_ms'],last_source_ms=books[-1]['source_ms'],window=[start,end],winner=result['winner'],target_actual=float(result['net_pnl_usdt']),target_opposite=(float(result['down_position_shares'] if result['winner']=='UP' else result['up_position_shares'])-float(result['buy_notional_usdt'])))
 con.close();tcon.close()
 win_sha=hashlib.sha256(json.dumps({str(m):market_meta[m]['window'] for m in MARKETS},sort_keys=True,separators=(',',':')).encode()).hexdigest()
 for tag,parent in PARENTS.items():
  out=OUTS[tag]
  if not out.exists(): shutil.copytree(parent,out)
  (out/'inputs/tapes').mkdir(parents=True,exist_ok=True)
  for m,payload in payloads.items():
   raw=json.dumps(payload,separators=(',',':'),allow_nan=False).encode()
   (out/f'inputs/public_{m}.json.gz').write_bytes(gzip.compress(raw,mtime=0))
   shutil.copy2(TAPE_DIR/f'{m}.json.xz',out/f'inputs/tapes/{m}.json.xz')
  man=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
  man['version']=f'BTC5M_RECENT_MIDDLE_{tag}_V1_20260915'
  man['recent_middle_holdout_markets']=list(MARKETS)
  man['window_source_sha256']=win_sha
  man['recent_input_note']='Post-2026-09-10 temporal holdout; complete execution tape reconstructed L2 only. supplemental public[] empty and unused by this frozen controller.'
  man['files']={p.relative_to(out).as_posix():sha(p) for p in out.rglob('*') if p.is_file() and p.name!='manifest.json'}
  (out/'manifest.json').write_text(json.dumps(man,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'status':'PASS','markets':market_meta,'window_source_sha256':win_sha,'packages':{k:str(v.relative_to(ROOT)) for k,v in OUTS.items()}},indent=2))

if __name__=='__main__':main()
