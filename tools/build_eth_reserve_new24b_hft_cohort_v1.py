"""Freeze a second unseen ETH5M 24-market graduation reserve cohort before V2 execution.
Same quality rules as ETH_NEW24_HFT_COHORT_V1, but select the latest 24 eligible strictly before the already-frozen NEW24-A first window and strictly after the old consumed 20260905 cohort.
No strategy is executed and no PnL is used for selection.
"""
from __future__ import annotations
import hashlib,json,lzma,sqlite3,zipfile,argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TAPES=ROOT/'data/execution_tape_v1/markets'; DB=ROOT/'data/target_wallet_official_v1.db'
OLD=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_latest_settled24_20260905_bundle.zip'
NEWA=ROOT/'data/research/r4_v0/p0_provenance_v1/ETH_NEW24_HFT_COHORT_V1_MANIFEST_20260906.json'
N=24;MIN_UPDATES=200;MIN_MATCHES=50;MAX_START_LAG=3000;MAX_END_GAP=2000

def sha(p):
 h=hashlib.sha256();
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def quality(p):
 d=json.load(lzma.open(p,'rt',encoding='utf-8'));m=d['market'];u=d.get('updates') or [];src=[int(x[0]) for x in u if isinstance(x,list) and len(x)>1];end=int(m['window_end_ms']);start=end-300000
 if not src:return None
 return {'title':str(m.get('title') or ''),'windowEndMs':end,'updates':len(u),'matches':len(d.get('matches') or []),'firstSourceMs':min(src),'lastSourceMs':max(src),'startLagMs':min(src)-start,'endGapMs':end-max(src)}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--manifest',required=True);a=ap.parse_args();out=Path(a.bundle).resolve();man=Path(a.manifest).resolve()
 if out.exists() or man.exists():ap.error('refuse overwrite')
 with zipfile.ZipFile(OLD) as z: old=json.loads(z.read('cohort.json'))['rows']; oldmax=max(int(r['windowEndMs']) for r in old)
 newa=json.load(open(NEWA,encoding='utf-8')); cutoff=min(int(r['windowEndMs']) for r in newa['markets'])
 con=sqlite3.connect(DB);rs=con.execute("select market_id,window_end_ms,winner,resolved_at_ms,title from target_markets where asset='ETH' and status='SETTLED' and winner in ('UP','DOWN') and window_end_ms>? and window_end_ms<? order by window_end_ms desc",(oldmax,cutoff)).fetchall();con.close()
 eligible=[];rejected=[]
 for mid,end,w,res,title in rs:
  p=TAPES/f'{mid}.json.xz'
  if not p.exists():rejected.append({'marketId':mid,'reason':'NO_TAPE'});continue
  try:q=quality(p)
  except Exception as e:rejected.append({'marketId':mid,'reason':'READ','error':str(e)});continue
  bad=[]
  if q is None:bad.append('NO_UPDATES')
  else:
   if 'Ethereum Up or Down' not in q['title']:bad.append('NOT_ETH')
   if q['updates']<MIN_UPDATES:bad.append('TOO_FEW_UPDATES')
   if q['matches']<MIN_MATCHES:bad.append('TOO_FEW_MATCHES')
   if q['startLagMs']>MAX_START_LAG:bad.append('LATE_START')
   if q['endGapMs']>MAX_END_GAP:bad.append('EARLY_END')
   if abs(q['windowEndMs']-int(end))>1:bad.append('WINDOW_MISMATCH')
  if bad:rejected.append({'marketId':mid,'reason':bad,'quality':q});continue
  eligible.append({'marketId':int(mid),'winner':str(w).upper(),'windowEndMs':int(end),'resolvedAtMs':int(res or 0),'title':title,'tapePath':str(p.relative_to(ROOT)).replace('\\','/'),'tapeSha256':sha(p),'quality':q})
  if len(eligible)>=N:break
 if len(eligible)<N:raise RuntimeError(f'only {len(eligible)} eligible')
 selected=sorted(eligible[:N],key=lambda r:r['windowEndMs'])
 cohort={'rows':[{'marketId':r['marketId'],'winner':r['winner'],'split':'RESERVE_NEW24B_20260906','chronologyIndex':i,'windowEndMs':r['windowEndMs']} for i,r in enumerate(selected)]}
 out.parent.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_STORED) as z:
  z.writestr('cohort.json',json.dumps(cohort,indent=2));
  for r in selected:z.write(ROOT/r['tapePath'],f"tapes/{r['marketId']}.json.xz")
 m={'version':'ETH_RESERVE_NEW24B_HFT_COHORT_V1','date':'2026-09-06','frozenBeforeV2Execution':True,'strategyOutcomeUsedForSelection':False,'selection':{'afterOldConsumedEndMs':oldmax,'strictlyBeforeNewAFirstEndMs':cutoff,'latestEligibleCount':N,'minUpdates':MIN_UPDATES,'minMatches':MIN_MATCHES,'maxStartLagMs':MAX_START_LAG,'maxEndGapMs':MAX_END_GAP},'markets':selected,'rejectedBeforeCutoff':rejected,'bundleSha256':sha(out),'guards':['do not inspect candidate V2 results on this cohort before formal attempt','do not replace markets after V2 result','winner posthoc only']};man.write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'ids':[r['marketId'] for r in selected],'window':[selected[0]['windowEndMs'],selected[-1]['windowEndMs']],'sha':m['bundleSha256']},ensure_ascii=False))
if __name__=='__main__':main()
