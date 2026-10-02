import json, glob, sqlite3, statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
RET=ROOT/'data/research/lan_worker_returns'
DB=ROOT/'data/target_wallet_official_v1.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_maker_prearm_cancel_wave24_postepisode_score_v1.json'
NOTE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_management_testbed_progress_20260830_maker_prearm_cancel_postepisode_v1.md'
files=sorted(RET.glob('r4-makeronly-prearmcancel-*-v1/result.json'))
if len(files)!=8: raise SystemExit(f'expected 8 artifacts, got {len(files)}')
rows=[]
for p in files:
    d=json.loads(p.read_text(encoding='utf-8'))
    g=d.get('guards',{})
    if g.get('dreamFill') is not False or g.get('realisticHFT') is not True or g.get('winnerRuntimeInput') is not False:
        raise SystemExit(f'guard mismatch {p}')
    rows.extend(d.get('rows',[]))
mids=sorted({int(r['marketId']) for r in rows})
con=sqlite3.connect(DB)
try:
    winners={}
    for mid in mids:
        q=con.execute("select winner from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone()
        winners[mid]=str(q[0]).upper() if q and q[0] else None
finally: con.close()
missing=[m for m,w in winners.items() if w not in {'UP','DOWN'}]

def score(r):
    w=winners[int(r['marketId'])]
    if w not in {'UP','DOWN'}: return None
    f=r['final']; payout=float(f['up'] if w=='UP' else f['down']); pnl=payout-float(f['cost'])
    return {'winner':w,'pnlUsdt':pnl,'positive':pnl>0}
for r in rows: r['_postScore']=score(r)
configs=sorted({(int(r['leadMs']),str(r['policy'])) for r in rows})
bycfg={}
for cfg in configs:
    rr=[r for r in rows if (int(r['leadMs']),str(r['policy']))==cfg and r['_postScore']]
    wins=sum(r['_postScore']['positive'] for r in rr); pnl=sum(r['_postScore']['pnlUsdt'] for r in rr)
    bycfg[f'{cfg[1]}_{cfg[0]}']={
      'markets':len(rr),'wins':wins,'winRate':wins/len(rr) if rr else None,'totalPnlUsdt':pnl,
      'makerFilledShares':sum(float(r.get('makerFilledShares',0)) for r in rr),
      'prearmedOrders':sum(int(r.get('prearmedOrders',0)) for r in rr),'prunedOrders':sum(int(r.get('prunedOrders',0)) for r in rr),
      'medianFinalFloor':statistics.median(float(r['final']['floor']) for r in rr) if rr else None
    }
base=bycfg.get('REACTIVE_0',{})
# paired conversions and retention
by_mid={}
for r in rows:
    if r['_postScore']:
      by_mid.setdefault(int(r['marketId']),{})[(int(r['leadMs']),str(r['policy']))]=r
paired={}
for lead,pol in configs:
    if (lead,pol)==(0,'REACTIVE'): continue
    conv={'LOSS_TO_WIN':0,'WIN_TO_LOSS':0,'WIN_TO_WIN':0,'LOSS_TO_LOSS':0}; deltas=[]; fill_delta=[]
    for mid,m in by_mid.items():
        b=m.get((0,'REACTIVE')); x=m.get((lead,pol))
        if not b or not x: continue
        a=bool(b['_postScore']['positive']); z=bool(x['_postScore']['positive'])
        key=('WIN' if a else 'LOSS')+'_TO_'+('WIN' if z else 'LOSS'); conv[key]+=1
        deltas.append(x['_postScore']['pnlUsdt']-b['_postScore']['pnlUsdt'])
        fill_delta.append(float(x.get('makerFilledShares',0))-float(b.get('makerFilledShares',0)))
    paired[f'{pol}_{lead}']={'conversions':conv,'meanDeltaPnlUsdt':statistics.fmean(deltas) if deltas else None,'medianDeltaPnlUsdt':statistics.median(deltas) if deltas else None,'meanDeltaMakerFilledShares':statistics.fmean(fill_delta) if fill_delta else None}
# rank WIN RATE FIRST, then winner preservation, then pnl
ranking=[]
for k,v in bycfg.items():
    if k=='REACTIVE_0': continue
    c=paired.get(k,{}).get('conversions',{})
    ranking.append({'config':k,**v,'winRateDeltaVsReactive':(v['winRate']-base.get('winRate')) if v.get('winRate') is not None and base.get('winRate') is not None else None,'lossToWin':c.get('LOSS_TO_WIN',0),'winToLoss':c.get('WIN_TO_LOSS',0),'makerActivityRetentionVsReactive':v['makerFilledShares']/base['makerFilledShares'] if base.get('makerFilledShares') else None})
ranking.sort(key=lambda x:(x['wins'],-x['winToLoss'],x['totalPnlUsdt']),reverse=True)
rep={'version':'R4_MAKER_PREARM_CANCEL_WAVE24_POSTEPISODE_SCORE_V1','date':'2026-08-30','researchOnly':True,'actionAuthority':False,
     'source':'existing 8 LAN worker result.json artifacts; no HFT rerun','scientificBoundary':{'strictPastRuntime':True,'realisticHFT':True,'dreamFill':False,'winnerUse':'post-episode scoring only','freshPromotionEvidence':False,'liveMutation':False},
     'markets':len(mids),'missingSettlement':missing,'baseline':base,'configs':bycfg,'pairedVsReactive':paired,'winRateFirstRanking':ranking,
     'conclusion':'Post-episode rescore repairs the worker-bundle settlement omission without rerunning HFT. This is consumed development evidence only; use ranking to decide whether pre-position/cancel lifecycle deserves a fresh or wider market-disjoint test.'}
OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
best=ranking[0] if ranking else None
note=f'''# R4 Maker Flexibility First — prearm/cancel post-episode rescore v1\n\nThe eight LAN HFT artifacts were reused; no heavy job was relaunched. Worker-side `winner=null` was caused by a missing settlement DB, so the main host applied settlement **after** simulation only. Winner never enters runtime features or actions.\n\nMarkets scored: {len(mids)}; missing settlement: {len(missing)}. Reactive baseline: {base.get('wins')}/{base.get('markets')} wins = {(100*base.get('winRate',0)):.1f}%, PnL {base.get('totalPnlUsdt',0):.4f} USDT.\n\nBest development config by WIN RATE FIRST: `{best['config'] if best else 'NONE'}` = {best['wins'] if best else 0}/{best['markets'] if best else 0} wins ({100*best['winRate'] if best else 0:.1f}%), delta {100*best['winRateDeltaVsReactive'] if best else 0:+.1f} pp; LOSS->WIN={best['lossToWin'] if best else 0}, WIN->LOSS={best['winToLoss'] if best else 0}, Maker activity retention={best['makerActivityRetentionVsReactive'] if best else 0:.3f}.\n\nThis remains consumed development evidence, not promotion. No market skipping, no live mutation, no learned action authority, and no fresh cohort consumption occurred.\n'''
NOTE.write_text(note,encoding='utf-8')
print(json.dumps({'markets':len(mids),'missing':len(missing),'baseline':base,'best':best},ensure_ascii=False))
