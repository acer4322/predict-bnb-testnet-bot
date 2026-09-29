from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.build_r2_residual_intervention_curriculum_v0 import run_recovery, OUT, EPS
DELAYS=(0,1000,3000,5000,10000)
HORIZONS=(5000,10000,20000)

def local_maker_fills(run, start, horizon):
    end=int(start)+int(horizon)
    out={'UP':0.0,'DOWN':0.0,'TOTAL':0.0}
    for ev in run.get('fillLog') or []:
        if str(ev.get('role'))!='MAKER': continue
        t=int(ev.get('eventMs') or 0)
        if t < int(start) or t > end: continue
        q=float(ev.get('shares') or 0.0); s=str(ev.get('side'))
        out['TOTAL']+=q
        if s in ('UP','DOWN'): out[s]+=q
    return out

def classify(hrows):
    # No scalar reward. Require weak Pareto dominance on local tracking and R2-realization.
    residual_dom=[]; base_dom=[]
    for x in hrows:
        de=x['deltaTargetErrorArea']
        dr=x['deltaMakerRealizedShares']
        residual_dom.append(de < -EPS and dr >= -EPS)
        base_dom.append(de > EPS and dr <= EPS)
    if all(residual_dom): return 'RESIDUAL_DOMINATES'
    if all(base_dom): return 'BASE_DOMINATES'
    return 'TRADEOFF'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--market-ids',required=True);ap.add_argument('--output',default='r2_residual_local_pareto_v1.json');a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    for i,mid in enumerate(mids,1):
      for delay in DELAYS:
        b=run_recovery(mid,enable_intervention=False,candidate_delay_ms=delay,passive_priority=False)
        r=run_recovery(mid,enable_intervention=False,candidate_delay_ms=delay,passive_priority=True)
        start=b.get('candidateAtMs') or r.get('candidateAtMs')
        if start is None: continue
        feat=b.get('candidateFeatures') or r.get('candidateFeatures') or {}
        rec_side=str(b.get('candidateRecoverySide') or r.get('candidateRecoverySide') or '')
        sur_side='DOWN' if rec_side=='UP' else 'UP' if rec_side=='DOWN' else ''
        hs=[]
        for h in HORIZONS:
            bf=local_maker_fills(b,start,h); rf=local_maker_fills(r,start,h)
            bte=float(b.get(f'targetErrorArea{h//1000}s') or 0.0); rte=float(r.get(f'targetErrorArea{h//1000}s') or 0.0)
            hs.append({'horizonMs':h,'baselineTargetErrorArea':bte,'residualTargetErrorArea':rte,'deltaTargetErrorArea':rte-bte,
                       'baselineMakerRealizedShares':bf['TOTAL'],'residualMakerRealizedShares':rf['TOTAL'],'deltaMakerRealizedShares':rf['TOTAL']-bf['TOTAL'],
                       'baselineRecoveryFillShares':bf.get(rec_side,0.0),'residualRecoveryFillShares':rf.get(rec_side,0.0),'deltaRecoveryFillShares':rf.get(rec_side,0.0)-bf.get(rec_side,0.0),
                       'baselineSurplusFillShares':bf.get(sur_side,0.0),'residualSurplusFillShares':rf.get(sur_side,0.0),'deltaSurplusFillShares':rf.get(sur_side,0.0)-bf.get(sur_side,0.0)})
        lab=classify(hs)
        rows.append({'marketId':mid,'candidateDelayMs':delay,'episodeAtMs':start,'recoverySide':rec_side,'features':feat,'paretoLabel':lab,'horizons':hs,
                     'deltaPnlDiagnostic':float(r['realizedPnl'])-float(b['realizedPnl']) if r.get('realizedPnl') is not None and b.get('realizedPnl') is not None else None})
      print(json.dumps({'progressMarket':i,'marketId':mid,'rows':sum(1 for x in rows if x['marketId']==mid)},ensure_ascii=False),flush=True)
    counts={k:sum(x['paretoLabel']==k for x in rows) for k in ('RESIDUAL_DOMINATES','BASE_DOMINATES','TRADEOFF')}
    rep={'version':'R2_RESIDUAL_LOCAL_PARETO_V1','researchOnly':True,'dreamFillAllowed':False,'delaysMs':list(DELAYS),'horizonsMs':list(HORIZONS),'markets':len(mids),'rows':len(rows),'labelCounts':counts,
         'paretoSemantics':{'RESIDUAL_DOMINATES':'At all 5/10/20s horizons residual lowers local target-error-area and does not reduce realized R2-authorized Maker shares.',
                            'BASE_DOMINATES':'At all horizons residual worsens target-error-area and does not increase realized R2-authorized Maker shares.',
                            'TRADEOFF':'All other cases; no scalar reward is imposed.'},
         'guardrails':['Frozen R2 intent tape','Strict-past features only','No winner/PnL in labels/features','No scalar reward weights','Actual HftBacktest fills only','Residual default must remain EXECUTE_AS_R2 for TRADEOFF/uncertain states'],
         'rowsData':rows}
    out=OUT/a.output;out.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'report':str(out),'rows':len(rows),'counts':counts},ensure_ascii=False))
if __name__=='__main__': main()
