from __future__ import annotations
import argparse,bisect,csv,json,math,sqlite3,statistics,zlib
from collections import deque
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
EPS=1e-9
GRID=.01
FRAMES={
 'BTC5M':{'book':'data/wallet_maker_book_inference.db','duration_ms':300000,'asset':'BTC','source':'official'},
 'BTC15M':{'book':'data/wallet_maker_book_inference_btc15m.db','duration_ms':900000,'asset':'BTC','source':'local'},
 'BTC1H':{'book':'data/wallet_maker_book_inference_btc1h.db','duration_ms':3600000,'asset':'BTC','source':'local'},
 'ETH5M':{'book':'data/wallet_maker_book_inference_eth5m.db','duration_ms':300000,'asset':'ETH','source':'local'},
}
FEATURES=[
 'phase','seconds_left_frac','gross','abs_net','imbalance_ratio','paired_coverage','floor','best','risk_reward_ratio',
 'combined_avg_pair_edge','dominant_mid','dominant_support','action_mid','spread_ticks','dominant_depth_imbalance',
 'dominant_mid_delta_1update','dominant_mid_delta_5updates','dominant_mid_delta_20updates','dominant_mid_delta_since_prev_action',
 'parent_gap_frac','last_repair_age_frac','last_expand_age_frac','same_side_streak','same_role_streak','recent3_repair_share',
 'prev_service_fraction','repair_progress_since_last_expand','prev_route_is_active'
]

def ro(p:Path):
 c=sqlite3.connect(f'file:{p.resolve().as_posix()}?mode=ro',uri=True,timeout=30);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');return c

def dec(b): return json.loads(zlib.decompress(b).decode('utf-8')) if b else None

def apply_changes(book,chg):
 if not isinstance(chg,dict):return
 for k in ('bids','asks'):
  for x in chg.get(k,[]) or []:
   p=float(x['price']);a=float(x['after'])
   if a<=1e-12:book[k].pop(p,None)
   else:book[k][p]=a

def sign(x):return 1 if x>EPS else -1 if x<-EPS else 0

def qmedian(a):
 a=[float(x) for x in a if x is not None and math.isfinite(float(x))]
 return statistics.median(a) if a else None

def quantile(a,p):
 a=sorted(float(x) for x in a if x is not None and math.isfinite(float(x)))
 if not a:return None
 return a[int(round((len(a)-1)*p))]

def rank_auc(y,x):
 pairs=[(float(v),int(t)) for v,t in zip(x,y) if v is not None and math.isfinite(float(v))]
 if not pairs:return None
 n1=sum(t for _,t in pairs);n0=len(pairs)-n1
 if not n1 or not n0:return None
 pairs.sort(key=lambda z:z[0]);ranks=[0.0]*len(pairs);i=0
 while i<len(pairs):
  j=i+1
  while j<len(pairs) and pairs[j][0]==pairs[i][0]:j+=1
  r=(i+1+j)/2.0
  for k in range(i,j):ranks[k]=r
  i=j
 s=sum(r for r,(_,t) in zip(ranks,pairs) if t==1)
 return (s-n1*(n1+1)/2)/(n1*n0)

def book_features(book,mid_hist,dominant,action_side,prev_action_mid):
 bids,asks=book['bids'],book['asks']
 if not bids or not asks:return None
 bb=max(bids);ba=min(asks);bd=float(bids[bb]);ad=float(asks[ba])
 up_bid,up_ask=float(bb),float(ba);up_mid=(up_bid+up_ask)/2
 down_bid,down_ask=1-up_ask,1-up_bid;down_mid=1-up_mid
 bid3=sum(v for _,v in sorted(bids.items(),reverse=True)[:3]);ask3=sum(v for _,v in sorted(asks.items())[:3])
 up_di=(bid3-ask3)/(bid3+ask3) if bid3+ask3>EPS else 0.0
 ds=1 if dominant=='UP' else -1 if dominant=='DOWN' else 0
 dom_mid=up_mid if dominant=='UP' else down_mid if dominant=='DOWN' else .5
 action_mid=up_mid if action_side=='UP' else down_mid
 mh=list(mid_hist)
 def d(n):
  if len(mh)<n+1:return math.nan
  return ds*(up_mid-mh[-1-n])
 return {
  'up_bid':up_bid,'up_ask':up_ask,'up_mid':up_mid,'down_bid':down_bid,'down_ask':down_ask,'down_mid':down_mid,
  'dominant_mid':dom_mid,'dominant_support':dom_mid-.5,'action_mid':action_mid,'spread_ticks':(up_ask-up_bid)/GRID,
  'dominant_depth_imbalance':ds*up_di,'dominant_mid_delta_1update':d(1),'dominant_mid_delta_5updates':d(5),'dominant_mid_delta_20updates':d(20),
  'dominant_mid_delta_since_prev_action':(ds*(up_mid-prev_action_mid) if prev_action_mid is not None and ds else math.nan),
 }

