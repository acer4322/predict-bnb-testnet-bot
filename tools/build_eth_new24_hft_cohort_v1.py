"""Freeze a chronology-only NEW24 ETH5M HFT graduation cohort.

Selection is strategy/outcome-blind except settlement winner existence:
- target_markets asset=ETH, status=SETTLED, winner UP/DOWN
- local execution tape exists and is an ETH 5m tape
- >=200 L2 updates, >=50 matches
- first source-clock update no later than window_start+3000ms
- last source-clock update no earlier than window_end-2000ms
- strictly after the previously consumed 2026-09-05 settled24 cohort
- choose the latest 24 eligible by window_end_ms, then write them in chronology order

Winner is stored only for post-hoc scoring. No strategy is executed here.
"""
from __future__ import annotations
import argparse, hashlib, json, lzma, sqlite3, zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TAPE_ROOT=ROOT/'data/execution_tape_v1/markets'
DB=ROOT/'data/target_wallet_official_v1.db'
OLD=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_latest_settled24_20260905_bundle.zip'
MIN_UPDATES=200; MIN_MATCHES=50; MAX_START_LAG_MS=3000; MAX_END_GAP_MS=2000; N=24

def sha256(p:Path):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()

def tape_quality(p:Path):
 with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
 m=d.get('market') or {}; title=str(m.get('title') or '')
 ups=d.get('updates') or []; src=[int(x[0]) for x in ups if isinstance(x,list) and len(x)>=2]
 end=int(m.get('window_end_ms') or 0); start=end-300000
 if not src:return None
 return {'title':title,'windowEndMs':end,'updates':len(ups),'matches':len(d.get('matches') or []),
         'firstSourceMs':min(src),'lastSourceMs':max(src),'startLagMs':min(src)-start,'endGapMs':end-max(src)}

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--manifest',required=True); a=ap.parse_args()
 out=Path(a.bundle).resolve(); man=Path(a.manifest).resolve()
 if out.exists() or man.exists():ap.error('Refusing overwrite')
 with zipfile.ZipFile(OLD) as z: old_rows=json.loads(z.read('cohort.json'))['rows']
 old_max_end=max(int(r['windowEndMs']) for r in old_rows)
 con=sqlite3.connect(DB)
 rows=con.execute("select market_id,window_end_ms,winner,resolved_at_ms,title from target_markets where asset='ETH' and status='SETTLED' and winner in ('UP','DOWN') and window_end_ms>? order by window_end_ms desc",(old_max_end,)).fetchall(); con.close()
 eligible=[]; rejected=[]
 for mid,end,winner,resolved,title in rows:
  p=TAPE_ROOT/f'{int(mid)}.json.xz'
  if not p.exists():rejected.append({'marketId':mid,'reason':'NO_TAPE'});continue
  try:q=tape_quality(p)
  except Exception as e:rejected.append({'marketId':mid,'reason':'TAPE_READ_ERROR','error':str(e)});continue
  reasons=[]
  if 'Ethereum Up or Down' not in q['title']:reasons.append('NOT_ETH_TITLE')
  if abs(int(q['windowEndMs'])-int(end))>1:reasons.append('WINDOW_END_MISMATCH')
  if q['updates']<MIN_UPDATES:reasons.append('TOO_FEW_UPDATES')
  if q['matches']<MIN_MATCHES:reasons.append('TOO_FEW_MATCHES')
  if q['startLagMs']>MAX_START_LAG_MS:reasons.append('LATE_START')
  if q['endGapMs']>MAX_END_GAP_MS:reasons.append('EARLY_END')
  if reasons:rejected.append({'marketId':mid,'reason':reasons,'quality':q});continue
  eligible.append({'marketId':int(mid),'winner':str(winner).upper(),'windowEndMs':int(end),'resolvedAtMs':int(resolved or 0),'title':title,
                   'tapePath':str(p.relative_to(ROOT)).replace('\\','/'),'tapeSha256':sha256(p),'quality':q})
  if len(eligible)>=N:break
 if len(eligible)<N:raise RuntimeError(f'Only {len(eligible)} eligible markets')
 selected=sorted(eligible[:N],key=lambda r:r['windowEndMs'])
 cohort={'rows':[{'marketId':r['marketId'],'winner':r['winner'],'split':'NEW24_20260906_GRADUATION','chronologyIndex':i,'windowEndMs':r['windowEndMs']} for i,r in enumerate(selected)]}
 out.parent.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_STORED) as z:
  z.writestr('cohort.json',json.dumps(cohort,ensure_ascii=False,indent=2))
  for r in selected:z.write(ROOT/r['tapePath'],f"tapes/{r['marketId']}.json.xz")
 manifest={'version':'ETH_NEW24_HFT_COHORT_V1','date':'2026-09-06','frozenBeforeCandidateExecution':True,'strategyOutcomeUsedForSelection':False,
           'selection':{'asset':'ETH','status':'SETTLED','winnerRequiredOnlyForPostHocScoring':True,'afterConsumedWindowEndMs':old_max_end,
                        'latestEligibleCount':N,'minUpdates':MIN_UPDATES,'minMatches':MIN_MATCHES,'maxStartLagMs':MAX_START_LAG_MS,'maxEndGapMs':MAX_END_GAP_MS},
           'markets':selected,'rejectedBeforeCutoff':rejected,'bundlePath':str(out.relative_to(ROOT)).replace('\\','/'),'bundleSha256':sha256(out),
           'guards':['chronology only; no PnL/winner direction used to rank/select','winner inaccessible to runtime actions','realistic execution tapes only','do not replace markets after candidate result']}
 man.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'ok':True,'markets':[r['marketId'] for r in selected],'windowRange':[selected[0]['windowEndMs'],selected[-1]['windowEndMs']],
                   'bundleSha256':manifest['bundleSha256'],'quality':[{k:r['quality'][k] for k in ('updates','matches','startLagMs','endGapMs')} for r in selected]},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
