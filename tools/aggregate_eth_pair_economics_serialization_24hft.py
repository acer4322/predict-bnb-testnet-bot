from __future__ import annotations
import json, math
from pathlib import Path
from collections import defaultdict

ROOT=Path(__file__).resolve().parents[1]
RETURN=ROOT/'data/research/lan_worker_returns'
OUTJ=ROOT/'data/research/r4_v0/p0_provenance_v1/ETH_PAIR_ECONOMICS_SERIALIZATION_24HFT_RESULT_20260905.json'
OUTM=ROOT/'data/research/r4_v0/p0_provenance_v1/ETH_PAIR_ECONOMICS_SERIALIZATION_24HFT_SYNTHESIS_20260905.md'
CELLS=['PAIR_ECONOMICS_1SLOT','PAIR_ECONOMICS_4SLOT','PAIR_PLUS_SERIALIZATION_1SLOT','PAIR_PLUS_SERIALIZATION_4SLOT']
rows=[]
for i in range(1,5):
    rows += json.load(open(RETURN/f'eth-pair-serial-24hft-s{i}-20260905-v1'/'result.json',encoding='utf-8'))['rows']
by={(int(r['marketId']),r['cell']):r for r in rows}
markets=sorted({int(r['marketId']) for r in rows})

def agg(cell):
    xs=[r for r in rows if r['cell']==cell]
    pn=[float(r['pnlDiagnosticOnly']) for r in xs]; fl=[float(r['floor']) for r in xs]
    wins=[x for x in pn if x>1e-9]; losses=[x for x in pn if x<-1e-9]
    ts=sum(int(r['submits']) for r in xs); tf=sum(int(r['fillEvents']) for r in xs)
    grosswin=sum(wins); grossloss=-sum(losses)
    cum=0.0; peak=0.0; maxdd=0.0
    for mid in markets:
        p=float(by[mid,cell]['pnlDiagnosticOnly']); cum+=p; peak=max(peak,cum); maxdd=max(maxdd,peak-cum)
    return {
        'markets':len(xs),'tradeCoverage':sum(int(r['fillEvents'])>0 for r in xs)/len(xs),
        'totalSubmits':ts,'totalFills':tf,'avgSubmitsPerMarket':ts/len(xs),'avgFillsPerMarket':tf/len(xs),'fillToSubmit':tf/ts,
        'wins':len(wins),'losses':len(losses),'flats':len(xs)-len(wins)-len(losses),'winRate':len(wins)/len(xs),
        'grossWin':grosswin,'grossLoss':grossloss,'profitFactor':grosswin/grossloss if grossloss else None,
        'totalPnl':sum(pn),'avgPnl':sum(pn)/len(xs),'pnlPerSubmit':sum(pn)/ts,'pnlPerFill':sum(pn)/tf,
        'avgWin':sum(wins)/len(wins) if wins else None,'avgLoss':sum(losses)/len(losses) if losses else None,
        'bestWin':max(wins) if wins else 0.0,'worstLoss':min(losses) if losses else 0.0,'maxSequentialMarketDrawdown':maxdd,
        'terminalFloorSum':sum(fl),'terminalFloorAvg':sum(fl)/len(fl),'terminalFloorWorst':min(fl),
        'lossesLt1':sum(1 for x in losses if abs(x)<1),'lossesLe1':sum(1 for x in losses if abs(x)<=1),
    }
summary={c:agg(c) for c in CELLS}

p1=summary['PAIR_ECONOMICS_1SLOT']; p4=summary['PAIR_ECONOMICS_4SLOT']; s1=summary['PAIR_PLUS_SERIALIZATION_1SLOT']; s4=summary['PAIR_PLUS_SERIALIZATION_4SLOT']
matched=[]; improved=worsened=same=0
for mid in markets:
    a=by[mid,'PAIR_ECONOMICS_4SLOT']; b=by[mid,'PAIR_PLUS_SERIALIZATION_4SLOT']; d=float(b['pnlDiagnosticOnly'])-float(a['pnlDiagnosticOnly'])
    if d>1e-9: improved+=1
    elif d<-1e-9: worsened+=1
    else: same+=1
    matched.append({'marketId':mid,'pair1Pnl':float(by[mid,'PAIR_ECONOMICS_1SLOT']['pnlDiagnosticOnly']),'pair4Pnl':float(a['pnlDiagnosticOnly']),'serialized4Pnl':float(b['pnlDiagnosticOnly']),'serializationPnlDeltaVsPair4':d,'pair4Fills':int(a['fillEvents']),'serialized4Fills':int(b['fillEvents']),'pair4Submits':int(a['submits']),'serialized4Submits':int(b['submits'])})
