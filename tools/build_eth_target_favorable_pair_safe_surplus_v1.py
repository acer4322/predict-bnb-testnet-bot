from __future__ import annotations
import argparse, json, math, sqlite3
from collections import deque
from pathlib import Path
import numpy as np

EPS=1e-9
CUTOFF_DEFAULT=1823545
FEATURES=[
 'seconds_left','pair_coverage','absnet_ratio','gross_log','candidate_relation','candidate_qty_log',
 'floor_ratio','best_pnl_ratio','avg_cost_up','avg_cost_down','avg_cost_gap',
 'candidate_side_up','candidate_price','candidate_notional_ratio','match_fraction','opp_unmatched_ratio',
 'matched_opposite_avg_price','candidate_pair_sum','pair_edge','pair_reserve_ratio','pair_debt_ratio','net_pair_reserve_ratio',
 'projected_floor_delta_ratio','projected_best_delta_ratio','post_pair_coverage','post_absnet_ratio',
 'last_maker_age_log','recent_repair_frac','recent_expand_frac','recent_maker_log'
]
BALANCE_FEATURES=['seconds_left','pair_coverage','absnet_ratio','gross_log','candidate_relation','candidate_qty_log']


def relation(side,u,d):
    if abs(u-d)<=EPS:return 0
    weak='UP' if u<d else 'DOWN'
    return 1 if side==weak else -1

def metrics(u,d,cost):
    g=u+d;gap=abs(u-d);pair=min(u,d)
    return {
      'gross':g,'gap':gap,'paircov':2*pair/g if g>EPS else 1.0,
      'absratio':gap/g if g>EPS else 0.0,'floor':pair-cost,'best':max(u,d)-cost
    }

def avg_cost(q,c):return c/q if q>EPS else 0.0

def peek_match(unmatched,side,qty,price):
    opp='DOWN' if side=='UP' else 'UP'; left=max(0.0,qty); mq=0.0; opp_cost=0.0; edge_value=0.0
    for oq,op in unmatched[opp]:
        if left<=EPS:break
        z=min(left,oq); mq+=z;opp_cost+=z*op;edge_value+=z*(1.0-(op+price));left-=z
    oavg=opp_cost/mq if mq>EPS else 0.0
    ps=oavg+price if mq>EPS else 0.0
    return mq,oavg,ps,(edge_value/mq if mq>EPS else 0.0)

def apply_fill(unmatched,side,qty,price):
    opp='DOWN' if side=='UP' else 'UP';left=max(0.0,qty);reserve=debt=paired=0.0
    while left>EPS and unmatched[opp]:
        oq,op=unmatched[opp][0];z=min(left,oq);edge=z*(1.0-(op+price));paired+=z
        if edge>=0:reserve+=edge
        else:debt+=-edge
        left-=z;oq-=z
        if oq<=EPS:unmatched[opp].popleft()
        else:unmatched[opp][0]=(oq,op)
    if left>EPS:unmatched[side].append((left,price))
    return paired,reserve,debt

def unmatched_qty(unmatched,side):return sum(q for q,_ in unmatched[side])

def safe(st):return st['floor']>=-1e-9 and st['best']>1e-9

def future_labels(row,states):
    t=row['t'];post=row['post']; f30=[s for s in states if s['t']>=t and s['t']<=t+30000]; f60=[s for s in states if s['t']>=t and s['t']<=t+60000]
    if not f30:f30=[post]
    if not f60:f60=[post]
    # State persists between actual fills. A safe state is durable10 if no observed state breaks it during the next 10s.
    durable=0
    for s in f30:
        if not safe(s):continue
        tail=[z for z in states if z['t']>=s['t'] and z['t']<=s['t']+10000]
        if all(safe(z) for z in tail):durable=1;break
    safe60=int(any(safe(s) for s in f60))
    scale=max(post['cost'],1.0)
    min_floor=min(s['floor'] for s in f30); max_best=max(s['best'] for s in f30)
    y_floor=max(-3.0,min(3.0,(min_floor-post['floor'])/scale))
    y_best=max(-3.0,min(3.0,(max_best-post['best'])/scale))
    exp_rec=np.nan
    if row['rel']==1 and row['matchQty']>EPS and row['pairEdge']<0:
        exp_rec=0.0
        for s in f30:
            if s['netReserve']>=row['preNetReserve']-1e-9 and safe(s):exp_rec=1.0;break
    safe_expand=np.nan
    if row['rel']==-1 and row['pre']['floor']>=-1e-9:
        safe_expand=float(all(s['floor']>=-1e-9 for s in f30) and max_best>row['pre']['best']+1e-9)
    return durable,safe60,exp_rec,safe_expand,y_floor,y_best

