from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.build_r2_residual_intervention_curriculum_v0 import run_recovery, OUT, EPS
DELAYS=(0,1000,3000,5000,10000)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--market-ids',required=True);ap.add_argument('--output',default='r2_residual_multicheckpoint_curriculum_v0.json');a=ap.parse_args()
 mids=[int(x) for x in a.market_ids.split(',') if x.strip()]; rows=[]
 for i,mid in enumerate(mids,1):
  for delay in DELAYS:
   b=run_recovery(mid,enable_intervention=False,candidate_delay_ms=delay,passive_priority=False)
   r=run_recovery(mid,enable_intervention=False,candidate_delay_ms=delay,passive_priority=True)
   if b.get('candidateAtMs') is None and r.get('candidateAtMs') is None: continue
   feat=b.get('candidateFeatures') or r.get('candidateFeatures') or {}
   dte=float(r['targetErrorAreaShareSeconds'])-float(b['targetErrorAreaShareSeconds'])
   dexp=float(r['combinedExposureAreaShareSeconds'])-float(b['combinedExposureAreaShareSeconds'])
   rows.append({'marketId':mid,'candidateDelayMs':delay,'episodeAtMs':b.get('candidateAtMs') or r.get('candidateAtMs'),'features':feat,'deltaTargetErrorArea':dte,'deltaExposureArea':dexp,'deltaFinalAbsTrackingError':float(r['finalAbsTrackingError'])-float(b['finalAbsTrackingError']),'deltaPnlDiagnostic':float(r['realizedPnl'])-float(b['realizedPnl']) if r.get('realizedPnl') is not None and b.get('realizedPnl') is not None else None,'value':-dte,'improves':int(dte < -EPS),'harms':int(dte > EPS)})
  print(json.dumps({'progressMarket':i,'marketId':mid,'rows':sum(1 for x in rows if x['marketId']==mid)},ensure_ascii=False),flush=True)
 rep={'version':'R2_RESIDUAL_MULTICHECKPOINT_CURRICULUM_V0','researchOnly':True,'dreamFillAllowed':False,'delaysMs':list(DELAYS),'delaySemantics':'fixed lifecycle-age probes, preregistered from coarse order-age buckets; not PnL tuned','markets':len(mids),'rows':len(rows),'positive':sum(x['improves'] for x in rows),'negative':sum(x['harms'] for x in rows),'guardrails':['Frozen R2 intents unchanged','Strict-past features only','No winner/PnL/Target runtime input','No threshold sweep','Existing queue preserved','Actual HftBacktest fills only'],'rowsData':rows}
 out=OUT/a.output;out.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'report':str(out),'rows':len(rows),'positive':rep['positive'],'negative':rep['negative']},ensure_ascii=False))
if __name__=='__main__': main()
