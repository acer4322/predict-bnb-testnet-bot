from __future__ import annotations
import json, math, sys
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss
import joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import load_reference_paper, load_public_snapshots
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from src.predict_bot import unified_controller_paper_v2 as mod
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
OUT=D/'r2_placement_adverse_teacher_v0_dataset.csv'; REP=D/'r2_placement_adverse_teacher_v0_report.json'; ART=D/'r2_placement_adverse_teacher_v0.joblib'
REASON_FILES=[D/'r2_exact_intent_reason_random30_v0.json',D/'r2_exact_intent_reason_forward20_v0.json']
MAIN_REASONS={'MAKER_HAZARD','MAKER_BURST'}
PUBLIC=['secondsLeft','directionScore','spotReturn1sBps','spotReturn3sBps','spotQueueImbalance','spotTakerImbalance1s','futuresReturn1sBps','futuresReturn3sBps','futuresQueueImbalance','futuresTakerImbalance1s','predictUpMid','spotMinusStrikeBps','chainlinkMinusStrikeBps']
FEATURES=['side_is_up','reason_is_burst','quote_price','side_bid','side_ask','spread_ticks','quote_offset_ticks','direction_toward_side','spot1_toward_side','spot3_toward_side','spot_queue_toward_side','spot_taker_toward_side','fut1_toward_side','fut3_toward_side','fut_queue_toward_side','fut_taker_toward_side']+PUBLIC

def reasons_by_market():
 out={}
 for p in REASON_FILES:
  if not p.exists(): continue
  d=json.loads(p.read_text(encoding='utf-8'))
  for r in d.get('rows',[]): out[int(r['marketId'])]=list(r.get('makerIntents',[]))
 return out

def choose_idx(n,k=20):
 if n<=k:return list(range(n))
 return sorted(set(int(round(x)) for x in np.linspace(0,n-1,k)))

def snap_before(snaps,t):
 z=[s for s in snaps if int(s.get('sampledAtMs') or 0)<=t]
 return z[-1] if z else (snaps[0] if snaps else {})

def side_mid(book,side):
 bf=mod.outcome_book(book.book,None)
 if not bf:return None
 return (float(bf['up_bid'])+float(bf['up_ask']))/2 if side=='UP' else (float(bf['down_bid'])+float(bf['down_ask']))/2

