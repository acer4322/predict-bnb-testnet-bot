from __future__ import annotations
import json, math, sqlite3
from collections import defaultdict, deque
from pathlib import Path
import joblib
import numpy as np
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss
from tools.test_target_btc_eth_directional_thesis_memory_v2 import mem, label, MEM_NAMES

BASE=ROOT/'data/research/r4_v0/p0_provenance_v1'
DB=BASE/'eth_fresh150_inference_compact_v1.db'
FROZEN=BASE/'target_btc_eth_directional_thesis_memory_v2_models.joblib'
OUT=BASE/'TARGET_ETH_DIRECTIONAL_THESIS_MEMORY_V2_FRESH150_ONESHOT.json'
EPS=1e-9
FEATURES=[
 'seconds_left','pair_coverage','absnet_ratio','gross_log','candidate_relation','candidate_qty_log',
 'floor_ratio','best_pnl_ratio','avg_cost_up','avg_cost_down','avg_cost_gap',
 'candidate_side_up','candidate_price','candidate_notional_ratio','match_fraction','opp_unmatched_ratio',
 'matched_opposite_avg_price','candidate_pair_sum','pair_edge','pair_reserve_ratio','pair_debt_ratio','net_pair_reserve_ratio',
 'projected_floor_delta_ratio','projected_best_delta_ratio','post_pair_coverage','post_absnet_ratio',
 'last_maker_age_log','recent_repair_frac','recent_expand_frac','recent_maker_log'
]

def relation(side,u,d):
    if abs(u-d)<=EPS:return 0
    weak='UP' if u<d else 'DOWN';return 1 if side==weak else -1

def metrics(u,d,cost):
    g=u+d;gap=abs(u-d);pair=min(u,d)
    return {'gross':g,'gap':gap,'paircov':2*pair/g if g>EPS else 1.0,'absratio':gap/g if g>EPS else 0.0,'floor':pair-cost,'best':max(u,d)-cost}
def avg_cost(q,c):return c/q if q>EPS else 0.0

def peek_match(unmatched,side,qty,price):
    opp='DOWN' if side=='UP' else 'UP';left=max(0.0,qty);mq=0.0;oc=0.0;ev=0.0
    for oq,op in unmatched[opp]:
        if left<=EPS:break
        z=min(left,oq);mq+=z;oc+=z*op;ev+=z*(1-(op+price));left-=z
    oavg=oc/mq if mq>EPS else 0.0;ps=oavg+price if mq>EPS else 0.0
    return mq,oavg,ps,ev/mq if mq>EPS else 0.0

def apply_fill(unmatched,side,qty,price):
    opp='DOWN' if side=='UP' else 'UP';left=max(0.0,qty);reserve=debt=0.0
    while left>EPS and unmatched[opp]:
        oq,op=unmatched[opp][0];z=min(left,oq);edge=z*(1-(op+price))
        if edge>=0:reserve+=edge
        else:debt+=-edge
        left-=z;oq-=z
        if oq<=EPS:unmatched[opp].popleft()
        else:unmatched[opp][0]=(oq,op)
    if left>EPS:unmatched[side].append((left,price))
    return reserve,debt

def uq(unmatched,side):return sum(q for q,_ in unmatched[side])

def load_parents():
    con=sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro',uri=True)
    ends=dict((int(a),int(b)) for a,b in con.execute('select market_id,window_end_ms from maker_book_inference_markets'))
    rows=con.execute('''select market_id,upper(role),upper(side),coalesce(nullif(order_hash,''),source_leg_id) as pid,
      min(event_ms) as t,sum(shares) as qty,sum(price*shares)/sum(shares) as px
      from maker_book_inference_wallet_events where upper(quote_type)='BID' and shares>0
      group by market_id,upper(role),upper(side),coalesce(nullif(order_hash,''),source_leg_id)
      order by market_id,t,pid''').fetchall()
    con.close();return ends,rows

