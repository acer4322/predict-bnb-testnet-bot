from __future__ import annotations
import json, sqlite3, shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC_STRAT=ROOT/'data'/'strategy_target_compare_v1.db'
SRC_BOOK=ROOT/'data'/'wallet_maker_book_inference.db'
OUT=ROOT/'data'/'research'/'r4_v0'/'p0_provenance_v1'/'r3_dream_core_compact_v1'
HIST=[1490557,1490930,1490940,1491074,1491858,1491861,1492232,1492235,1492246,1492362,1492396,1492500,1492547,1493063,1493070,1493077,1493151]
RECENT=[1770676,1774144,1774549,1774858,1775465,1775470,1776637,1777166,1777167,1777186,1777237,1777712,1782260,1782263,1782437,1782453,1782487,1782750,1783150,1783154]
HIST_SRC='UNIFIED_CONTROLLER_PAPER_V1%'
RECENT_SRC='UNIFIED_PROMOTED_OWNSTATE_V4_R2_RESIDUAL_FORWARD_PAPER'

def ro(p):
 c=sqlite3.connect(f'file:{p.resolve().as_posix()}?mode=ro',uri=True,timeout=60);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');return c

def schema(src,table):
 r=src.execute("select sql from sqlite_master where type='table' and name=?",(table,)).fetchone()
 if not r or not r[0]:raise RuntimeError(f'missing schema {table}')
 return str(r[0])

def idx_sqls(src,table):
 return [str(r[0]) for r in src.execute("select sql from sqlite_master where type='index' and tbl_name=? and sql is not null",(table,))]

def win_end(snap,decision_ms):
 for k in ('windowEndMs','window_end_ms'):
  try:
   v=int(float(snap.get(k)))
   if v>0:return v
  except Exception:pass
 for k in ('secondsLeft','seconds_left'):
  try:return int(decision_ms+float(snap.get(k))*1000)
  except Exception:pass
 return None

def main():
 OUT.mkdir(parents=True,exist_ok=True)
 sdb=OUT/'strategy_target_compare_v1.db';bdb=OUT/'wallet_maker_book_inference.db'
 for p in (sdb,bdb):
  if p.exists():p.unlink()
 src=ro(SRC_STRAT); dst=sqlite3.connect(sdb);dst.row_factory=sqlite3.Row
 try:
  dst.execute(schema(src,'our_decisions'))
  cols=[r[1] for r in src.execute('pragma table_info(our_decisions)')]; ph=','.join('?'*len(cols)); cn=','.join(cols)
  meta=[]
  for label,ids,pat in [('historical17',HIST,HIST_SRC),('recent20',RECENT,RECENT_SRC)]:
   q=','.join('?'*len(ids)); sql=f"select * from our_decisions where market_id in ({q}) and strategy_version like ? order by market_id,decision_ms"
   rows=src.execute(sql,(*ids,pat)).fetchall();dst.executemany(f'insert into our_decisions({cn}) values({ph})',[tuple(r[c] for c in cols) for r in rows])
   by={}
   for r in rows:
    m=int(r['market_id']);d=by.setdefault(m,{'marketId':m,'minDecisionMs':int(r['decision_ms']),'maxDecisionMs':int(r['decision_ms']),'windowEndMs':None,'sourceStrategy':str(r['strategy_version'])})
    d['minDecisionMs']=min(d['minDecisionMs'],int(r['decision_ms']));d['maxDecisionMs']=max(d['maxDecisionMs'],int(r['decision_ms']))
    if d['windowEndMs'] is None:
     try:snap=json.loads(str(r['public_state_json']));d['windowEndMs']=win_end(snap,int(r['decision_ms'])) if isinstance(snap,dict) else None
     except Exception:pass
   meta.extend({'cohort':label,**v} for v in by.values())
  for sql in idx_sqls(src,'our_decisions'):
   try:dst.execute(sql)
   except sqlite3.OperationalError:pass
  dst.commit()
 finally:src.close();dst.close()

 src=ro(SRC_BOOK);dst=sqlite3.connect(bdb);dst.row_factory=sqlite3.Row
 try:
  for t in ('maker_book_inference_markets','maker_book_inference_updates'):dst.execute(schema(src,t))
  wes=sorted({int(x['windowEndMs']) for x in meta if x.get('windowEndMs')})
  q=','.join('?'*len(wes)); mrows=src.execute(f'select * from maker_book_inference_markets where window_end_ms in ({q}) order by market_id',wes).fetchall() if wes else []
  mcols=[r[1] for r in src.execute('pragma table_info(maker_book_inference_markets)')];dst.executemany(f"insert into maker_book_inference_markets({','.join(mcols)}) values({','.join('?'*len(mcols))})",[tuple(r[c] for c in mcols) for r in mrows])
  bywe={}
  for r in mrows:bywe.setdefault(int(r['window_end_ms']),[]).append(int(r['market_id']))
  ucols=[r[1] for r in src.execute('pragma table_info(maker_book_inference_updates)')]; ins=f"insert into maker_book_inference_updates({','.join(ucols)}) values({','.join('?'*len(ucols))})"
  copied=0; mapping=[]
  for x in meta:
   we=x.get('windowEndMs'); mids=bywe.get(int(we),[]) if we else []
   if not mids:
    mapping.append({**x,'bookMarketId':None,'bookRows':0});continue
   bm=mids[0];start=int(x['minDecisionMs']);end=int(x['maxDecisionMs'])
   ck=src.execute('select id,source_timestamp_ms from maker_book_inference_updates where market_id=? and is_checkpoint=1 and source_timestamp_ms<=? order by source_timestamp_ms desc,id desc limit 1',(bm,start)).fetchone()
   if ck is None:
    mapping.append({**x,'bookMarketId':bm,'bookRows':0,'checkpointMissing':True});continue
   rows=src.execute('select * from maker_book_inference_updates where market_id=? and id>=? and source_timestamp_ms<=? order by id',(bm,int(ck['id']),end)).fetchall()
   # Same book market can back multiple selected OUR markets only in pathological duplicate mappings; ignore duplicate ids safely.
   for r in rows:
    try:dst.execute(ins,tuple(r[c] for c in ucols));copied+=1
    except sqlite3.IntegrityError:pass
   mapping.append({**x,'bookMarketId':bm,'bookRows':len(rows),'checkpointSourceMs':int(ck['source_timestamp_ms'])})
  for t in ('maker_book_inference_markets','maker_book_inference_updates'):
   for sql in idx_sqls(src,t):
    try:dst.execute(sql)
    except sqlite3.OperationalError:pass
  dst.commit()
 finally:src.close();dst.close()
 manifest={'version':'R3_DREAM_CORE_COMPACT_V1','historicalIds':HIST,'recentIds':RECENT,'historicalSource':HIST_SRC,'recentSource':RECENT_SRC,'markets':meta,'bookMapping':mapping,'strategyDbBytes':sdb.stat().st_size,'bookDbBytes':bdb.stat().st_size,'bookUpdateRowsCopied':copied}
 (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
 print(json.dumps({'ok':True,'strategyDbBytes':manifest['strategyDbBytes'],'bookDbBytes':manifest['bookDbBytes'],'bookUpdateRowsCopied':copied,'strategyMarkets':len(meta),'mapped':sum(x.get('bookRows',0)>0 for x in mapping),'unmapped':[x['marketId'] for x in mapping if not x.get('bookRows',0)]}))
if __name__=='__main__':main()