def build(db,cutoff,max_markets=0):
    c=sqlite3.connect(f'file:{Path(db).resolve().as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row
    ends={int(r['market_id']):int(r['window_end_ms']) for r in c.execute("select market_id,window_end_ms from target_markets where asset='ETH' and market_id<=? and window_end_ms is not null",(cutoff,))}
    mids=[int(r[0]) for r in c.execute("select distinct market_id from target_parent_orders where asset='ETH' and role='MAKER' and market_id<=? order by market_id",(cutoff,)) if int(r[0]) in ends]
    if max_markets>0:mids=mids[-max_markets:]
    rows=[];market_stats=[]
    for ii,mid in enumerate(mids,1):
        evs=list(c.execute("select parent_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' and market_id=? and first_event_ms is not null order by first_event_ms,parent_id",(mid,)))
        if not evs:continue
        u=d=cost=cu=cd=0.0; reserve=debt=0.0; unmatched={'UP':deque(),'DOWN':deque()};hist=deque();states=[];mr=[]
        end=ends[mid]
        for eidx,r in enumerate(evs):
            role=str(r['role']);side=str(r['side']);t=int(r['first_event_ms']);px=float(r['average_price'] or 0.0);qty=float(r['shares'] or 0.0)
            if qty<=EPS or px<0:continue
            pre=metrics(u,d,cost); rel=relation(side,u,d) if role=='MAKER' else 0
            if role=='MAKER':
                mq,oavg,ps,edge=peek_match(unmatched,side,qty,px); au=avg_cost(u,cu);ad=avg_cost(d,cd);scale=max(cost,1.0)
                pu=u+(qty if side=='UP' else 0.0);pd=d+(qty if side=='DOWN' else 0.0);pcost=cost+qty*px;pm=metrics(pu,pd,pcost)
                recent=[x for x in hist if t-x[0]<=30000 and x[1]=='MAKER'];rr=sum(x[2]==1 for x in recent);ee=sum(x[2]==-1 for x in recent);lastm=next((x for x in reversed(hist) if x[1]=='MAKER'),None)
                opp='DOWN' if side=='UP' else 'UP'; oq=unmatched_qty(unmatched,opp)
                f=[
                  (end-t)/1000.0,pre['paircov'],pre['absratio'],math.log1p(pre['gross']),float(rel),math.log1p(qty),
                  pre['floor']/scale,pre['best']/scale,au,ad,au-ad,
                  1.0 if side=='UP' else 0.0,px,(qty*px)/scale,mq/max(qty,EPS),oq/max(pre['gross'],1.0),
                  oavg,ps,edge,reserve/scale,debt/scale,(reserve-debt)/scale,
                  (pm['floor']-pre['floor'])/scale,(pm['best']-pre['best'])/scale,pm['paircov'],pm['absratio'],
                  math.log1p(min(300000,t-lastm[0] if lastm else 300000))/math.log1p(300000),rr/max(len(recent),1),ee/max(len(recent),1),math.log1p(len(recent))/math.log1p(64)
                ]
                mr.append({'market':mid,'end':end,'t':t,'eventIndex':eidx,'x':f,'rel':rel,'matchQty':mq,'pairEdge':edge,'preNetReserve':reserve-debt,'pre':dict(pre),'postProjected':dict(pm)})
            if side=='UP':u+=qty;cu+=qty*px
            else:d+=qty;cd+=qty*px
            cost+=qty*px
            _,dr,dd=apply_fill(unmatched,side,qty,px);reserve+=dr;debt+=dd
            post=metrics(u,d,cost); post.update({'t':t,'cost':cost,'netReserve':reserve-debt,'reserve':reserve,'debt':debt})
            states.append(post)
            hist.append((t,role,rel if role=='MAKER' else 0,side,qty,px))
            while hist and t-hist[0][0]>120000:hist.popleft()
        # attach exact post-state and future labels
        st_by_t={s['t']:s for s in states}
        for r in mr:
            r['post']=st_by_t.get(r['t'],r['postProjected']); y=future_labels(r,states);r['y']=y;rows.append(r)
        market_stats.append({'marketId':mid,'makerRows':len(mr),'safeEnd':int(bool(states and safe(states[-1]))),'endFloor':states[-1]['floor'] if states else None,'endBest':states[-1]['best'] if states else None})
        if ii%250==0:print(json.dumps({'buildProgress':ii,'markets':len(mids),'rows':len(rows)}),flush=True)
    c.close();return rows,market_stats

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--cutoff',type=int,default=CUTOFF_DEFAULT);ap.add_argument('--max-markets',type=int,default=0);ap.add_argument('--output',required=True);a=ap.parse_args()
    rows,ms=build(a.db,a.cutoff,a.max_markets)
    X=np.asarray([r['x'] for r in rows],np.float32);Y=np.asarray([r['y'] for r in rows],np.float32);mid=np.asarray([r['market'] for r in rows],np.int32);end=np.asarray([r['end'] for r in rows],np.int64);rel=np.asarray([r['rel'] for r in rows],np.int8);edge=np.asarray([r['pairEdge'] for r in rows],np.float32);mq=np.asarray([r['matchQty'] for r in rows],np.float32)
    op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);np.savez_compressed(op,X=X,Y=Y,market_id=mid,end_ms=end,relation=rel,pair_edge=edge,matched_qty=mq)
    yname=['durable_safe_surplus_30s','safe_surplus_60s','expensive_repair_recovers_30s','safe_expand_preserves_floor_30s','future_min_floor_delta_30s','future_max_best_delta_30s']
    meta={'version':'ETH_TARGET_FAVORABLE_PAIR_SAFE_SURPLUS_DATASET_V1','cutoff':a.cutoff,'maxMarkets':a.max_markets,'features':FEATURES,'balanceFeatures':BALANCE_FEATURES,'labels':yname,'rows':len(rows),'markets':len(set(int(x) for x in mid.tolist())) if len(mid) else 0,'marketStats':{'safeEndRate':float(np.mean([x['safeEnd'] for x in ms])) if ms else None},'labelSupport':{yname[j]:{'n':int(np.isfinite(Y[:,j]).sum()),'positiveRate':float(np.nanmean(Y[:,j])) if j<4 and np.isfinite(Y[:,j]).any() else None} for j in range(len(yname))},'boundary':['marketId<=cutoff only','Target actual fills only','candidate price/side/qty are proposal-time features','future Target path labels only','winner/PnL absent','no dream fill']}
    op.with_suffix('.meta.json').write_text(json.dumps(meta,indent=2),encoding='utf-8');print(json.dumps(meta,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