def sim_one(events,side,px,qty,start):
 bt=ex.new_bt(events,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk'); ex.initialize_bt(bt)
 try:
  ex.advance_to(bt,start); rc=ex.submit_native(bt,1,side,px,qty); ex.advance_to(bt,start+5000); s=ex.order_snapshot(bt,1)
  cum=float(s.get('cumExecQty') or 0.0); ep=s.get('execPrice'); fillpx=px
  if ep is not None and math.isfinite(float(ep)): fillpx=float(ep) if side=='UP' else 1.0-float(ep)
  fm=int((s.get('exchangeTs') or (start+5000)*1_000_000)//1_000_000) if cum>0 else None
  return rc,cum,fillpx,fm,str(s.get('status') or 'NONE')
 finally: bt.close()

def fnum(x):
 try:
  v=float(x);return v if math.isfinite(v) else math.nan
 except:return math.nan

def build():
 rbm=reasons_by_market(); allrows=[]
 mids30=[int(r['marketId']) for r in json.loads((D/'r2_exact_intent_reason_random30_v0.json').read_text(encoding='utf-8'))['rows']]
 mids20=[int(r['marketId']) for r in json.loads((D/'r2_exact_intent_reason_forward20_v0.json').read_text(encoding='utf-8'))['rows']]
 mids=mids30+mids20
 for mi,mid in enumerate(mids,1):
  paper=load_reference_paper(mid); rr=rbm.get(mid,[]); snaps=load_public_snapshots(mid); events,_,_=tape_v1.build_archive_events(mid,trade_offset='mid'); book=mod.PublicBookTailer(mod.BOOK_DB)
  try:
   orders=list(paper['orders']); n=min(len(orders),len(rr)); cand=[i for i in range(n) if str(rr[i].get('reason') or '') in MAIN_REASONS]
   pick=[cand[j] for j in choose_idx(len(cand),20)] if cand else []
   for i in pick:
    o=orders[i]; ri=rr[i]; side=str(o['side']).upper(); px=float(o['price']); qty=float(o.get('shares') or 18.0); t=int(o['placed_at_ms']); ps=snap_before(snaps,t)
    book.reset(mid,t); bf=mod.outcome_book(book.book,None) or {}; bid=fnum(bf.get('up_bid') if side=='UP' else bf.get('down_bid')); ask=fnum(bf.get('up_ask') if side=='UP' else bf.get('down_ask')); sign=1.0 if side=='UP' else -1.0
    rc,cum,fillpx,fm,status=sim_one(events,side,px,qty,t); m1=m3=None
    if cum>0 and fm is not None:
      book.reset(mid,fm); book.advance(mid,fm+1000); x=side_mid(book,side); m1=((x-fillpx)/mod.GRID) if x is not None else None
      book.reset(mid,fm); book.advance(mid,fm+3000); x=side_mid(book,side); m3=((x-fillpx)/mod.GRID) if x is not None else None
    row={'marketId':mid,'cohort':'train' if mi<=15 else 'validation' if mi<=30 else 'forward','placedAtMs':t,'side':side,'reason':str(ri.get('reason')),'qty':qty,'fillShares5s':cum,'filled5s':int(cum>0),'fillPrice':fillpx if cum>0 else None,'fillMs':fm,'markout1sTicks':m1,'markout3sTicks':m3,'labelAdverse1s':int(m1<0) if m1 is not None else None,'labelAdverse3s':int(m3<0) if m3 is not None else None,'side_is_up':float(side=='UP'),'reason_is_burst':float(str(ri.get('reason'))=='MAKER_BURST'),'quote_price':px,'side_bid':bid,'side_ask':ask,'spread_ticks':(ask-bid)/mod.GRID if math.isfinite(bid) and math.isfinite(ask) else math.nan,'quote_offset_ticks':(bid-px)/mod.GRID if math.isfinite(bid) else math.nan}
    for c in PUBLIC: row[c]=ps.get(c)
    row.update({'direction_toward_side':fnum(ps.get('directionScore'))*sign,'spot1_toward_side':fnum(ps.get('spotReturn1sBps'))*sign,'spot3_toward_side':fnum(ps.get('spotReturn3sBps'))*sign,'spot_queue_toward_side':fnum(ps.get('spotQueueImbalance'))*sign,'spot_taker_toward_side':fnum(ps.get('spotTakerImbalance1s'))*sign,'fut1_toward_side':fnum(ps.get('futuresReturn1sBps'))*sign,'fut3_toward_side':fnum(ps.get('futuresReturn3sBps'))*sign,'fut_queue_toward_side':fnum(ps.get('futuresQueueImbalance'))*sign,'fut_taker_toward_side':fnum(ps.get('futuresTakerImbalance1s'))*sign})
    allrows.append(row)
  finally: book.close()
  print(json.dumps({'progressMarket':mi,'marketId':mid,'rows':len(allrows)},ensure_ascii=False),flush=True)
 pd.DataFrame(allrows).to_csv(OUT,index=False); return pd.DataFrame(allrows)

def met(y,p):
 y=np.asarray(y,dtype=int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7)
 return {'n':len(y),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum() else None,'logLoss':float(log_loss(y,p,labels=[0,1]))}

def main():
 df=build(); z=df[df.labelAdverse1s.notna()].copy(); tr=z[z.cohort=='train']; va=z[z.cohort=='validation']; te=z[z.cohort=='forward']
 model=Pipeline([('imp',SimpleImputer(strategy='median')),('clf',HistGradientBoostingClassifier(max_depth=3,learning_rate=.05,max_iter=160,l2_regularization=3.0,min_samples_leaf=15,random_state=20260822))]);model.fit(tr[FEATURES],tr.labelAdverse1s.astype(int))
 metrics={}
 for name,x in [('train',tr),('validation',va),('forward',te)]: metrics[name]=met(x.labelAdverse1s.astype(int),model.predict_proba(x[FEATURES])[:,1]) if len(x) else None
 joblib.dump({'version':'R2_PLACEMENT_ADVERSE_TEACHER_V0','model':model,'features':FEATURES,'trainingMarkets':sorted(tr.marketId.unique().tolist()),'runtimeTargetAllowed':False,'winnerPnlAllowed':False},ART)
 rep={'version':'R2_PLACEMENT_ADVERSE_TEACHER_V0','researchOnly':True,'datasetRows':len(df),'filledRows':len(z),'cohortRows':df.cohort.value_counts().to_dict(),'filledByCohort':z.cohort.value_counts().to_dict(),'metrics':metrics,'features':FEATURES,'guardrails':['Placement-time strict-past inputs only','HftBacktest actual fill within 5s','Future +1s public-book markout label only','No Target/winner/PnL feature or label','No threshold sweep']};REP.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False))
if __name__=='__main__':main()
