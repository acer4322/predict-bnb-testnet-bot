from __future__ import annotations
import argparse,json,sqlite3,math,os,sys
from collections import defaultdict,deque
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from src.predict_bot.execution_tape_archive_v1 import load_archive
DB=ROOT/'data/wallet_maker_book_inference.db';OUTDIR=ROOT/'data/research/r4_v0/hourly';TICK=.01
TIME_PRICE=['seconds_left','best_bid','best_ask','mid','spread_ticks']
DEPTH=['bid_l1','ask_l1','bid_top3','ask_top3','bid_top5','ask_top5','bid_top10','ask_top10','sum_l1','min_l1','max_l1','abs_l1_imbalance','book_imb_l1','book_imb_top3','book_imb_top5','book_imb_top10','bid_levels','ask_levels','bid_l1_share_top5','ask_l1_share_top5','bid_add_1s','ask_add_1s','bid_remove_1s','ask_remove_1s','bid_add_3s','ask_add_3s','bid_remove_3s','ask_remove_3s','remove_imb_1s','remove_imb_3s','add_imb_1s','add_imb_3s']

def imb(a,b):return (a-b)/(a+b) if a+b>1e-12 else 0.
def qsum(d,ps):return sum(float(d.get(p,0.)) for p in ps)
def apply(book,chg):
 for k in ('bids','asks'):
  for x in (chg or {}).get(k,[]) or []:
   p=float(x[0]);a=float(x[2]);
   if a<=1e-12:book[k].pop(p,None)
   else:book[k][p]=a

def features(book,flow,t,end):
 bids,asks=book['bids'],book['asks'];bp=sorted(bids,reverse=True);ap=sorted(asks)
 if not bp or not ap:return None
 bb,ba=bp[0],ap[0];z={'seconds_left':(end-t)/1000.,'best_bid':bb,'best_ask':ba,'mid':(bb+ba)/2.,'spread_ticks':(ba-bb)/TICK,'bid_levels':len(bp),'ask_levels':len(ap)}
 for n in (1,3,5,10):
  bd=qsum(bids,bp[:n]);ad=qsum(asks,ap[:n]);z['bid_l1' if n==1 else f'bid_top{n}']=bd;z['ask_l1' if n==1 else f'ask_top{n}']=ad;z['book_imb_l1' if n==1 else f'book_imb_top{n}']=imb(bd,ad)
 z['sum_l1']=z['bid_l1']+z['ask_l1'];z['min_l1']=min(z['bid_l1'],z['ask_l1']);z['max_l1']=max(z['bid_l1'],z['ask_l1']);z['abs_l1_imbalance']=abs(z['book_imb_l1']);z['bid_l1_share_top5']=z['bid_l1']/z['bid_top5'] if z['bid_top5'] else 0.;z['ask_l1_share_top5']=z['ask_l1']/z['ask_top5'] if z['ask_top5'] else 0.
 for ms,s in ((1000,'1s'),(3000,'3s')):
  ba=aa=br=ar=0.;cut=t-ms
  for tt,x1,x2,x3,x4 in flow:
   if cut<=tt<=t:ba+=x1;aa+=x2;br+=x3;ar+=x4
  z[f'bid_add_{s}']=ba;z[f'ask_add_{s}']=aa;z[f'bid_remove_{s}']=br;z[f'ask_remove_{s}']=ar;z[f'remove_imb_{s}']=imb(br,ar);z[f'add_imb_{s}']=imb(ba,aa)
 return z

def select_markets(c,max_markets,market_offset=0):
 parent_max={int(r[0]):int(r[1]) for r in c.execute("select market_id,max(placement_first_ms) from maker_book_inference_v21_parent_lifecycles where placement_first_ms is not null and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 group by market_id")}
 mans={int(r[0]):str(r[1]) for r in c.execute("select market_id,archive_path from maker_execution_archive_manifest_v1")}
 inter=[(m,mans[m],parent_max[m]) for m in parent_max if m in mans]
 inter.sort(key=lambda x:(x[2],x[0]),reverse=True)
 return inter[market_offset:market_offset+max_markets]

