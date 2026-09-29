from __future__ import annotations
import glob,json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
bench=json.loads((P/'r4_threeway10_action_enabled_benchmark_v1.json').read_text())
pdelta={int(r['marketId']):float(r['R4_MANAGEMENT_TESTBED']['pnlUsdt'])-float(r['R3_R31_FULL_RESEARCH']['pnlUsdt']) for r in bench['markets']}
rows=[]
for fp in glob.glob(str(P/'r4_threeway10_benchmark_chunk_*_v1.json')):
    j=json.loads(Path(fp).read_text())
    for e in j.get('r4Events',[]):
        if not e.get('forcedCross'):continue
        r={k:e.get(k) for k in e};mid=int(e['marketId']);r['pnlDelta']=pdelta[mid];r['harmfulMarket']=pdelta[mid]<-1e-9
        gap=float(e.get('mf_abs_gap') or 0);r['gapLots18']=gap/18.0;r['weakOutstandingToGap']=float(e.get('mf_weak_unresolved_shares') or 0)/max(gap,1e-9);r['domOutstandingToGap']=float(e.get('mf_dominant_unresolved_shares') or 0)/max(gap,1e-9)
        r['recentDominantFill']=float(e.get('mf_dominant_fill_shares_5s') or 0)>0
        rows.append(r)
def avg(xs,k):
    z=[float(x[k]) for x in xs if x.get(k) is not None and math.isfinite(float(x[k]))];return sum(z)/len(z) if z else None
H=[r for r in rows if r['harmfulMarket']];N=[r for r in rows if not r['harmfulMarket']]
keys=['mf_seconds_left','floor','mf_abs_gap','risk','transition','pFormationPath','mf_weak_active_owners','mf_dominant_active_owners','mf_weak_unresolved_shares','mf_dominant_unresolved_shares','mf_weak_fill_shares_5s','mf_dominant_fill_shares_5s','weakOutstandingToGap','domOutstandingToGap']
summary={'harmfulEvents':len(H),'neutralEvents':len(N),'harmfulMarkets':sorted(set(int(r['marketId']) for r in H)),'neutralMarkets':sorted(set(int(r['marketId']) for r in N)),'means':{'harmful':{k:avg(H,k) for k in keys},'neutral':{k:avg(N,k) for k in keys}},'simplePartitions':{
 'gap_le_18':{'harmful':sum(float(r.get('mf_abs_gap') or 0)<=18+1e-9 for r in H),'neutral':sum(float(r.get('mf_abs_gap') or 0)<=18+1e-9 for r in N)},
 'weak_outstanding_ge_gap':{'harmful':sum(float(r.get('mf_weak_unresolved_shares') or 0)>=float(r.get('mf_abs_gap') or 0)-1e-9 for r in H),'neutral':sum(float(r.get('mf_weak_unresolved_shares') or 0)>=float(r.get('mf_abs_gap') or 0)-1e-9 for r in N)},
 'recent_dom_fill':{'harmful':sum(bool(r['recentDominantFill']) for r in H),'neutral':sum(bool(r['recentDominantFill']) for r in N)},
 'risk_ge_08':{'harmful':sum(float(r.get('risk') or 0)>=.8 for r in H),'neutral':sum(float(r.get('risk') or 0)>=.8 for r in N)}
}}
out={'version':'R4_FORCED_CROSS_HARM_DIAGNOSTIC_V1','researchOnly':True,'consumedCohortDiagnostic':True,'rows':rows,'summary':summary,'interpretation':'Legacy action-enabled R4 has no positive PnL-delta forced-cross market in consumed threeway10. Harm is concentrated in low-gap and/or pipeline/topology states. Use only to define safer authority semantics; never as validation evidence.'}
(P/'r4_forced_cross_harm_diagnostic_v1.json').write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True))
print(json.dumps(summary,indent=2,ensure_ascii=False))