def markets_for(c,start,end):
 return [dict(r) for r in c.execute('select market_id,title,first_seen_ms,window_end_ms,status from maker_book_inference_markets where window_end_ms>? and window_end_ms<=? order by window_end_ms,market_id',(start,end))]

def load_parents(frame,c,mid):
 cfg=FRAMES[frame]
 if cfg['source']=='official':
  t=ro(ROOT/'data/target_wallet_official_v1.db')
  try:
   rr=t.execute("select parent_id,role,side,first_event_ms,last_event_ms,average_price,shares,fill_legs from target_parent_orders where asset=? and market_id=? and quote_type='BID' and role in ('MAKER','TAKER') order by first_event_ms,parent_id",(cfg['asset'],mid)).fetchall()
   return [dict(r) for r in rr]
  finally:t.close()
 q="""select coalesce(order_hash,source_leg_id) parent_id,role,side,min(event_ms) first_event_ms,max(event_ms) last_event_ms,
 sum(price*shares)/sum(shares) average_price,sum(shares) shares,count(*) fill_legs
 from maker_book_inference_wallet_events where market_id=? and quote_type='BID' and role in ('MAKER','TAKER')
 group by coalesce(order_hash,source_leg_id),role,side order by first_event_ms,parent_id"""
 return [dict(r) for r in c.execute(q,(mid,))]

