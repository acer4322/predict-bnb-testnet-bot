import sqlite3,csv,json,argparse,math,statistics
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--target-db',required=True); p.add_argument('--liq-csv',required=True); p.add_argument('--output',required=True); a=p.parse_args()

def rank(x):
    order=sorted(range(len(x)),key=lambda i:x[i]); r=[0.0]*len(x); i=0
    while i<len(order):
        j=i
        while j+1<len(order) and x[order[j+1]]==x[order[i]]: j+=1
        v=(i+j)/2+1
        for k in range(i,j+1): r[order[k]]=v
        i=j+1
    return r

def corr(x,y):
    if len(x)<3:return None
    mx=sum(x)/len(x); my=sum(y)/len(y); dx=[v-mx for v in x]; dy=[v-my for v in y]; den=(sum(v*v for v in dx)*sum(v*v for v in dy))**0.5
    return sum(dx[i]*dy[i] for i in range(len(x)))/den if den else None

def spear(pairs):
    pairs=[(float(x),float(y)) for x,y in pairs if x is not None and y is not None and math.isfinite(float(x)) and math.isfinite(float(y))]
    if len(pairs)<3:return None
    return corr(rank([x for x,_ in pairs]),rank([y for _,y in pairs]))

def quartiles(rows,key):
    xs=sorted([r for r in rows if r.get(key) is not None],key=lambda r:r[key]); out=[]
    for q in range(4):
        g=xs[len(xs)*q//4:len(xs)*(q+1)//4]
        if not g: continue
        out.append({'q':q+1,'n':len(g),'metricMedian':statistics.median(r[key] for r in g),'dominantFrac':sum(r['dom'] for r in g)/max(1,sum(r['dom']+r['weak'] for r in g)),'makerDominantFrac':sum(r['maker_dom'] for r in g)/max(1,sum(r['maker_dom']+r['maker_weak'] for r in g)),'takerDominantFrac':sum(r['taker_dom'] for r in g)/max(1,sum(r['taker_dom']+r['taker_weak'] for r in g)),'meanPnl':sum(r['pnl'] for r in g)/len(g),'winRate':sum(r['pnl']>0 for r in g)/len(g),'meanNotional':sum(r['notional'] for r in g)/len(g)})
    return out

liq={}
with open(a.liq_csv,encoding='utf-8') as f:
    for r in csv.DictReader(f):
        liq[(r['asset'],int(r['market_id']))]={'updates':int(r['updates']),'avg_order_count':float(r['avg_order_count']),'avg_total_levels':float(r['avg_total_levels']),'coverage_sec':float(r['coverage_sec']) if r['coverage_sec'] else None,'avg_spread':float(r['avg_spread']) if r['avg_spread'] else None}

c=sqlite3.connect(a.target_db); c.row_factory=sqlite3.Row
out={'version':'TARGET_ETH_LIQUIDITY_ADD_PROFIT_V1','assets':{},'boundaries':['dominant-side acquisition is an ADD/expansion proxy, not a proven internal Target role label','avg_order_count/levels/spread are public-book liquidity/execution-opportunity proxies, not exchange-reported traded volume','descriptive association only; no causal claim']}
for asset in ['BTC','ETH']:
    by={}
    cur_mid=None; up=down=0.0
    rows=c.execute('select market_id,role,side,shares,first_event_ms,parent_id from target_parent_orders where asset=? order by market_id,first_event_ms,parent_id',(asset,))
    for r in rows:
        mid=int(r['market_id'])
        if mid!=cur_mid: cur_mid=mid; up=down=0.0
        d=by.setdefault(mid,{'dom':0,'weak':0,'maker_dom':0,'maker_weak':0,'taker_dom':0,'taker_weak':0})
        side=r['side']; role=r['role']; sh=float(r['shares'])
        if abs(up-down)>1e-9:
            weak_side='UP' if up<down else 'DOWN'; cls='weak' if side==weak_side else 'dom'; d[cls]+=1; d[role.lower()+'_'+cls]+=1
        if side=='UP': up+=sh
        else: down+=sh
    joined=[]
    for rr in c.execute('select market_id,net_pnl_usdt,buy_notional_usdt,net_roi from target_market_results where asset=?',(asset,)):
        mid=int(rr['market_id']); L=liq.get((asset,mid)); D=by.get(mid)
        if not L or not D: continue
        joined.append({**L,**D,'pnl':float(rr['net_pnl_usdt']),'notional':float(rr['buy_notional_usdt']),'roi':float(rr['net_roi'] or 0)})
    for r in joined:
        den=r['dom']+r['weak']; r['dom_frac']=r['dom']/den if den else None
        den=r['maker_dom']+r['maker_weak']; r['maker_dom_frac']=r['maker_dom']/den if den else None
        den=r['taker_dom']+r['taker_weak']; r['taker_dom_frac']=r['taker_dom']/den if den else None
    A={'joinedMarkets':len(joined),'aggregateDominantFrac':sum(r['dom'] for r in joined)/max(1,sum(r['dom']+r['weak'] for r in joined)),'aggregateMakerDominantFrac':sum(r['maker_dom'] for r in joined)/max(1,sum(r['maker_dom']+r['maker_weak'] for r in joined)),'aggregateTakerDominantFrac':sum(r['taker_dom'] for r in joined)/max(1,sum(r['taker_dom']+r['taker_weak'] for r in joined)),'correlations':{},'quartiles':{}}
    for key in ['avg_order_count','avg_total_levels','updates','avg_spread']:
        A['correlations'][key]={'vsDominantFrac':spear([(r[key],r['dom_frac']) for r in joined]),'vsMakerDominantFrac':spear([(r[key],r['maker_dom_frac']) for r in joined]),'vsTakerDominantFrac':spear([(r[key],r['taker_dom_frac']) for r in joined]),'vsPnl':spear([(r[key],r['pnl']) for r in joined]),'vsRoi':spear([(r[key],r['roi']) for r in joined]),'n':sum(1 for r in joined if r[key] is not None)}
        A['quartiles'][key]=quartiles(joined,key)
    out['assets'][asset]=A
c.close(); Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2))