def build(max_markets,market_offset=0):
 c=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True,timeout=30);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');mans=select_markets(c,max_markets,market_offset);mids=[m for m,_,_ in mans];placements=defaultdict(list)
 if mids:
  for i in range(0,len(mids),300):
   blk=mids[i:i+300];q=','.join('?'*len(blk));
   for r in c.execute(f"select market_id,placement_first_ms from maker_book_inference_v21_parent_lifecycles where market_id in ({q}) and placement_first_ms is not null and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 order by market_id,placement_first_ms",blk):placements[int(r[0])].append(int(r[1]))
 c.close();rows=[];archive_errors=[]
 import bisect
 for ix,(mid,pathstr,_pmax) in enumerate(reversed(mans),1):
  p=Path(pathstr);p=p if p.exists() else ROOT/'data/execution_tape_v1/markets'/f'{mid}.json.xz'
  try:d=load_archive(p)
  except Exception as e:archive_errors.append({'marketId':mid,'error':f'{type(e).__name__}:{e}'});continue
  ups=sorted(d.get('updates') or [],key=lambda r:(int(r[0]),int(r[1])));end=int((d.get('market') or {}).get('window_end_ms') or 0);book={'bids':{},'asks':{}};flow=deque();samples={}
  for u in ups:
   t=int(u[0]);is_cp=int(u[3]);chg=u[6] or {}
   if is_cp and u[4] is not None and u[5] is not None:book={'bids':{float(k):float(v) for k,v in (u[4] or {}).items()},'asks':{float(k):float(v) for k,v in (u[5] or {}).items()}}
   else:
    ba=aa=br=ar=0.
    for x in chg.get('bids',[]) or []:dv=float(x[3]);ba+=max(0.,dv);br+=max(0.,-dv)
    for x in chg.get('asks',[]) or []:dv=float(x[3]);aa+=max(0.,dv);ar+=max(0.,-dv)
    apply(book,chg);flow.append((t,ba,aa,br,ar))
   while flow and flow[0][0]<t-3000:flow.popleft()
   ft=features(book,flow,t,end)
   if ft is not None:samples[t//1000]=(t,ft)
  ps=placements.get(mid,[])
  for t,ft in samples.values():
   if not (0<=ft['seconds_left']<=310):continue
   j=bisect.bisect_right(ps,t);nxt=ps[j] if j<len(ps) else None;rr={'market_id':mid,'sample_ms':t,'next_placement_ms':nxt,**ft}
   for h in (1,2,5):rr[f'y{h}']=int(nxt is not None and t<nxt<=t+h*1000)
   rows.append(rr)
  if ix%50==0:print(json.dumps({'marketsDone':ix,'rows':len(rows),'placementMarketsSeen':sum(bool(placements.get(m)) for m,_,_ in reversed(mans[:ix]))}),flush=True)
 df=pd.DataFrame(rows).sort_values(['sample_ms','market_id']).reset_index(drop=True);return df,{'selectedMarkets':len(mans),'conditioning':'markets with at least one high-confidence inferred Maker placement and archived L2','archiveErrors':archive_errors,'placementMarketsSelected':sum(bool(placements.get(m)) for m,_,_ in mans)}

def safe_auc(y,p):return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None
def make_folds(df):
 mm=df.groupby('market_id').sample_ms.min().sort_values();ms=[int(x) for x in mm.index];n=len(ms);test=max(15,min(30,n//7));mintr=max(50,n//3);last=max(mintr,n-test);starts=sorted(set(int(round(mintr+i*(last-mintr)/3)) for i in range(4)));return [(ms[:s],ms[s:min(n,s+test)]) for s in starts if len(ms[s:min(n,s+test)])>=10]
def evaluate(df,label,fs):
 rs=[]
 for i,(trm,tem) in enumerate(make_folds(df)):
  tr=df[df.market_id.isin(trm)];te=df[df.market_id.isin(tem)];ytr=tr[label].astype(int);yte=te[label].astype(int)
  if ytr.sum()==0 or yte.sum()==0:continue
  m=HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=160,l2_regularization=3.,class_weight='balanced',random_state=70+i).fit(tr[fs],ytr);p=m.predict_proba(te[fs])[:,1];prior=np.full(len(yte),float(ytr.mean()));bl=float(log_loss(yte,prior,labels=[0,1]));ml=float(log_loss(yte,p,labels=[0,1]));rs.append({'fold':i,'trainMarkets':len(trm),'testMarkets':len(tem),'testN':len(te),'positiveRate':float(yte.mean()),'auc':safe_auc(yte,p),'ap':float(average_precision_score(yte,p)),'logLoss':ml,'priorLogLoss':bl,'logLossLift':bl-ml})
 def av(k):
  a=[r[k] for r in rs if r.get(k) is not None];return float(np.mean(a)) if a else None
 return {'features':fs,'folds':rs,'meanAuc':av('auc'),'meanAP':av('ap'),'meanLogLoss':av('logLoss'),'meanLogLossLift':av('logLossLift')}
def desc(df,h):
 p=df[df[f'y{h}']==1];n=df[df[f'y{h}']==0];ks=['sum_l1','min_l1','abs_l1_imbalance','bid_l1','ask_l1','bid_top5','ask_top5','bid_remove_1s','ask_remove_1s','bid_add_1s','ask_add_1s','spread_ticks']
 return {k:{'posMedian':float(p[k].median()),'negMedian':float(n[k].median()),'posMean':float(p[k].mean()),'negMean':float(n[k].mean())} for k in ks}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--max-markets',type=int,default=240);ap.add_argument('--market-offset',type=int,default=0);a=ap.parse_args();df,meta=build(a.max_markets,a.market_offset);OUTDIR.mkdir(parents=True,exist_ok=True);tag=f'o{a.market_offset}_m{a.max_markets}';csvp=OUTDIR/f'r4_target_depth_archive_hazard_v1_{tag}.csv';df.to_csv(csvp,index=False);sets={'TIME_PRICE':TIME_PRICE,'DEPTH_ONLY':DEPTH,'TIME_PRICE_DEPTH':TIME_PRICE+DEPTH};tasks={}
 for h in (1,2,5):
  rr={k:evaluate(df,f'y{h}',v) for k,v in sets.items()};b=rr['TIME_PRICE'];x=rr['TIME_PRICE_DEPTH'];rr['incrementDepthOverTimePrice']={'deltaMeanAuc':None if b['meanAuc'] is None or x['meanAuc'] is None else x['meanAuc']-b['meanAuc'],'deltaMeanAP':None if b['meanAP'] is None or x['meanAP'] is None else x['meanAP']-b['meanAP'],'deltaMeanLogLossLift':None if b['meanLogLossLift'] is None or x['meanLogLossLift'] is None else x['meanLogLossLift']-b['meanLogLossLift']};rr['descriptive']=desc(df,h);tasks[f'next{h}s']=rr
 report={'version':'R4_TARGET_DEPTH_ARCHIVE_HAZARD_V1','researchOnly':True,'question':'Does archived native public depth/flow add blocked-walk-forward predictive value for inferred Target Maker placement hazard beyond seconds-left + native touch price?','coverage':{**meta,'rows':len(df),'markets':int(df.market_id.nunique()),'positiveRates':{f'next{h}s':float(df[f'y{h}'].mean()) for h in (1,2,5)}},'guards':{'inferredPlacementNotPrivateGroundTruth':True,'archivedL2SourceTimestampAndPlacementClockAligned':True,'futurePlacementSidePriceUnused':True,'winnerUnused':True,'noThresholdSweep':True,'noLiveChanges':True},'featureSets':sets,'tasks':tasks,'interpretationRule':'Positive OOF lift TIME_PRICE_DEPTH vs TIME_PRICE supports depth/queue as an incremental trigger; weak/no lift supports depth mainly as quote-level selector.'};out=OUTDIR/f'r4_target_depth_archive_hazard_20260826_v1_{tag}.json';out.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'coverage':report['coverage'],'increments':{k:v['incrementDepthOverTimePrice'] for k,v in tasks.items()}},ensure_ascii=False))
if __name__=='__main__':main()
