from __future__ import annotations
import bisect, glob, json, lzma, math, sqlite3
from pathlib import Path
import joblib, numpy as np
from sklearn.metrics import roc_auc_score, balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
RET=ROOT/'data/research/lan_worker_returns'
BUNDLE=ROOT/'data/research/lan_worker_bundles/r4_profit_regime_selector_v2_challenge_c_20260831/data/hft_forward_paper_v1/markets'
MODEL=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_profit_regime_selector_v2_frozen.joblib'
CONTRACT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_profit_regime_selector_v2_challenge_c_contract.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_profit_regime_selector_v2_challenge_c_postepisode_score.json'
NOTE=ROOT/'data/research/r4_v0/p0_provenance_v1/R4_MANAGEMENT_TESTBED_PROGRESS_20260831_PROFIT_REGIME_SELECTOR_V2_CHALLENGE_C.md'

def fv(x,d=math.nan):
    try:
        z=float(x); return z if math.isfinite(z) else d
    except Exception:return d

def pnl(r,w):
    f=r['final']; return float(f['up'] if w=='UP' else f['down'])-float(f['cost'])

def latest_decision(mid,t):
    p=BUNDLE/f'{mid}_r2_hft_closed_loop_v1.json.xz'
    if not p.exists(): return {}
    with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
    rr=d.get('decisionRows') or []; ts=[int(x.get('decisionMs') or 0) for x in rr]; j=bisect.bisect_right(ts,int(t))-1
    return rr[j] if j>=0 else {}

def first_features(mid,diag):
    first=next((x for x in (diag.get('continuousActionTrace') or []) if x.get('event')=='PULL_NEGATIVE'),None)
    if first is None:return None
    z=latest_decision(mid,int(first['t'])); m=z.get('models') or {}
    return {'t':int(first['t']),'pMakerSum':fv(m.get('pMakerUp'),0)+fv(m.get('pMakerDown'),0),'pResidualWake':fv(m.get('pResidualWake'),0),'orderAgeS':fv(first.get('orderAgeMs'),0)/1000.0}