def extract_frame(frame,start,end):
 cfg=FRAMES[frame];c=ro(ROOT/cfg['book']);rows=[];market_stats=[]
 try:
  ms=markets_for(c,start,end)
  for mi,m in enumerate(ms,1):
   mid=int(m['market_id']);wend=int(m['window_end_ms']);wstart=wend-int(cfg['duration_ms'])
   parents=load_parents(frame,c,mid)
   if not parents:continue
   ups=c.execute('select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,)).fetchall()
   if not ups:continue
   book={'bids':{},'asks':{}};ui=0;mid_hist=deque(maxlen=64)
   up=down=cost=0.0;up_cost_total=down_cost_total=0.0;prev_class=None;prev_route=None;prev_side=None;prev_t=None;prev_up_mid=None
   same_side_streak=0;same_role_streak=0;hist_classes=deque(maxlen=3);last_repair_t=None;last_expand_t=None;prev_service_fraction=math.nan;abs_net_at_last_expand=None
   usable=0
   for p in parents:
    t=int(p['first_event_ms'])
    while ui<len(ups) and int(ups[ui]['source_timestamp_ms'])<t:
     u=ups[ui]
     if int(u['is_checkpoint']):book={'bids':{float(k):float(v) for k,v in (dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(u['native_asks_z']) or {}).items()}}
     else:apply_changes(book,dec(u['changes_z']) or {})
     if book['bids'] and book['asks']:
      mid_hist.append((max(book['bids'])+min(book['asks']))/2)
     ui+=1
    net=up-down;gross=up+down;absnet=abs(net);dom='UP' if net>EPS else 'DOWN' if net<-EPS else None
    side=str(p['side']);route=str(p['role']);sh=float(p['shares']);px=float(p['average_price'])
    klass='BASE' if dom is None else ('EXPAND' if side==dom else 'REPAIR')
    bf=book_features(book,mid_hist,dom,side,prev_up_mid)
    if bf and klass!='BASE':
     floor=min(up-cost,down-cost);best=max(up-cost,down-cost)
     phase=(t-wstart)/(wend-wstart) if wend>wstart else math.nan
     rr=(abs(floor)/best if best>EPS else math.nan)
     avg_u=(up_cost_total/up if up>EPS else math.nan);avg_d=(down_cost_total/down if down>EPS else math.nan)
     pair_edge=1-avg_u-avg_d if math.isfinite(avg_u) and math.isfinite(avg_d) else math.nan
     repair_prog=(max(0.0,(abs_net_at_last_expand-absnet)/abs_net_at_last_expand) if abs_net_at_last_expand and abs_net_at_last_expand>EPS else math.nan)
     row={'frame':frame,'market_id':mid,'window_start_ms':wstart,'window_end_ms':wend,'t':t,'current_class':klass,'current_route':route,'current_side':side,'prev_class':prev_class,'prev_route':prev_route,'prev_side':prev_side,
          'phase':phase,'seconds_left_frac':(wend-t)/(wend-wstart),'gross':gross,'net':net,'abs_net':absnet,'imbalance_ratio':absnet/gross if gross>EPS else 0.0,'paired_coverage':2*min(up,down)/gross if gross>EPS else 0.0,
          'floor':floor,'best':best,'risk_reward_ratio':rr,'combined_avg_pair_edge':pair_edge,
          'parent_gap_frac':((t-prev_t)/(wend-wstart) if prev_t is not None else math.nan),'last_repair_age_frac':((t-last_repair_t)/(wend-wstart) if last_repair_t is not None else math.nan),'last_expand_age_frac':((t-last_expand_t)/(wend-wstart) if last_expand_t is not None else math.nan),
          'same_side_streak':same_side_streak,'same_role_streak':same_role_streak,'recent3_repair_share':(sum(x=='REPAIR' for x in hist_classes)/len(hist_classes) if hist_classes else math.nan),'prev_service_fraction':prev_service_fraction,'repair_progress_since_last_expand':repair_prog,'prev_route_is_active':1.0 if prev_route=='TAKER' else 0.0 if prev_route else math.nan,
          'action_shares':sh,'action_price':px,'pre_dominant_side':dom,**bf}
     rows.append(row);usable+=1
    # apply current parent after snapshot
    if side=='UP':up+=sh
    else:down+=sh
    if side=='UP':up_cost_total+=px*sh
    else:down_cost_total+=px*sh
    cost+=px*sh
    postabs=abs(up-down)
    service_fraction=(min(1.0,sh/absnet) if klass=='REPAIR' and absnet>EPS else math.nan)
    if klass=='EXPAND':last_expand_t=t;abs_net_at_last_expand=postabs
    elif klass=='REPAIR':last_repair_t=t
    prev_service_fraction=service_fraction
    same_side_streak=(same_side_streak+1 if prev_side==side else 1)
    same_role_streak=(same_role_streak+1 if prev_class==klass else 1)
    hist_classes.append(klass);prev_class=klass;prev_route=route;prev_side=side;prev_t=t
    if bf:prev_up_mid=bf['up_mid']
   market_stats.append({'market_id':mid,'parents':len(parents),'usable':usable})
  return rows,market_stats
 finally:c.close()

def summarize(rows):
 out={}
 for fr in FRAMES:
  rr=[r for r in rows if r['frame']==fr and r['prev_class'] in ('EXPAND','REPAIR') and r['current_class'] in ('EXPAND','REPAIR')]
  s={'rows':len(rr),'transitions':{}}
  for a in ('EXPAND','REPAIR'):
   for b in ('EXPAND','REPAIR'):
    s['transitions'][f'{a}_TO_{b}']=sum(r['prev_class']==a and r['current_class']==b for r in rr)
  s['tasks']={}
  tasks=[('AFTER_EXPAND_REPAIR_VS_EXPAND','EXPAND','REPAIR'),('AFTER_REPAIR_EXPAND_VS_REPAIR','REPAIR','EXPAND')]
  for name,prev,pos in tasks:
   z=[r for r in rr if r['prev_class']==prev]
   y=[1 if r['current_class']==pos else 0 for r in z]
   fs={}
   for f in FEATURES:
    vals=[r.get(f) for r in z];auc=rank_auc(y,vals);p=[v for v,t in zip(vals,y) if t==1];n=[v for v,t in zip(vals,y) if t==0]
    fs[f]={'auc':auc,'medianPositive':qmedian(p),'medianNegative':qmedian(n),'deltaMedian':((qmedian(p)-qmedian(n)) if qmedian(p) is not None and qmedian(n) is not None else None),'nPositive':sum(y),'nNegative':len(y)-sum(y)}
   s['tasks'][name]={'n':len(z),'positive':sum(y),'positiveShare':sum(y)/len(y) if y else None,'features':fs}
  out[fr]=s
 return out

def build_btc_cross_support(rows):
 byfr={fr:[r for r in rows if r['frame']==fr] for fr in ('BTC5M','BTC15M','BTC1H')}
 for fr in byfr:byfr[fr].sort(key=lambda r:r['t'])
 def latest_state(fr,t):
  cand=[r for r in byfr[fr] if r['window_start_ms']<=t<r['window_end_ms'] and r['t']<t]
  if not cand:return None
  r=max(cand,key=lambda x:x['t']);postnet=r['net']+(r['action_shares'] if r['current_side']=='UP' else -r['action_shares'])
  return sign(postnet)
 out=[]
 for r in byfr['BTC5M']:
  if r['prev_class'] not in ('EXPAND','REPAIR'):continue
  s5=sign(r['net']);s15=latest_state('BTC15M',r['t']);s1h=latest_state('BTC1H',r['t'])
  x=dict(r);x['align15m']=(1 if s15 and s5 and s15==s5 else 0 if s15 and s5 else None);x['align1h']=(1 if s1h and s5 and s1h==s5 else 0 if s1h and s5 else None);x['higher_support_count']=sum(v==1 for v in (x['align15m'],x['align1h']) if v is not None);x['higher_available']=sum(v is not None for v in (x['align15m'],x['align1h']));out.append(x)
 return out

def support_summary(x):
 out={}
 for prev,pos,name in [('EXPAND','REPAIR','AFTER_EXPAND_REPAIR_VS_EXPAND'),('REPAIR','EXPAND','AFTER_REPAIR_EXPAND_VS_REPAIR')]:
  z=[r for r in x if r['prev_class']==prev]
  for field in ('align15m','align1h'):
   p=[r[field] for r in z if r['current_class']==pos and r[field] is not None];n=[r[field] for r in z if r['current_class']!=pos and r[field] is not None]
   out[f'{name}_{field}']={'positiveN':len(p),'negativeN':len(n),'positiveAlignShare':sum(p)/len(p) if p else None,'negativeAlignShare':sum(n)/len(n) if n else None,'delta':((sum(p)/len(p)-sum(n)/len(n)) if p and n else None)}
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--hours',type=float,default=6);ap.add_argument('--output',required=True);ap.add_argument('--rows-output');a=ap.parse_args()
 maxends=[]
 for fr,cfg in FRAMES.items():
  c=ro(ROOT/cfg['book']);maxends.append(int(c.execute('select max(window_end_ms) from maker_book_inference_markets where window_end_ms is not null').fetchone()[0]));c.close()
 end=min(maxends);start=int(end-a.hours*3600*1000)
 rows=[];mstats={}
 for fr in FRAMES:
  rr,ss=extract_frame(fr,start,end);rows+=rr;mstats[fr]=ss;print(json.dumps({'frame':fr,'markets':len(ss),'rows':len(rr)},ensure_ascii=False),flush=True)
 summary=summarize(rows);cross=build_btc_cross_support(rows);cs=support_summary(cross)
 # robust feature orientation across all four frames
 robust={}
 for task in ('AFTER_EXPAND_REPAIR_VS_EXPAND','AFTER_REPAIR_EXPAND_VS_REPAIR'):
  cand=[]
  for f in FEATURES:
   aucs=[summary[fr]['tasks'][task]['features'][f]['auc'] for fr in FRAMES]
   if all(v is not None for v in aucs):
    oriented=[v-.5 for v in aucs];same=all(v>0 for v in oriented) or all(v<0 for v in oriented)
    strength=min(abs(v) for v in oriented)
    if same:cand.append({'feature':f,'aucs':dict(zip(FRAMES,aucs)),'minAbsAucFrom05':strength,'orientation':'HIGHER_IN_POSITIVE' if all(v>0 for v in oriented) else 'LOWER_IN_POSITIVE'})
  robust[task]=sorted(cand,key=lambda x:x['minAbsAucFrom05'],reverse=True)
 out={'version':'TARGET_CROSS_TIMEFRAME_ACTION_VALUE_DECISION_SLICE_V1','researchOnly':True,'winnerUsed':False,'selection':{'commonEndMs':end,'commonStartMs':start,'hours':a.hours,'rule':'all markets in common latest time window; no outcome filtering'},'marketStats':mstats,'summary':summary,'btcCrossTimeframeSupport':cs,'robustFeatures':robust,'boundary':['Target actual fills/parents are posthoc behavior evidence only','all decision features strict-past relative to current Target parent first_event_ms','current route/action are labels, never candidate runtime inputs','no winner/settlement','book updates use source_timestamp_ms strictly less than action time','parent-level role reconstruction from pre-action inventory','raw target action may cross neutral point; current intent label uses pre-action inventory only']}
 op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
 if a.rows_output:
  rp=Path(a.rows_output);rp.parent.mkdir(parents=True,exist_ok=True)
  if rows:
   keys=sorted({k for r in rows for k in r});
   with rp.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)
 print(json.dumps({'ok':True,'selection':out['selection'],'rowCounts':{fr:summary[fr]['rows'] for fr in FRAMES},'btcCrossTimeframeSupport':cs,'robustFeatureTop':{k:v[:8] for k,v in robust.items()}},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
