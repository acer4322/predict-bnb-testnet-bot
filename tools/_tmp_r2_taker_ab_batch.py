import importlib.util,sqlite3,json,sys
from pathlib import Path
p=Path('tools/hftbacktest_r2_execution_school_v0.py'); spec=importlib.util.spec_from_file_location('h',p); h=importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
markets=[int(x) for x in sys.argv[1].split(',')]
eng=sqlite3.connect('data/echtgeld_engine_v1.db'); winners={int(r[0]):str(r[1]).upper() for r in eng.execute('select market_id,winner from engine_settlements where market_id in (%s)'%(','.join('?'*len(markets))),markets).fetchall()}; eng.close()
def s(r,w):
 z=r['studentRollout']; fp=z['finalPortfolio']; gross=float(fp.get('combined_gross') or 0); net=float(fp.get('combined_net') or 0); up=(gross+net)/2; dn=(gross-net)/2; cost=float(z.get('makerCostUsdt') or 0)+float(z.get('takerCostUsdt') or 0)+float(z.get('takerFeesUsdt') or 0); pnl=(up if w=='UP' else dn)-cost if w in ('UP','DOWN') else None
 return {'pnl':pnl,'up':up,'down':dn,'cost':cost,'absNet':float(fp.get('combined_abs_net') or 0),'takerFills':int(z.get('takerFills') or 0),'takerShares':float(z.get('takerFilledShares') or 0),'makerShares':float(z.get('makerFilledShares') or 0),'decisions':int(z.get('decisions') or 0)}
out=[]
for mid in markets:
 w=winners.get(mid)
 try:
  a=s(h.run_market(mid,taker_sizing_mode='fixed'),w); b=s(h.run_market(mid,taker_sizing_mode='dynamic_state_gap'),w)
  row={'marketId':mid,'winner':w,'fixed18':a,'unlocked':b,'deltaPnl':(b['pnl']-a['pnl']) if a['pnl'] is not None and b['pnl'] is not None else None,'deltaAbsNet':b['absNet']-a['absNet'],'trajectoryChanged':abs(b['takerShares']-a['takerShares'])>1e-9 or abs(b['absNet']-a['absNet'])>1e-9}
 except Exception as e: row={'marketId':mid,'winner':w,'error':f'{type(e).__name__}: {e}'}
 out.append(row); print(json.dumps(row,ensure_ascii=False),flush=True)
with Path('data/research/execution_aware_fill_lifecycle_v0/r2_taker_fixed18_unlocked_hft_ab_30_rows.jsonl').open('a',encoding='utf-8') as f:
 for row in out: f.write(json.dumps(row,ensure_ascii=False)+'\n')
