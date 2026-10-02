from __future__ import annotations
import json,sqlite3,zlib,math,statistics
from pathlib import Path
from scipy.stats import spearmanr
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/wallet_maker_book_inference.db'; SRC=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r2_queue_option_counterfactual_v1.json'; OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r2_queue_progress_proxy_v0.json'
def dec(b): return json.loads(zlib.decompress(b).decode()) if b else None
def native(side,p): return ('bids',round(p,2)) if side=='UP' else ('asks',round(1.0-p,2))
def replay(con,mid,submit,cand,side,p):
 rows=con.execute('select id,received_at_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? and received_at_ms<=? order by received_at_ms,id',(mid,cand)).fetchall()
 state={'bids':{},'asks':{}}; nside,npx=native(side,p); init=None; cur=None; neg=0.; neg_events=0; pos=0.; pos_events=0
 for r in rows:
  t=int(r['received_at_ms'])
  if int(r['is_checkpoint'] or 0):
   state={'bids':{float(k):float(v) for k,v in (dec(r['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(r['native_asks_z']) or {}).items()}}
  else:
   ch=dec(r['changes_z']) or {}
   for sd in ('bids','asks'):
    for z in ch.get(sd,[]) or []:
     px=float(z['price']); before=float(z.get('before',state[sd].get(px,0.0))); after=float(z.get('after',0.0)); delta=float(z.get('delta',after-before))
     if submit < t <= cand and sd==nside and abs(px-npx)<1e-9:
      if delta<0: neg += -delta; neg_events += 1
      elif delta>0: pos += delta; pos_events += 1
     if after<=1e-12: state[sd].pop(px,None)
     else: state[sd][px]=after
  if t<=submit: init=float(state[nside].get(npx,0.0))
 cur=float(state[nside].get(npx,0.0))
 return {'nativeSide':nside,'nativePrice':npx,'initialVisibleDepth':init,'currentVisibleDepth':cur,'negativeDepletion':neg,'negativeDepletionEvents':neg_events,'positiveAdditions':pos,'positiveAdditionEvents':pos_events,'depletionOverInitial':(neg/init if init and init>1e-9 else None),'netDepletionOverInitial':((neg-pos)/init if init and init>1e-9 else None)}
def main():
 d=json.load(open(SRC)); con=sqlite3.connect(DB); con.row_factory=sqlite3.Row; out=[]
 for r in d['rows']:
  if not r.get('eligible') or not r.get('queueFeatures'): continue
  f=r['queueFeatures']; cand=int(r['candidateAtMs']); submit=cand-int(f['orderAgeMs']); q=replay(con,int(r['marketId']),submit,cand,str(r['side']),float(f['orderPrice'])); out.append({'marketId':int(r['marketId']),'queueValueTargetErrorArea':float(r['queueValueTargetErrorArea']),'keepBetter':int(float(r['queueValueTargetErrorArea'])>1e-9),'reinsertBetter':int(float(r['queueValueTargetErrorArea'])<-1e-9),'neutral':int(abs(float(r['queueValueTargetErrorArea']))<=1e-9),'orderAgeMs':int(f['orderAgeMs']),'quoteOffsetTicks':f.get('quoteOffsetTicks'),**q})
 con.close()
 corr={}
 for k in ['initialVisibleDepth','currentVisibleDepth','negativeDepletion','positiveAdditions','depletionOverInitial','netDepletionOverInitial','orderAgeMs','quoteOffsetTicks']:
  z=[(float(x[k]),float(x['queueValueTargetErrorArea'])) for x in out if x.get(k) is not None and math.isfinite(float(x[k]))]
  if len(z)>3:
   rr=spearmanr([a for a,b in z],[b for a,b in z]); corr[k]={'n':len(z),'rho':float(rr.statistic),'p':float(rr.pvalue)}
 groups={'KEEP':[x for x in out if x['keepBetter']],'REINSERT':[x for x in out if x['reinsertBetter']],'NEUTRAL':[x for x in out if x['neutral']]}; meds={}
 for g,a in groups.items():
  meds[g]={}
  for k in ['initialVisibleDepth','currentVisibleDepth','negativeDepletion','positiveAdditions','depletionOverInitial','netDepletionOverInitial','orderAgeMs','quoteOffsetTicks']:
   z=[float(x[k]) for x in a if x.get(k) is not None and math.isfinite(float(x[k]))]; meds[g][k]=statistics.median(z) if z else None
 rep={'version':'R2_QUEUE_PROGRESS_PROXY_V0','researchOnly':True,'note':'Predict L2 negative depletion mixes trades/cancels; diagnostic queue-progress proxy only. received_at_ms matches HftBacktest replay clock.','n':len(out),'correlationsVsReinsertMinusKeepTrackingArea':corr,'groupMedians':meds,'rows':out}; OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps({k:rep[k] for k in ['n','correlationsVsReinsertMinusKeepTrackingArea','groupMedians']},indent=2))
if __name__=='__main__': main()