def build():
    ends,rows=load_parents();by=defaultdict(list)
    for r in rows:by[int(r[0])].append(r)
    X=[];mids=[];ts=[];sides=[];events={};parent_counts=0
    for mid in sorted(ends):
        evs=by.get(mid,[]);parent_counts+=len(evs);u=d=cost=cu=cd=0.0;reserve=debt=0.0;unmatched={'UP':deque(),'DOWN':deque()};hist=deque();me=[]
        for r in evs:
            _,role,side,pid,t,qty,px=r;role=str(role);side=str(side);t=int(t);qty=float(qty);px=float(px)
            pre=metrics(u,d,cost);rel=relation(side,u,d) if role=='MAKER' else 0
            if role=='MAKER':
                mq,oavg,ps,edge=peek_match(unmatched,side,qty,px);au=avg_cost(u,cu);ad=avg_cost(d,cd);scale=max(cost,1.0)
                pu=u+(qty if side=='UP' else 0);pd=d+(qty if side=='DOWN' else 0);pc=cost+qty*px;pm=metrics(pu,pd,pc)
                recent=[x for x in hist if t-x[0]<=30000 and x[1]=='MAKER'];rr=sum(x[2]==1 for x in recent);ee=sum(x[2]==-1 for x in recent);lastm=next((x for x in reversed(hist) if x[1]=='MAKER'),None)
                opp='DOWN' if side=='UP' else 'UP';oq=uq(unmatched,opp)
                f=[
                  (ends[mid]-t)/1000.0,pre['paircov'],pre['absratio'],math.log1p(pre['gross']),float(rel),math.log1p(qty),pre['floor']/scale,pre['best']/scale,au,ad,au-ad,
                  1.0 if side=='UP' else 0.0,px,(qty*px)/scale,mq/max(qty,EPS),oq/max(pre['gross'],1.0),oavg,ps,edge,reserve/scale,debt/scale,(reserve-debt)/scale,
                  (pm['floor']-pre['floor'])/scale,(pm['best']-pre['best'])/scale,pm['paircov'],pm['absratio'],math.log1p(min(300000,t-lastm[0] if lastm else 300000))/math.log1p(300000),
                  rr/max(len(recent),1),ee/max(len(recent),1),math.log1p(len(recent))/math.log1p(64)]
                if rel==-1:
                    X.append(f);mids.append(mid);ts.append(t);sides.append(side)
                me.append((t,side,rel,qty,px,pid))
            if side=='UP':u+=qty;cu+=qty*px
            else:d+=qty;cd+=qty*px
            cost+=qty*px;dr,dd=apply_fill(unmatched,side,qty,px);reserve+=dr;debt+=dd
            hist.append((t,role,rel if role=='MAKER' else 0,side,qty,px))
            while hist and t-hist[0][0]>120000:hist.popleft()
        events[mid]=me
    return np.asarray(X,float),np.asarray(mids,int),np.asarray(ts,np.int64),np.asarray(sides),events,{'markets':len(ends),'aggregatedParents':parent_counts,'dominantMakerRows':len(X)}

def met(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float)
    return {'n':int(len(y)),'positive':int(y.sum()),'positiveRate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1])) if len(y) else None}

def main():
    X,mids,ts,sides,events,cov=build();M=np.vstack([mem(events.get(int(mid),[]),int(t),str(s)) for mid,t,s in zip(mids,ts,sides)]);y=np.asarray([label(events.get(int(mid),[]),int(t),str(s)) for mid,t,s in zip(mids,ts,sides)],int)
    frozen=joblib.load(FROZEN)['ETH'];groups={
      'FULL30':(frozen['FULL30'],X),
      'THESIS_MEMORY':(frozen['THESIS_MEMORY'],M),
      'FULL30_PLUS_MEMORY':(frozen['FULL30_PLUS_MEMORY'],np.hstack([X,M]))}
    scores={}
    for name,(mod,xx) in groups.items():scores[name]=met(y,mod.predict_proba(xx)[:,1])
    # Fresh anatomy uses only strict observed chronology; no fitting.
    pos=y==1;neg=y==0
    anatomy={}
    for j,n in enumerate(MEM_NAMES):anatomy[n]={'positiveMedian':float(np.median(M[pos,j])) if pos.any() else None,'negativeMedian':float(np.median(M[neg,j])) if neg.any() else None}
    rep={'version':'TARGET_ETH_DIRECTIONAL_THESIS_MEMORY_V2_FRESH150_ONESHOT','researchOnly':True,'actionAuthority':False,'preRegistration':'TARGET_ETH_DIRECTIONAL_THESIS_MEMORY_V2_FRESH150_ONESHOT_PREREGISTERED.json','coverage':cov,'marketRange':[int(mids.min()) if len(mids) else None,int(mids.max()) if len(mids) else None],'scores':scores,'deltas':{'COMBINED_vs_FULL30_AUC':scores['FULL30_PLUS_MEMORY']['auc']-scores['FULL30']['auc'] if scores['FULL30_PLUS_MEMORY']['auc'] is not None and scores['FULL30']['auc'] is not None else None,'COMBINED_vs_MEMORY_AUC':scores['FULL30_PLUS_MEMORY']['auc']-scores['THESIS_MEMORY']['auc'] if scores['FULL30_PLUS_MEMORY']['auc'] is not None and scores['THESIS_MEMORY']['auc'] is not None else None},'memoryAnatomy':anatomy,'guards':{'retrained':False,'featureChanged':False,'winnerUsed':False,'pnlUsed':False,'behaviorChanged':False}}
    OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'out':str(OUT.relative_to(ROOT)),'coverage':cov,'marketRange':rep['marketRange'],'scores':scores,'deltas':rep['deltas'],'selectedMemory':{k:anatomy[k] for k in ['same_dom_count_30','same_dom_count_60','repair_count_30','sec_since_same_dom','sec_since_repair','same_dom_streak','repair_to_same_dom_qty_60']}},indent=2),flush=True)
if __name__=='__main__':main()
