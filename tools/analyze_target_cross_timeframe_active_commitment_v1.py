from __future__ import annotations
import argparse,bisect,json,sqlite3
from collections import defaultdict,deque
from pathlib import Path
import numpy as np,pandas as pd

ROOT=Path('.')
BASE=ROOT/'data/research/r4_v0/p0_provenance_v1'
XCSV=BASE/'TARGET_CROSS_TIMEFRAME_ACTION_VALUE_DECISION_SLICE_V1_20260907.csv'
XJSON=BASE/'TARGET_CROSS_TIMEFRAME_ACTION_VALUE_DECISION_SLICE_V1_20260907.json'
TARGET=ROOT/'data/target_wallet_official_v1.db'
PUBLIC=ROOT/'data/public_source_snapshot_archive_v2.db'
OUTDIR=ROOT/'data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907'
EPS=1e-9;SEED=20260907

def ro(p):
 c=sqlite3.connect(f'file:{Path(p).resolve().as_posix()}?mode=ro',uri=True,timeout=30);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');return c

def side_sign(s):return 1 if s=='UP' else -1 if s=='DOWN' else 0
def opp(s):return 'DOWN' if s=='UP' else 'UP'

def getv(j,*keys):
 for k in keys:
  if k in j and j[k] is not None:return j[k]
 return None

def reconstruct_actions(tc,mid):
 ev=list(tc.execute("select id,event_ms,role,side,price,shares,order_hash from wallet_shadow_target_events where market_id=? and asset='BTC' and role in ('MAKER','TAKER') and side in ('UP','DOWN') order by event_ms,id",(mid,)))
 by=defaultdict(list)
 for e in ev:by[int(e['event_ms'])].append(e)
 q={'UP':deque(),'DOWN':deque()};outstanding={'UP':0.0,'DOWN':0.0};up=down=cost=0.0;rows=[];composite_generation=0
 for t,legs in sorted(by.items()):
  pre_up,pre_down,pre_cost=up,down,cost;rem={'UP':0.0,'DOWN':0.0};maker={'UP':0.0,'DOWN':0.0};taker={'UP':0.0,'DOWN':0.0};in_up=in_down=0.0
  for e in legs:
   s=str(e['side']);qty=float(e['shares']);px=float(e['price']);rem[s]+=qty;cost+=px*qty
   if s=='UP':up+=qty;in_up+=qty
   else:down+=qty;in_down+=qty
   (maker if str(e['role'])=='MAKER' else taker)[s]+=qty
  repair={'UP':0.0,'DOWN':0.0}
  for pay in ('UP','DOWN'):
   need=rem[pay];dq=q[opp(pay)]
   while need>EPS and dq:
    lot=dq[0];take=min(need,lot);dq[0]-=take;outstanding[opp(pay)]-=take;repair[pay]+=take;need-=take
    if dq[0]<=EPS:dq.popleft()
   rem[pay]=need
  pair=min(rem['UP'],rem['DOWN']);rem['UP']-=pair;rem['DOWN']-=pair;birth={'UP':0.0,'DOWN':0.0}
  for s in ('UP','DOWN'):
   if rem[s]>EPS:q[s].append(rem[s]);outstanding[s]+=rem[s];birth[s]=rem[s]
  rq=repair['UP']+repair['DOWN'];bq=birth['UP']+birth['DOWN']
  if rq>EPS and bq>EPS:role='COMPOSITE_CROSSING';composite_generation+=1
  elif rq>EPS:role='REPAIR_ONLY'
  elif bq>EPS:role='CLEAN_AGGREGATE_EXPAND'
  else:role='PAIR_ONLY_OR_NET_NEUTRAL'
  bside='UP' if birth['UP']>EPS and birth['DOWN']<=EPS else 'DOWN' if birth['DOWN']>EPS and birth['UP']<=EPS else None
  mqty=maker['UP']+maker['DOWN'];tqty=taker['UP']+taker['DOWN'];route='MIXED' if mqty>EPS and tqty>EPS else 'MAKER_ONLY' if mqty>EPS else 'TAKER_ONLY'
  rows.append({'market_id':mid,'event_ms':t,'pre_up':pre_up,'pre_down':pre_down,'pre_net':pre_up-pre_down,'pre_cost':pre_cost,'post_up':up,'post_down':down,'post_net':up-down,'repair_qty':rq,'birth_qty':bq,'birth_side':bside,'economic_role':role,'route_mix':route,'maker_up_qty':maker['UP'],'maker_down_qty':maker['DOWN'],'taker_up_qty':taker['UP'],'taker_down_qty':taker['DOWN'],'composite_generation':composite_generation})
 return pd.DataFrame(rows)