def main():
    contract=json.loads(CONTRACT.read_text(encoding='utf-8')); mids=[int(x) for x in contract['marketIds']]
    pair={}; diag={}
    for i in range(8):
        p=RET/f'r4-profit-v2c-pair-{i:02d}'/'result.json'; d=json.loads(p.read_text(encoding='utf-8'))
        if d.get('winnerUsed') is not False or d.get('errors'): raise SystemExit(f'pair guard/error {p}')
        for r in d['rows']: pair[(int(r['marketId']),str(r['config']))]=r
        p=RET/f'r4-profit-v2c-diag-{i:02d}'/'result.json'; d=json.loads(p.read_text(encoding='utf-8'))
        if d.get('winnerUsed') is not False or d.get('errors'): raise SystemExit(f'diag guard/error {p}')
        for r in d['rows']: diag[int(r['marketId'])]=r
    db=sqlite3.connect(ROOT/'data/target_wallet_official_v1.db')
    q=','.join('?'*len(mids)); wins={int(a):str(b).upper() for a,b in db.execute(f'select market_id,winner from target_markets where market_id in ({q})',mids) if str(b).upper() in ('UP','DOWN')}; db.close()
    frozen=joblib.load(MODEL); model=frozen['model']; feats=frozen['features']; th=float(frozen['threshold'])
    rows=[]
    for mid in mids:
        f=first_features(mid,diag[mid]); b=pair[(mid,'FLEX_W5000')]; h=pair[(mid,'CONT_STATE_H2_SUSPEND')]; w=wins.get(mid)
        if f is None or w is None: continue
        x=np.array([[f[k] for k in feats]],float); prob=float(model.predict_proba(x)[0,1]); pred='H2' if prob>=th else 'FLEX'
        bp=pnl(b,w); hp=pnl(h,w); chosen=hp if pred=='H2' else bp
        rows.append({'marketId':mid,'features':f,'probH2':prob,'pred':pred,'winner':w,'flexPnl':bp,'h2Pnl':hp,'h2Delta':hp-bp,'actualBetter':'H2' if hp>bp+1e-9 else 'FLEX','chosenPnlDiagnostic':chosen,'makerFilledFlex':float(b.get('makerFilledShares',0)),'makerFilledH2':float(h.get('makerFilledShares',0))})
    y=np.array([1 if r['actualBetter']=='H2' else 0 for r in rows]); p=np.array([r['probH2'] for r in rows]); pr=np.array([1 if r['pred']=='H2' else 0 for r in rows])
    def wins_by(field): return sum(r[field]>0 for r in rows)
    flex_pnl=sum(r['flexPnl'] for r in rows); h2_pnl=sum(r['h2Pnl'] for r in rows); sel_pnl=sum(r['chosenPnlDiagnostic'] for r in rows); oracle_pnl=sum(max(r['flexPnl'],r['h2Pnl']) for r in rows)
    flex_w=sum(r['flexPnl']>0 for r in rows); h2_w=sum(r['h2Pnl']>0 for r in rows); sel_w=sum(r['chosenPnlDiagnostic']>0 for r in rows)
    conv={'LOSS_TO_WIN':0,'WIN_TO_LOSS':0,'WIN_TO_WIN':0,'LOSS_TO_LOSS':0}
    for r in rows:
        a=r['flexPnl']>0; z=r['chosenPnlDiagnostic']>0; conv[('WIN' if a else 'LOSS')+'_TO_'+('WIN' if z else 'LOSS')]+=1
    out={'version':'R4_PROFIT_REGIME_SELECTOR_V2_CHALLENGE_C_POSTEPISODE_SCORE','researchOnly':True,'actionAuthority':False,'challengeContract':str(CONTRACT.relative_to(ROOT)),'frozenSelector':str(MODEL.relative_to(ROOT)),'scientificBoundary':{'strictPastFeatures':True,'winnerUse':'post-episode scoring only','realisticHFT':True,'dreamFill':False,'causalReplayEvidence':False,'freshPromotionEvidence':False,'reason':'Challenge C is disjoint from selector A/B development but not yet fully audited against project-wide used-market registry.'},'marketsScored':len(rows),'missing':sorted(set(mids)-{r['marketId'] for r in rows}),'selectorMetrics':{'aucH2Better':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'balancedAccuracy':float(balanced_accuracy_score(y,pr)) if len(set(y))>1 else None,'selectedH2':int(pr.sum()),'selectedFLEX':int((1-pr).sum())},'winRateDiagnostic':{'FLEX':{'wins':flex_w,'markets':len(rows),'rate':flex_w/len(rows)},'H2':{'wins':h2_w,'markets':len(rows),'rate':h2_w/len(rows)},'FROZEN_SELECTOR_STITCHED':{'wins':sel_w,'markets':len(rows),'rate':sel_w/len(rows),'conversionsVsFlex':conv}},'pnlDiagnostic':{'FLEX':flex_pnl,'H2':h2_pnl,'FROZEN_SELECTOR_STITCHED':sel_pnl,'ORACLE':oracle_pnl},'makerParticipation':{'flexFilledShares':sum(r['makerFilledFlex'] for r in rows),'h2FilledShares':sum(r['makerFilledH2'] for r in rows),'h2RetentionVsFlex':sum(r['makerFilledH2'] for r in rows)/sum(r['makerFilledFlex'] for r in rows) if sum(r['makerFilledFlex'] for r in rows) else None},'rows':rows,'conclusionBoundary':'This is a frozen-selector generalization diagnostic only. Because it stitches already-run FLEX/H2 trajectories, it is not causal replay policy evidence and cannot establish promotion or superiority versus frozen R3+R3.1.'}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    note=f'''# R4 — Profit Regime Selector V2 Challenge C\n\nAll 16 pre-existing LAN worker jobs were attached/collected with `required_artifact=result.json`; no duplicate HFT launch occurred. Challenge C was frozen before settlement read and is disjoint from selector A/B development, but has not yet passed a full-project never-seen audit, so it is not promotion evidence.\n\nFrozen 3-feature selector (`pMakerSum`, `pResidualWake`, `orderAgeS`) scored {len(rows)} markets. H2-better classification AUC={out['selectorMetrics']['aucH2Better']:.3f}, balanced accuracy={out['selectorMetrics']['balancedAccuracy']:.3f}. It selected H2 on {out['selectorMetrics']['selectedH2']}/{len(rows)} markets.\n\nWIN RATE diagnostic: FLEX {flex_w}/{len(rows)} ({100*flex_w/len(rows):.1f}%), blanket H2 {h2_w}/{len(rows)} ({100*h2_w/len(rows):.1f}%), frozen stitched selector {sel_w}/{len(rows)} ({100*sel_w/len(rows):.1f}%). Selector conversions vs FLEX: {conv}.\n\nPnL diagnostic: FLEX {flex_pnl:.4f}, H2 {h2_pnl:.4f}, stitched selector {sel_pnl:.4f}, oracle {oracle_pnl:.4f} USDT. H2 Maker-fill retention vs FLEX={out['makerParticipation']['h2RetentionVsFlex']:.3f}.\n\nInterpretation: this is an outcome-independent frozen-feature generalization test followed by post-episode scoring, but the policy result is stitched rather than causal. The next valid step is to implement the frozen selector at the first H2 pull seam inside realistic-HFT replay, then compare win rate/winner preservation/Maker participation and only afterward consider a fully fresh market-disjoint promotion cohort.\n'''
    NOTE.write_text(note,encoding='utf-8')
    print(json.dumps({k:v for k,v in out.items() if k!='rows'},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
