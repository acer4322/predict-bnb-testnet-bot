from __future__ import annotations
import json,math,sqlite3,time
from pathlib import Path
from typing import Any
from .execution_tape_archive_v1 import load_archive
VERSION='PREDICT_EXECUTION_TAPE_QUALITY_V1'

def _pct(xs:list[int],q:float)->int|None:
    if not xs:return None
    a=sorted(xs); p=(len(a)-1)*q; lo=int(math.floor(p)); hi=int(math.ceil(p)); return a[lo] if lo==hi else int(round(a[lo]*(hi-p)+a[hi]*(p-lo)))

def ensure_schema(con:sqlite3.Connection)->None:
    con.executescript('''
    CREATE TABLE IF NOT EXISTS maker_execution_market_quality_v1 (
      market_id INTEGER PRIMARY KEY,
      quality_status TEXT NOT NULL,
      eligible_execution_training INTEGER NOT NULL,
      eligible_legacy_replay INTEGER NOT NULL,
      window_start_ms INTEGER,
      window_end_ms INTEGER,
      first_source_ms INTEGER,
      last_source_ms INTEGER,
      first_source_delay_ms INTEGER,
      tail_gap_ms INTEGER,
      meta_rows INTEGER NOT NULL,
      l2_rows INTEGER NOT NULL,
      match_rows INTEGER NOT NULL,
      max_source_gap_ms INTEGER,
      p99_source_gap_ms INTEGER,
      archive_bytes INTEGER NOT NULL,
      assessed_at_ms INTEGER NOT NULL,
      evidence_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_execution_quality_status_v1 ON maker_execution_market_quality_v1(quality_status,market_id);
    '''); con.commit()

def assess_archive(path:Path,*,partial_market_ids:set[int]|None=None)->dict[str,Any]:
    tape=load_archive(path); m=int(tape['marketId']); market=tape.get('market') or {}; meta=tape.get('executionMeta') or []; updates=tape.get('updates') or []; matches=tape.get('matches') or []
    end=int(market.get('window_end_ms')) if market.get('window_end_ms') is not None else None; start=end-300_000 if end is not None else None
    times=[int(r[0]) for r in meta] if meta else []
    first=min(times) if times else None; last=max(times) if times else None; gaps=sorted(b-a for a,b in zip(sorted(times),sorted(times)[1:])) if len(times)>1 else []
    first_delay=first-start if first is not None and start is not None else None; tail=end-last if last is not None and end is not None else None
    reasons=[]
    if partial_market_ids and m in partial_market_ids: reasons.append('EXPLICIT_PARTIAL_RESTART')
    if not meta: reasons.append('NO_SETTLEMENT_METADATA_LEGACY')
    else:
        if first_delay is not None and first_delay>5000: reasons.append('PARTIAL_START_GT_5S')
        if tail is not None and tail>5000: reasons.append('EARLY_STOP_GT_5S')
        if gaps and max(gaps)>5000: reasons.append('SOURCE_GAP_GT_5S')
    if not updates: reasons.append('NO_L2_UPDATES')
    if not matches: reasons.append('NO_RAW_MATCHES')
    if 'EXPLICIT_PARTIAL_RESTART' in reasons: status='PARTIAL_RESTART'
    elif not meta: status='LEGACY_NO_SETTLEMENT_META' if updates and matches else 'INCOMPLETE_LEGACY'
    elif any(x in reasons for x in ['PARTIAL_START_GT_5S','EARLY_STOP_GT_5S','SOURCE_GAP_GT_5S','NO_L2_UPDATES','NO_RAW_MATCHES']): status='INCOMPLETE_FORWARD'
    else: status='COMPLETE_FORWARD_V1'
    eligible_training=status=='COMPLETE_FORWARD_V1'; eligible_legacy=status in {'COMPLETE_FORWARD_V1','LEGACY_NO_SETTLEMENT_META'}
    return {'version':VERSION,'marketId':m,'qualityStatus':status,'eligibleExecutionTraining':eligible_training,'eligibleLegacyReplay':eligible_legacy,'windowStartMs':start,'windowEndMs':end,'firstSourceMs':first,'lastSourceMs':last,'firstSourceDelayMs':first_delay,'tailGapMs':tail,'metaRows':len(meta),'l2Rows':len(updates),'matchRows':len(matches),'maxSourceGapMs':max(gaps) if gaps else None,'p99SourceGapMs':_pct(gaps,.99),'archiveBytes':path.stat().st_size,'reasons':reasons}

def record_quality(db_path:Path,archive_path:Path,*,partial_market_ids:set[int]|None=None)->dict[str,Any]:
    q=assess_archive(archive_path,partial_market_ids=partial_market_ids); con=sqlite3.connect(db_path,timeout=10)
    try:
        ensure_schema(con); con.execute('''INSERT INTO maker_execution_market_quality_v1 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(market_id) DO UPDATE SET quality_status=excluded.quality_status,eligible_execution_training=excluded.eligible_execution_training,eligible_legacy_replay=excluded.eligible_legacy_replay,window_start_ms=excluded.window_start_ms,window_end_ms=excluded.window_end_ms,first_source_ms=excluded.first_source_ms,last_source_ms=excluded.last_source_ms,first_source_delay_ms=excluded.first_source_delay_ms,tail_gap_ms=excluded.tail_gap_ms,meta_rows=excluded.meta_rows,l2_rows=excluded.l2_rows,match_rows=excluded.match_rows,max_source_gap_ms=excluded.max_source_gap_ms,p99_source_gap_ms=excluded.p99_source_gap_ms,archive_bytes=excluded.archive_bytes,assessed_at_ms=excluded.assessed_at_ms,evidence_json=excluded.evidence_json''',(
            q['marketId'],q['qualityStatus'],int(q['eligibleExecutionTraining']),int(q['eligibleLegacyReplay']),q['windowStartMs'],q['windowEndMs'],q['firstSourceMs'],q['lastSourceMs'],q['firstSourceDelayMs'],q['tailGapMs'],q['metaRows'],q['l2Rows'],q['matchRows'],q['maxSourceGapMs'],q['p99SourceGapMs'],q['archiveBytes'],int(time.time()*1000),json.dumps({'version':VERSION,'reasons':q['reasons']},separators=(',',':'))));
        try: con.execute("update maker_book_inference_markets set status=? where market_id=?",(q['qualityStatus'],q['marketId']))
        except sqlite3.Error: pass
        con.commit()
    finally: con.close()
    return q