def load_public(pc,mid):
 rows=[]
 for r in pc.execute('select sampled_at_ms,snapshot_json from public_source_snapshots_v2 where market_id=? order by sampled_at_ms,id',(mid,)):
  try:j=json.loads(str(r['snapshot_json']))
  except:continue
  if not isinstance(j,dict):continue
  rows.append({'checkpoint_ms':int(r['sampled_at_ms']),'predict_up_mid':getv(j,'predictUpMid','predict_up_mid'),'predict_down_mid':getv(j,'predictDownMid','predict_down_mid'),'spot_minus_strike_bps':getv(j,'spotMinusStrikeBps','spot_minus_strike_bps'),'spot_queue_imbalance':getv(j,'spotQueueImbalance','spot_queue_imbalance'),'futures_queue_imbalance':getv(j,'futuresQueueImbalance','futures_queue_imbalance'),'spot_taker_imbalance_1s':getv(j,'spotTakerImbalance1s','spot_taker_imbalance_1s'),'futures_taker_imbalance_1s':getv(j,'futuresTakerImbalance1s','futures_taker_imbalance_1s'),'spot_return_1s_bps':getv(j,'spotReturn1sBps','spot_return_1s_bps'),'futures_return_1s_bps':getv(j,'futuresReturn1sBps','futures_return_1s_bps'),'seconds_left':getv(j,'secondsLeft','seconds_left')})
 return pd.DataFrame(rows)

def state_at(actions,t):
 if not len(actions):return None
 ts=actions.event_ms.to_numpy(np.int64);i=np.searchsorted(ts,int(t),side='left')-1
 return actions.iloc[i] if i>=0 else None

def clean_anchor_at(actions,t):
 z=actions[(actions.event_ms<t)&actions.economic_role.eq('CLEAN_AGGREGATE_EXPAND')&actions.birth_side.notna()]
 return z.iloc[-1] if len(z) else None

def higher_features(xrows,frame,t,anchor):
 z=xrows[(xrows.frame==frame)&(xrows.window_start_ms<=t)&(xrows.window_end_ms>t)&(xrows.t<t)]
 if not len(z):return {'available':0,'align':np.nan,'age_ms':np.nan,'anchor_support':np.nan,'post_net_abs':np.nan}
 r=z.sort_values('t').iloc[-1];postnet=float(r['net'])+side_sign(r['current_side'])*float(r['action_shares']);ps=1 if postnet>EPS else -1 if postnet<-EPS else 0;a=side_sign(anchor)
 anchor_mid=float(r['up_mid']) if anchor=='UP' else float(r['down_mid']);return {'available':1,'align':1.0 if ps and ps==a else 0.0 if ps else np.nan,'age_ms':float(t-r['t']),'anchor_support':anchor_mid-.5,'post_net_abs':abs(postnet)}

