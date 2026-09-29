from __future__ import annotations
import json, random, sqlite3, statistics, math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'strategy_target_compare_v1.db'
HFT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/mature_inventory_manifold_v0_random30.json'
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r2_random_thinning_vs_hft_v0.json'
VER='UNIFIED_PROMOTED_OWNSTATE_V4_R2_RESIDUAL_FORWARD_PAPER'
SEEDS=range(1000)
EPS=1e-9

def pnl(fills,winner):
    up=sum(float(x['shares']) for x in fills if x['side']=='UP')
    dn=sum(float(x['shares']) for x in fills if x['side']=='DOWN')
    cost=sum(float(x['shares'])*float(x['price']) for x in fills)
    return (up if winner=='UP' else dn)-cost

def thin_exact(fills,target_shares,rng):
    fills=[dict(x) for x in fills]
    total=sum(float(x['shares']) for x in fills)
    tgt=max(0.0,min(float(target_shares),total))
    if tgt<=EPS:return []
    # Random order; take complete paper fills until target, then partial final fill.
    idx=list(range(len(fills))); rng.shuffle(idx)
    out=[]; rem=tgt
    for i in idx:
        if rem<=EPS:break
        f=dict(fills[i]); q=min(float(f['shares']),rem); f['shares']=q; out.append(f); rem-=q
    return out

def stats(xs):
    y=sorted(float(x) for x in xs)
    def q(p):
        if not y:return None
        z=(len(y)-1)*p; lo=math.floor(z); hi=math.ceil(z); a=z-lo
        return y[lo]*(1-a)+y[hi]*a
    return {'n':len(y),'mean':statistics.mean(y) if y else None,'median':statistics.median(y) if y else None,'p05':q(.05),'p25':q(.25),'p75':q(.75),'p95':q(.95),'min':y[0] if y else None,'max':y[-1] if y else None}

def main():
    h=json.load(open(HFT,encoding='utf-8'))['rows']
    mids=[int(r['marketId']) for r in h]
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    paper={}
    for mid in mids:
        paper[mid]=[dict(r) for r in con.execute("select side,price,shares,filled_at_ms,fill_id from our_fills where strategy_version=? and market_id=? and upper(channel)='MAKER' order by filled_at_ms,fill_id",(VER,mid))]
    con.close()
    rows=[]
    for r in h:
        mid=int(r['marketId']); w=str(r['winner']); pf=paper[mid]; af=[{'side':x['side'],'price':x['price'],'shares':x['deltaShares']} for x in r.get('makerFills',[])]
        pshares=sum(float(x['shares']) for x in pf); ashares=sum(float(x['shares']) for x in af)
        rows.append({'marketId':mid,'winner':w,'paperMakerFills':len(pf),'paperMakerShares':pshares,'hftMakerShares':ashares,'realizationRatio':ashares/pshares if pshares>EPS else None,'paperMakerPnl':pnl(pf,w),'hftMakerPnl':pnl(af,w)})
    seed_summ=[]; per_market={mid:[] for mid in mids}
    for seed in SEEDS:
        rng=random.Random(20260822+seed); pn=[]
        for rr in rows:
            tf=thin_exact(paper[rr['marketId']],rr['hftMakerShares'],rng); x=pnl(tf,rr['winner']); pn.append(x); per_market[rr['marketId']].append(x)
        seed_summ.append({'seed':seed,'totalPnl':sum(pn),'wins':sum(x>EPS for x in pn),'winRate':sum(x>EPS for x in pn)/len(pn)})
    hft_total=sum(r['hftMakerPnl'] for r in rows); hft_wr=sum(r['hftMakerPnl']>EPS for r in rows)/len(rows)
    paper_total=sum(r['paperMakerPnl'] for r in rows); paper_wr=sum(r['paperMakerPnl']>EPS for r in rows)/len(rows)
    thin_tot=[x['totalPnl'] for x in seed_summ]; thin_wr=[x['winRate'] for x in seed_summ]
    for rr in rows:
        xs=per_market[rr['marketId']]; rr['randomThinPnl']=stats(xs); rr['hftMinusRandomThinMean']=rr['hftMakerPnl']-statistics.mean(xs)
    report={'version':'R2_RANDOM_THINNING_VS_HFT_V0','researchOnly':True,'dreamFillGraduationEvidence':False,
      'question':'Holding each market maker-filled share quantity equal to observed HFT maker realization, does random selection of R2 theoretical Maker fills outperform the state-dependent HFT-selected fills?',
      'guardrails':['R2 theoretical fills are used only for counterfactual diagnosis, never graduation evidence','Each market random-thin total shares exactly match HFT Maker filled shares up to source paper capacity','Winner used only after fill selection for scoring','No threshold/seed selection; 1000 fixed seeds summarized'],
      'coverage':{'markets':len(rows),'seeds':len(seed_summ)},
      'paperFull':{'totalMakerPnl':paper_total,'winRate':paper_wr},
      'hftActualMakerOnly':{'totalMakerPnl':hft_total,'winRate':hft_wr,'realizationRatio':stats([r['realizationRatio'] for r in rows])},
      'randomThinSameQuantity':{'totalMakerPnl':stats(thin_tot),'winRate':stats(thin_wr),'shareSeedsBeatingHftPnl':sum(x>hft_total for x in thin_tot)/len(thin_tot),'shareSeedsBeatingHftWinRate':sum(x>hft_wr for x in thin_wr)/len(thin_wr)},
      'rows':rows}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['coverage','paperFull','hftActualMakerOnly','randomThinSameQuantity']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
