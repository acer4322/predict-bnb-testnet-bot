from __future__ import annotations
import argparse, sqlite3, json, datetime, statistics
from pathlib import Path
REC='data/strategy_target_compare_v1.db'; TGT='data/target_wallet_official_v1.db'
VERS={'10':'R2_S10_P010_DREAM_FORWARD_PAPER','8':'R2_S8_P013_DREAM_FORWARD_PAPER'}
OUTDIR=Path('data/research/tmp_scaled_paper_stats')
def summarize(a):
    wf=[x for x in a if x['fills']>0]; st=[x for x in wf if x['pnl'] is not None]; p=[x['pnl'] for x in st]
    return {'observed':len(a),'with_fills':len(wf),'settled':len(st),'unsettled':len(wf)-len(st),'no_fill':len(a)-len(wf),'wins':sum(x>1e-9 for x in p),'losses':sum(x<-1e-9 for x in p),'total_pnl':sum(p),'mean_pnl':statistics.mean(p) if p else None,'median_pnl':statistics.median(p) if p else None,'max_gain':max(p) if p else None,'max_loss':min(p) if p else None,'decisions':sum(x['decisions'] for x in a),'orders':sum(x['orders'] for x in a),'fills':sum(x['fills'] for x in a)}
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); ap.add_argument('--start-hour',type=int,default=0); ap.add_argument('--end-hour',type=int,default=24); args=ap.parse_args()
    d=datetime.date.fromisoformat(args.date); base=datetime.datetime.combine(d,datetime.time.min)
    start=int((base+datetime.timedelta(hours=args.start_hour)).timestamp()*1000); end=int((base+datetime.timedelta(hours=args.end_hour)).timestamp()*1000)
    r=sqlite3.connect(REC); r.row_factory=sqlite3.Row; r.execute('PRAGMA query_only=ON'); r.execute('PRAGMA busy_timeout=5000')
    t=sqlite3.connect(TGT); t.row_factory=sqlite3.Row; t.execute('PRAGMA query_only=ON')
    markets=t.execute("select market_id,window_end_ms,winner from target_markets where asset='BTC' and window_end_ms>=? and window_end_ms<? order by window_end_ms",(start,end)).fetchall()
    out={'date':args.date,'start_hour':args.start_hour,'end_hour':args.end_hour,'btc_markets':len(markets),'strategies':{}}
    for k,v in VERS.items():
        a=[]
        for m in markets:
            mid=int(m['market_id'])
            dc=r.execute('select count(*) c,min(decision_ms) mn,max(decision_ms) mx from our_decisions indexed by idx_compare_decisions_market_time where market_id=? and strategy_version=?',(mid,v)).fetchone()
            fills=r.execute('select side,price,shares,filled_at_ms from our_fills indexed by idx_compare_fills_market_time where market_id=? and strategy_version=? order by filled_at_ms',(mid,v)).fetchall()
            oc=r.execute("select count(*) c from our_orders indexed by idx_compare_orders_market_time where market_id=? and strategy_version=?",(mid,v)).fetchone()['c']
            if dc['c'] or oc or fills:
                pnl=None
                if m['winner'] in ('UP','DOWN') and fills:
                    cost=sum(float(x['price'])*float(x['shares']) for x in fills); payout=sum(float(x['shares']) for x in fills if x['side']==m['winner']); pnl=payout-cost
                a.append({'market':mid,'end_ms':m['window_end_ms'],'winner':m['winner'],'decisions':dc['c'],'orders':oc,'fills':len(fills),'pnl':pnl,'first_decision_ms':dc['mn'],'last_decision_ms':dc['mx']})
        out['strategies'][k]=a
    OUTDIR.mkdir(parents=True,exist_ok=True); suffix=f'{args.start_hour:02d}-{args.end_hour:02d}'; path=OUTDIR/f'{args.date}_{suffix}.json'; path.write_text(json.dumps(out,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    print(json.dumps({'date':args.date,'hours':suffix,'btc_markets':len(markets),'summary':{k:summarize(v) for k,v in out['strategies'].items()},'artifact':str(path)},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