def build_entries(actions,cp,wend):
 actions=actions.sort_values('event_ms').reset_index(drop=True); cp=cp.sort_values('checkpoint_ms').reset_index(drop=True)
 ats=set(actions.event_ms.astype(int)); rows=[]; prevkey=None; seen=False; c2run=0; ai=-1; st=None; anchor=None; generation=0
 # Strict-past linear chronology: advance only actions with event_ms < checkpoint.
 for r in cp.itertuples():
  t=int(r.checkpoint_ms)
  if t>=wend: break
  while ai+1<len(actions) and int(actions.iloc[ai+1].event_ms)<t:
   ai+=1; st=actions.iloc[ai]
   if st.economic_role=='CLEAN_AGGREGATE_EXPAND' and pd.notna(st.birth_side): anchor=str(st.birth_side)
   if st.economic_role=='COMPOSITE_CROSSING': generation+=1
  if t in ats: continue
  if st is None or anchor is None or pd.isna(r.seconds_left) or float(r.seconds_left)<=0: continue
  sg=side_sign(anchor); net=float(st.post_net)
  pred=sg*(float(r.predict_up_mid)-float(r.predict_down_mid))/2 if pd.notna(r.predict_up_mid) and pd.notna(r.predict_down_mid) else np.nan
  strike=sg*float(r.spot_minus_strike_bps) if pd.notna(r.spot_minus_strike_bps) else np.nan
  c1=np.isfinite(pred) and pred<0; c2=bool(c1 and np.isfinite(strike) and strike<0); key=(anchor,generation)
  if not c1: prevkey=None; seen=False; c2run=0; continue
  if key!=prevkey: seen=False; c2run=0
  if c2:
   c2run+=1
   if not seen and np.sign(net)==sg and abs(net)>EPS:
    rows.append({'market_id':int(st.market_id),'entry_ms':t,'anchor':anchor,'pre_net':net,'pre_debt':abs(net),'predict_support':pred,'strike_support':strike,'seconds_left':float(r.seconds_left),'spot_queue_imbalance':r.spot_queue_imbalance,'futures_queue_imbalance':r.futures_queue_imbalance,'spot_taker_imbalance_1s':r.spot_taker_imbalance_1s,'futures_taker_imbalance_1s':r.futures_taker_imbalance_1s,'spot_return_1s_bps':r.spot_return_1s_bps,'futures_return_1s_bps':r.futures_return_1s_bps})
    seen=True
  else: c2run=0
  prevkey=key
 return rows

def label_entries(entries,actions):
 out=[]
 for e in entries:
  t=e['entry_ms'];f=actions[(actions.event_ms>t)&(actions.event_ms<=t+5000)].sort_values('event_ms');same=f[(f.economic_role=='CLEAN_AGGREGATE_EXPAND')&(f.birth_side==e['anchor'])]
  tak=same[same.route_mix=='TAKER_ONLY']; first=f.iloc[0] if len(f) else None
  active_lowers=[]
  for x in same.itertuples():
   maker_side=float(x.maker_up_qty if e['anchor']=='UP' else x.maker_down_qty)
   active_lowers.append(max(0.0,float(x.birth_qty)-maker_side))
  active_lb=float(sum(active_lowers)); first_active_lb=0.0
  if first is not None and first.economic_role=='CLEAN_AGGREGATE_EXPAND' and first.birth_side==e['anchor']:
   maker_side=float(first.maker_up_qty if e['anchor']=='UP' else first.maker_down_qty); first_active_lb=max(0.0,float(first.birth_qty)-maker_side)
  rec=dict(e);rec['any_taker_clean_5s']=int(len(tak)>0);rec['first_action_taker_clean_same']=int(first is not None and first.economic_role=='CLEAN_AGGREGATE_EXPAND' and first.birth_side==e['anchor'] and first.route_mix=='TAKER_ONLY');rec['any_active_clean_lower_5s']=int(active_lb>EPS);rec['first_action_active_clean_lower']=int(first_active_lb>EPS);rec['active_clean_birth_lower_5s']=active_lb;rec['first_active_clean_birth_lower']=first_active_lb;rec['first_action_role']=None if first is None else first.economic_role;rec['first_action_route']=None if first is None else first.route_mix;rec['taker_clean_birth_qty_5s']=float(tak.birth_qty.sum()) if len(tak) else 0.0
  out.append(rec)
 return out

def prep(train,test):
 a=np.asarray(train,float);b=np.asarray(test,float);keep=np.isfinite(a).any(0);a=a[:,keep];b=b[:,keep];am=~np.isfinite(a);bm=~np.isfinite(b);med=np.nanmedian(np.where(am,np.nan,a),axis=0);a=np.where(am,med,a);b=np.where(bm,med,b);mu=a.mean(0);sd=a.std(0);sd[sd<1e-8]=1;mask=am.any(0);return np.c_[np.ones(len(a)),np.clip((a-mu)/sd,-8,8),am[:,mask]],np.c_[np.ones(len(b)),np.clip((b-mu)/sd,-8,8),bm[:,mask]]
