from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.build_r2_residual_intervention_curriculum_v0 import run_recovery, OUT, EPS
from tools.hftbacktest_r2_execution_school_v0 import load_reference_paper
DELAYS=(0,1000,3000,5000,10000)

def next_r2_maker_intent(mid:int,start:int):
    paper=load_reference_paper(mid)
    ts=sorted(int(o['placed_at_ms']) for o in paper['orders'] if int(o['placed_at_ms'])>int(start))
    return ts[0] if ts else None

def local_stats(run,start,end,target_net,actual_net0):
    # Integrate tracking error over an event-bounded interval using only actual HFT fills.
    evs=[]
    realized=0.0
    recovery_side=str(run.get('candidateRecoverySide') or '')
    recovery=surplus=0.0
    surplus_side='DOWN' if recovery_side=='UP' else 'UP' if recovery_side=='DOWN' else ''
    for ev in run.get('fillLog') or []:
        t=int(ev.get('eventMs') or 0)
        if t < int(start) or t >= int(end): continue
        side=str(ev.get('side')); q=float(ev.get('shares') or 0.0)
        sign=1.0 if side=='UP' else -1.0 if side=='DOWN' else 0.0
        evs.append((t,sign*q,str(ev.get('role')),side,q))
    evs.sort(key=lambda x:x[0])
    cur=float(actual_net0); last=int(start); area=0.0
    for t,dnet,role,side,q in evs:
        if t>last: area += abs(cur-float(target_net))*(t-last)/1000.0
        cur += dnet
        last=t
        if role=='MAKER':
            realized += q
            if side==recovery_side: recovery += q
            if side==surplus_side: surplus += q
    if int(end)>last: area += abs(cur-float(target_net))*(int(end)-last)/1000.0
    return {'targetErrorArea':area,'makerRealizedShares':realized,'recoveryFillShares':recovery,'surplusFillShares':surplus,'endActualNet':cur}

def label(base,res):
    de=res['targetErrorArea']-base['targetErrorArea']; dr=res['makerRealizedShares']-base['makerRealizedShares']
    if de < -EPS and dr >= -EPS: return 'RESIDUAL_DOMINATES'
    if de > EPS and dr <= EPS: return 'BASE_DOMINATES'
    return 'TRADEOFF'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--market-ids',required=True);ap.add_argument('--output',default='r2_residual_event_pareto_v1.json');a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]; rows=[]
    for i,mid in enumerate(mids,1):
      for delay in DELAYS:
        b=run_recovery(mid,enable_intervention=False,candidate_delay_ms=delay,passive_priority=False)
        r=run_recovery(mid,enable_intervention=False,candidate_delay_ms=delay,passive_priority=True)
        start=b.get('candidateAtMs') or r.get('candidateAtMs')
        if start is None: continue
        end=next_r2_maker_intent(mid,int(start))
        if end is None or int(end)<=int(start): continue
        feat=b.get('candidateFeatures') or r.get('candidateFeatures') or {}
        target=float(feat.get('targetNet') or 0.0); actual0=float(feat.get('actualNet') or 0.0)
        bs=local_stats(b,start,end,target,actual0); rs=local_stats(r,start,end,target,actual0)
        lab=label(bs,rs)
        rows.append({'marketId':mid,'candidateDelayMs':delay,'episodeAtMs':start,'terminationAtMs':end,'durationMs':int(end)-int(start),'termination':'NEXT_FROZEN_R2_MAKER_INTENT','features':feat,
                     'paretoLabel':lab,'baseline':bs,'residual':rs,'deltaTargetErrorArea':rs['targetErrorArea']-bs['targetErrorArea'],'deltaMakerRealizedShares':rs['makerRealizedShares']-bs['makerRealizedShares'],
                     'deltaPnlDiagnostic':float(r['realizedPnl'])-float(b['realizedPnl']) if r.get('realizedPnl') is not None and b.get('realizedPnl') is not None else None})
      print(json.dumps({'progressMarket':i,'marketId':mid,'rows':sum(1 for x in rows if x['marketId']==mid)},ensure_ascii=False),flush=True)
    counts={k:sum(x['paretoLabel']==k for x in rows) for k in ('RESIDUAL_DOMINATES','BASE_DOMINATES','TRADEOFF')}
    rep={'version':'R2_RESIDUAL_EVENT_PARETO_V1','researchOnly':True,'dreamFillAllowed':False,'markets':len(mids),'rows':len(rows),'delaysMs':list(DELAYS),'labelCounts':counts,
         'terminationSemantics':'Evaluate residual option only until the next Frozen R2 Maker intent, then bootstrap back to the base policy decision epoch.',
         'paretoSemantics':{'RESIDUAL_DOMINATES':'Lower event-bounded target-error-area and no reduction in R2-authorized Maker realization.','BASE_DOMINATES':'Higher target-error-area and no realization gain.','TRADEOFF':'All other cases; bootstrap to Frozen R2.'},
         'guardrails':['No winner/PnL/Target in labels or runtime features','No scalar reward weights','No threshold sweep','Actual HftBacktest fills only','Frozen R2 next-intent time defines option termination'],'rowsData':rows}
    out=OUT/a.output;out.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'report':str(out),'rows':len(rows),'counts':counts},ensure_ascii=False))
if __name__=='__main__': main()
