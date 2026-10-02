import importlib.util,sqlite3,json,statistics,traceback
from pathlib import Path
p=Path('tools/hftbacktest_r2_execution_school_v0.py'); spec=importlib.util.spec_from_file_location('h',p); h=importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
markets=[1675579,1675138,1674459,1667540,1663753,1605173,1604982,1604746,1604541,1646830,1644347,1651446,1651426,1651098,1649541,1636523,1606795,1606755,1606593,1621512,1611312,1611408,1634833,1633380,1658035,1609438,1579874,1579674,1579313,1579116]
outdir=Path('data/research/execution_aware_fill_lifecycle_v0'); rowsf=outdir/'r2_taker_fixed18_unlocked_hft_ab_30_rows.jsonl'; statusf=outdir/'r2_taker_fixed18_unlocked_hft_ab_30_status.json'; reportf=outdir/'r2_taker_fixed18_unlocked_hft_ab_30_v1.json'
rowsf.write_text('',encoding='utf-8'); statusf.write_text(json.dumps({'state':'RUNNING','completed':0,'total':30}),encoding='utf-8')
eng=sqlite3.connect('data/echtgeld_engine_v1.db'); q='select market_id,winner from engine_settlements where market_id in (%s)'%(','.join('?'*len(markets))); winners={int(a):str(b).upper() for a,b in eng.execute(q,markets)}; eng.close()
def s(r,w):
 z=r['studentRollout']; fp=z['finalPortfolio']; gross=float(fp.get('combined_gross') or 0); net=float(fp.get('combined_net') or 0); up=(gross+net)/2; dn=(gross-net)/2; cost=float(z.get('makerCostUsdt') or 0)+float(z.get('takerCostUsdt') or 0)+float(z.get('takerFeesUsdt') or 0); pnl=(up if w=='UP' else dn)-cost if w in ('UP','DOWN') else None
 return {'pnl':pnl,'up':up,'down':dn,'cost':cost,'absNet':float(fp.get('combined_abs_net') or 0),'takerFills':int(z.get('takerFills') or 0),'takerShares':float(z.get('takerFilledShares') or 0),'makerShares':float(z.get('makerFilledShares') or 0),'decisions':int(z.get('decisions') or 0)}
rows=[]
for i,mid in enumerate(markets,1):
 w=winners.get(mid)
 try:
  a=s(h.run_market(mid,taker_sizing_mode='fixed'),w); b=s(h.run_market(mid,taker_sizing_mode='dynamic_state_gap'),w)
  row={'marketId':mid,'winner':w,'fixed18':a,'unlocked':b,'deltaPnl':(b['pnl']-a['pnl']) if a['pnl'] is not None and b['pnl'] is not None else None,'deltaAbsNet':b['absNet']-a['absNet'],'trajectoryChanged':abs(b['takerShares']-a['takerShares'])>1e-9 or abs(b['absNet']-a['absNet'])>1e-9}
 except Exception as e: row={'marketId':mid,'winner':w,'error':f'{type(e).__name__}: {e}','trace':traceback.format_exc()[-2000:]}
 rows.append(row)
 with rowsf.open('a',encoding='utf-8') as f: f.write(json.dumps(row,ensure_ascii=False)+'\n')
 statusf.write_text(json.dumps({'state':'RUNNING','completed':i,'total':30,'lastMarket':mid,'lastRow':row},ensure_ascii=False,indent=2),encoding='utf-8')
valid=[x for x in rows if 'fixed18' in x]; pv=[x for x in valid if x['deltaPnl'] is not None]
agg={'requestedMarkets':30,'validMarkets':len(valid),'settledPnlMarkets':len(pv),'trajectoryChanged':sum(x['trajectoryChanged'] for x in valid),'fixedPnlSum':sum(x['fixed18']['pnl'] for x in pv),'unlockedPnlSum':sum(x['unlocked']['pnl'] for x in pv),'deltaPnlSum':sum(x['deltaPnl'] for x in pv),'betterPnlMarkets':sum(x['deltaPnl']>1e-9 for x in pv),'worsePnlMarkets':sum(x['deltaPnl']<-1e-9 for x in pv),'equalPnlMarkets':sum(abs(x['deltaPnl'])<=1e-9 for x in pv),'meanDeltaPnl':statistics.mean([x['deltaPnl'] for x in pv]) if pv else None,'medianDeltaPnl':statistics.median([x['deltaPnl'] for x in pv]) if pv else None,'fixedAbsNetSum':sum(x['fixed18']['absNet'] for x in valid),'unlockedAbsNetSum':sum(x['unlocked']['absNet'] for x in valid),'deltaAbsNetSum':sum(x['deltaAbsNet'] for x in valid),'fixedTakerSharesSum':sum(x['fixed18']['takerShares'] for x in valid),'unlockedTakerSharesSum':sum(x['unlocked']['takerShares'] for x in valid)}
report={'version':'R2_TAKER_FIXED18_UNLOCKED_HFT_AB_30_V1','researchOnly':True,'cohort':markets,'selection':'First 30 current-Frozen-R2 markets with >=30 archived decision snapshots and Execution Tape V1, fixed before A/B PnL evaluation; no PnL-based selection.','A':'fixed 18-share Taker','B':'dynamic_state_gap; same R2 timing/side/Maker/HFT','aggregate':agg,'rows':rows,'caveat':'HFT diagnostic only; not authoritative real-market outcome.'}
reportf.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); statusf.write_text(json.dumps({'state':'DONE','completed':30,'total':30,'aggregate':agg},ensure_ascii=False,indent=2),encoding='utf-8')