def fit(x,y,pen=10.):
 y=np.asarray(y,float);w=np.zeros(x.shape[1]);r=np.clip(y.mean(),1e-5,1-1e-5);w[0]=np.log(r/(1-r));reg=np.full(x.shape[1],pen);reg[0]=0
 for _ in range(40):
  p=1/(1+np.exp(-np.clip(x@w,-35,35)));g=x.T@(p-y)+reg*w;h=x.T@(x*(p*(1-p))[:,None])+np.diag(reg+1e-9);step=np.linalg.solve(h,g);w-=step
  if np.linalg.norm(step)<1e-7:break
 return w
def metric(y,p):
 y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7);pos=y.sum();neg=len(y)-pos;r=pd.Series(p).rank(method='average').to_numpy();auc=(r[y==1].sum()-pos*(pos+1)/2)/(pos*neg) if pos and neg else None;return {'rows':len(y),'rate':float(y.mean()),'auc':None if auc is None else float(auc),'logLoss':float(-(y*np.log(p)+(1-y)*np.log(1-p)).mean()),'brier':float(np.mean((p-y)**2))}

def models(d,label):
 mids=d.groupby('market_id').entry_ms.min().sort_values().index.tolist();n=len(mids);ntr=max(1,int(n*.6));nv=max(1,int(n*.2));mp={m:'TRAIN' for m in mids[:ntr]};mp.update({m:'VALIDATION' for m in mids[ntr:ntr+nv]});mp.update({m:'TEST' for m in mids[ntr+nv:]});d=d.copy();d['split']=d.market_id.map(mp);sg=d.anchor.map({'UP':1.,'DOWN':-1.});d['phase']=d.seconds_left/300;d['o_spot_q']=sg*d.spot_queue_imbalance;d['o_fut_q']=sg*d.futures_queue_imbalance;d['o_spot_t']=sg*d.spot_taker_imbalance_1s;d['o_fut_t']=sg*d.futures_taker_imbalance_1s;d['o_spot_r']=sg*d.spot_return_1s_bps;d['o_fut_r']=sg*d.futures_return_1s_bps;d['log_debt']=np.log1p(d.pre_debt);d['x15_age']=np.log1p(d.x15_age_ms);d['x1h_age']=np.log1p(d.x1h_age_ms)
 base=['phase','predict_support','strike_support','o_spot_q','o_fut_q','o_spot_t','o_fut_t','o_spot_r','o_fut_r','log_debt'];g={'M0_5M':base,'M1_PLUS_15M':base+['x15_align','x15_anchor_support','x15_age'],'M2_PLUS_1H':base+['x1h_align','x1h_anchor_support','x1h_age'],'M3_PLUS_BOTH':base+['x15_align','x15_anchor_support','x15_age','x1h_align','x1h_anchor_support','x1h_age']};y=d[label].to_numpy(int);tr=d.split.eq('TRAIN').to_numpy();out={}
 for name,cols in g.items():
  out[name]={}
  for sp in ['VALIDATION','TEST']:
   te=d.split.eq(sp).to_numpy();
   if te.sum()<5:out[name][sp]={'rows':int(te.sum())};continue
   x,z=prep(d.loc[tr,cols],d.loc[te,cols]);w=fit(x,y[tr]);p=1/(1+np.exp(-np.clip(z@w,-35,35)));out[name][sp]=metric(y[te],p)
 return {'splits':{k:[int(x) for x in d[d.split==k].market_id.unique()] for k in ['TRAIN','VALIDATION','TEST']},'features':g,'results':out}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--markets',nargs='*',type=int);ap.add_argument('--tag',default='FULL');args=ap.parse_args();meta=json.loads(XJSON.read_text(encoding='utf-8'));x=pd.read_csv(XCSV,low_memory=False);btc5=x[x.frame=='BTC5M'];mids=btc5.market_id.unique().tolist();
 if args.markets:mids=[m for m in mids if m in set(args.markets)]
 tc=ro(TARGET);pc=ro(PUBLIC);entries=[];aud=[]
 try:
  for mid in mids:
   ar=reconstruct_actions(tc,int(mid));cp=load_public(pc,int(mid));r5=btc5[btc5.market_id==mid];wend=int(r5.window_end_ms.max()) if len(r5) else int(cp.checkpoint_ms.max()+1);es=build_entries(ar,cp,wend);labs=label_entries(es,ar)
   for r in labs:
    f15=higher_features(x,'BTC15M',r['entry_ms'],r['anchor']);f1=higher_features(x,'BTC1H',r['entry_ms'],r['anchor']);
    for k,v in f15.items():r['x15_'+k]=v
    for k,v in f1.items():r['x1h_'+k]=v
    entries.append(r)
   aud.append({'market_id':int(mid),'actions':len(ar),'snapshots':len(cp),'c2_aligned_entries':len(labs)})
 finally:tc.close();pc.close()
 d=pd.DataFrame(entries);OUTDIR.mkdir(parents=True,exist_ok=True);csv=OUTDIR/f'XTFCOMMIT_ACTIVE_C2_{args.tag}_V1.csv';d.to_csv(csv,index=False)
 summary={'markets':len(mids),'entries':len(d),'entryMarkets':int(d.market_id.nunique()) if len(d) else 0,'anyTakerClean':int(d.any_taker_clean_5s.sum()) if len(d) else 0,'anyTakerCleanRate':float(d.any_taker_clean_5s.mean()) if len(d) else None,'directFirstTakerClean':int(d.first_action_taker_clean_same.sum()) if len(d) else 0,'directFirstTakerCleanRate':float(d.first_action_taker_clean_same.mean()) if len(d) else None,'anyActiveCleanLower':int(d.any_active_clean_lower_5s.sum()) if len(d) else 0,'anyActiveCleanLowerRate':float(d.any_active_clean_lower_5s.mean()) if len(d) else None,'directFirstActiveCleanLower':int(d.first_action_active_clean_lower.sum()) if len(d) else 0,'directFirstActiveCleanLowerRate':float(d.first_action_active_clean_lower.mean()) if len(d) else None,'activeCleanBirthLowerTotal':float(d.active_clean_birth_lower_5s.sum()) if len(d) else 0.0,'x15Availability':float(d.x15_available.mean()) if len(d) else None,'x1hAvailability':float(d.x1h_available.mean()) if len(d) else None}
 mods={}
 if len(d)>=30 and d.market_id.nunique()>=10:
  mods['anyTakerClean']=models(d,'any_taker_clean_5s');mods['directFirstTakerClean']=models(d,'first_action_taker_clean_same');mods['anyActiveCleanLower']=models(d,'any_active_clean_lower_5s');mods['directFirstActiveCleanLower']=models(d,'first_action_active_clean_lower')
 # alignment descriptive, fixed no tuning
 align={}
 for lab in ['any_taker_clean_5s','first_action_taker_clean_same','any_active_clean_lower_5s','first_action_active_clean_lower']:
  align[lab]={}
  for f in ['x15_align','x1h_align']:
   z=d[d[f].notna()];align[lab][f]={'n':len(z),'positiveN':int(z[lab].sum()),'positiveAlignRate':float(z[z[lab]==1][f].mean()) if z[lab].sum() else None,'negativeAlignRate':float(z[z[lab]==0][f].mean()) if (z[lab]==0).sum() else None}
 out={'version':'OUR_CROSS_TIMEFRAME_ACTIVE_DIRECTION_COMMITMENT_V1','tag':args.tag,'selection':meta['selection'],'summary':summary,'marketAudit':aud,'alignment':align,'models':mods,'guards':['Newest common 6h cohort only; no outcome-filtered market selection.','C2 uses BTC5M Predict and spot-strike opposition while BTC5M net remains aligned to prior clean side.','Primary labels are TAKER_ONLY same-side CLEAN renewal, so stale Maker-parent ambiguity is not required for this active subset.','BTC15M/BTC1H features use only latest strict-past parent state from the existing cross-timeframe slice; no future action, winner or settlement.','Higher-timeframe state is shadow/falsification evidence only, never runtime authority.']};op=OUTDIR/f'XTFCOMMIT_ACTIVE_C2_{args.tag}_V1.json';op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