comparison={
    'pair4VsPair1':{
        'submitMultiple':p4['totalSubmits']/p1['totalSubmits'],'fillMultiple':p4['totalFills']/p1['totalFills'],'pnlMultiple':p4['totalPnl']/p1['totalPnl'],
        'sameWinLossPattern':all((by[m,'PAIR_ECONOMICS_4SLOT']['pnlDiagnosticOnly']>0)==(by[m,'PAIR_ECONOMICS_1SLOT']['pnlDiagnosticOnly']>0) for m in markets),
    },
    'serialized4VsPair4':{
        'submitRetention':s4['totalSubmits']/p4['totalSubmits'],'fillRetention':s4['totalFills']/p4['totalFills'],
        'pnlRetention':s4['totalPnl']/p4['totalPnl'],'winRateDelta':s4['winRate']-p4['winRate'],
        'avgLossImprovement':s4['avgLoss']-p4['avgLoss'],'worstLossImprovement':s4['worstLoss']-p4['worstLoss'],
        'matchedMarketsImproved':improved,'matchedMarketsWorsened':worsened,'matchedMarketsSame':same,
    },
    'serialized4VsPair1':{
        'submitMultiple':s4['totalSubmits']/p1['totalSubmits'],'fillMultiple':s4['totalFills']/p1['totalFills'],
        'totalPnlDelta':s4['totalPnl']-p1['totalPnl'],'winRateDelta':s4['winRate']-p1['winRate'],
        'avgLossDelta':s4['avgLoss']-p1['avgLoss'],'worstLossDelta':s4['worstLoss']-p1['worstLoss'],
    }
}
out={'version':'ETH_PAIR_ECONOMICS_SERIALIZATION_24HFT_RESULT_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'markets':markets,'summary':summary,'comparison':comparison,'matched':matched,'boundary':['96 realistic-HFT cells: 24 markets x 4 variants','Pair economics and Pair+same-side serialization only','1-slot and 4-slot','same-price slots intentionally isolate concurrency rather than price fanout diversification','<=180s fence retained','winner post-hoc only','no Target runtime input','no dream fill','no 8781','not a semantic-cycle controller test']}
OUTJ.write_text(json.dumps(out,indent=2),encoding='utf-8')
md=f'''# ETH Pair Economics vs Pair + Serialization — 24 HFT Synthesis (2026-09-05)\n\n## Aggregate\n\n| Cell | Submits | Fills | Coverage | Win rate | Total PnL | PF | Avg win | Avg loss | Worst loss | Avg terminal Floor |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n| Pair 1-slot | {p1['totalSubmits']} | {p1['totalFills']} | {p1['tradeCoverage']:.1%} | {p1['winRate']:.1%} | {p1['totalPnl']:.3f} | {p1['profitFactor']:.3f} | {p1['avgWin']:.3f} | {p1['avgLoss']:.3f} | {p1['worstLoss']:.3f} | {p1['terminalFloorAvg']:.3f} |\n| Pair 4-slot | {p4['totalSubmits']} | {p4['totalFills']} | {p4['tradeCoverage']:.1%} | {p4['winRate']:.1%} | {p4['totalPnl']:.3f} | {p4['profitFactor']:.3f} | {p4['avgWin']:.3f} | {p4['avgLoss']:.3f} | {p4['worstLoss']:.3f} | {p4['terminalFloorAvg']:.3f} |\n| Pair+Serialization 1-slot | {s1['totalSubmits']} | {s1['totalFills']} | {s1['tradeCoverage']:.1%} | {s1['winRate']:.1%} | {s1['totalPnl']:.3f} | {s1['profitFactor']:.3f} | {s1['avgWin']:.3f} | {s1['avgLoss']:.3f} | {s1['worstLoss']:.3f} | {s1['terminalFloorAvg']:.3f} |\n| Pair+Serialization 4-slot | {s4['totalSubmits']} | {s4['totalFills']} | {s4['tradeCoverage']:.1%} | {s4['winRate']:.1%} | {s4['totalPnl']:.3f} | {s4['profitFactor']:.3f} | {s4['avgWin']:.3f} | {s4['avgLoss']:.3f} | {s4['worstLoss']:.3f} | {s4['terminalFloorAvg']:.3f} |\n\n## Main findings\n\n- Pair 4-slot is exactly a 4x concurrency/exposure scaling of Pair 1-slot on aggregate: submits x{comparison['pair4VsPair1']['submitMultiple']:.3f}, fills x{comparison['pair4VsPair1']['fillMultiple']:.3f}, PnL x{comparison['pair4VsPair1']['pnlMultiple']:.3f}; win/loss pattern is unchanged.\n- Adding same-side serialization to 4-slot retains only {comparison['serialized4VsPair4']['submitRetention']:.1%} of submits and {comparison['serialized4VsPair4']['fillRetention']:.1%} of fills.\n- Serialization improves PnL versus Pair 4-slot in {improved}/24 markets and worsens it in {worsened}/24, mostly by clipping both positive and negative exposure tails.\n- Serialized 4-slot total PnL is {s4['totalPnl']:.3f} vs Pair 4-slot {p4['totalPnl']:.3f}, with win rate {s4['winRate']:.1%} vs {p4['winRate']:.1%}.\n- Serialized 4-slot is only {comparison['serialized4VsPair1']['submitMultiple']:.3f}x Pair 1-slot submits and {comparison['serialized4VsPair1']['fillMultiple']:.3f}x fills, so most multi-slot capacity advantage disappears.\n- None of these variants satisfy the project downside target: there are zero losing markets with magnitude <= 1.\n\n## Interpretation boundary\n\nThis is a bare execution safety experiment. Slots intentionally use the same bid anchor to isolate slot concurrency; therefore Pair 4-slot's exact scaling is exposure multiplication, not evidence that price-fanout multi-slot improves decision quality. No semantic responsibility-cycle claim is made from this experiment.\n'''
OUTM.write_text(md,encoding='utf-8')
print(json.dumps({'ok':True,'summary':summary,'comparison':comparison,'json':str(OUTJ),'md':str(OUTM)},ensure_ascii=False))
