from __future__ import annotations
import csv,json,random
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STATES=ROOT/'data/research/target_controller_hazard_v21_states.csv'
PRIOR=[ROOT/'data/research/target_architecture_blind_random_v0_reveal.csv',ROOT/'data/research/target_taker_escalation_burst_urgency_v0_reveal.csv',ROOT/'data/research/target_taker_escalation_recent_taker_v1_reveal.csv',ROOT/'data/research/target_taker_escalation_burst_mode_v2_reveal.csv']
BLIND=ROOT/'data/research/target_execution_arbitration_maker_recovery_v3_blind.csv'; REVEAL=ROOT/'data/research/target_execution_arbitration_maker_recovery_v3_reveal.csv'; REPORT=ROOT/'data/research/target_execution_arbitration_maker_recovery_v3_report.json'; LOG=ROOT/'data/research/target_execution_arbitration_research_log_v4.json'
SEED=2026081805; PER=15

def n(v):
 try:return float(v)
 except:return None
def i(v):
 try:return int(float(v))
 except:return None
def excluded():
 s=set()
 for p in PRIOR:
  if p.exists():
   with p.open(encoding='utf-8',newline='') as f:
    for r in csv.DictReader(f):
     x=i(r.get('market_id'))
     if x is not None:s.add(x)
 return s
def write(path,rows):
 with path.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else []); w.writeheader(); w.writerows(rows)
def summ(rows):
 if not rows:return {'rows':0}
 tp=sum(r['blind_action']=='TAKER' and r['target_action_5s']=='TAKER' for r in rows); pp=sum(r['blind_action']=='TAKER' for r in rows); pos=sum(r['target_action_5s']=='TAKER' for r in rows)
 return {'rows':len(rows),'accuracy':sum(r['blind_action']==r['target_action_5s'] for r in rows)/len(rows),'alwaysPassiveAccuracy':sum(r['target_action_5s']=='PASSIVE' for r in rows)/len(rows),'takerPrecision':tp/pp if pp else None,'takerRecall':tp/pos if pos else None,'predicted':dict(Counter(r['blind_action'] for r in rows)),'target':dict(Counter(r['target_action_5s'] for r in rows)),'confusion':dict(Counter(f"{r['blind_action']}->{r['target_action_5s']}" for r in rows))}
def main():
 ex=excluded(); by=defaultdict(lambda:defaultdict(list)); labels={}
 feature_cols=['market_id','segment_id','regime','sample_ms','seconds_left','risk_deficit','abs_payoff_gap','worst_case_pnl','payoff_gap','prior_maker_parents','prior_taker_parents','time_since_last_maker_ms','time_since_last_taker_ms','maker_streak_age_ms','maker_parents_since_last_taker','maker_shares_since_last_taker','maker_notional_since_last_taker','risk_growth_5s','gap_growth_5s','maker_gap_growth_5s']
 with STATES.open(encoding='utf-8',newline='') as f:
  for r in csv.DictReader(f):
   mid=i(r.get('market_id')); sec=n(r.get('seconds_left')); prior=i(r.get('prior_taker_parents')); sm=i(r.get('sample_ms'))
   if mid is None or mid in ex or sm is None or prior is None or prior<1 or sec is None or not 15<=sec<=240:continue
   reg=r.get('regime','')
   if reg not in {'ORDINARY_2026_08_17','STRESS_2026_08_16'}:continue
   feat={k:r.get(k) for k in feature_cols}; by[reg][mid].append(feat)
   labels[(mid,sm)]={'taker':i(r.get('taker_within_5s')) or 0}
 rng=random.Random(SEED); blind=[]; meta={}
 for reg in ('ORDINARY_2026_08_17','STRESS_2026_08_16'):
  mids=sorted(by[reg]); chosen=rng.sample(mids,min(PER,len(mids))); meta[reg]={'eligibleMarkets':len(mids),'sampledMarkets':len(chosen)}
  for mid in chosen:
   r=rng.choice(by[reg][mid]); recent=(n(r['time_since_last_taker_ms']) or 1e99)<=10000; maker_since=n(r['maker_parents_since_last_taker']); no_recovery=maker_since is not None and maker_since<=1
   # Frozen hypothesis: a recent Taker only escalates if Maker has not yet re-established a recovery sequence.
   action='TAKER' if recent and no_recovery else 'PASSIVE'
   blind.append({**r,'recent_taker_le_10s':int(recent),'maker_parents_since_last_taker_le_1':int(no_recovery),'blind_action':action})
 blind.sort(key=lambda r:(r['regime'],i(r['market_id']))); write(BLIND,blind)
 reveal=[]
 for r in blind:
  lab=labels[(i(r['market_id']),i(r['sample_ms']))]; target='TAKER' if lab['taker'] else 'PASSIVE'; reveal.append({**r,'target_action_5s':target,'exact_5s':int(target==r['blind_action'])})
 write(REVEAL,reveal)
 overall=summ(reveal); regimes={x:summ([r for r in reveal if r['regime']==x]) for x in ('ORDINARY_2026_08_17','STRESS_2026_08_16')}
 verdict='KEEP' if overall['accuracy']>overall['alwaysPassiveAccuracy'] and (overall['takerPrecision'] or 0)>=.5 else 'OBSERVE' if (overall['takerPrecision'] or 0)>=.4 else 'REJECT'
 report={'reportVersion':'TARGET_EXECUTION_ARBITRATION_MAKER_RECOVERY_V3','researchOnly':True,'liveChanges':False,'parameterSweep':False,'topic':'Maker recovery availability after a recent Taker burst','hypothesis':'Recent Taker should escalate again only while Maker recovery has not re-established: time_since_last_taker<=10s AND maker_parents_since_last_taker<=1.','blindBoundary':{'excludedPriorBlindMarkets':len(ex),'futureLabelsAttachedOnlyAfterBlindCsvWritten':True,'oneRandomStrictPastStatePerMarket':True,'secondsLeftRange':[15,240]},'frozenRule':'recent_taker<=10s AND maker_parents_since_last_taker<=1 => TAKER; else PASSIVE','sample':meta,'overall':overall,'byRegime':regimes,'verdict':verdict}
 REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 log={'reportVersion':'TARGET_EXECUTION_ARBITRATION_RESEARCH_LOG_V4','researchOnly':True,'liveChanges':False,'topic':report['topic'],'hypothesis':report['hypothesis'],'literatureReview':['V3: recent Taker alone had unstable precision and did not beat always-passive overall.','V3: adding trailing 30s Taker density was rejected, so burst density is not the missing gate.','hazard_v21 exposes strict-past maker_parents_since_last_taker, allowing a direct Maker-recovery gate without future labels.'],'method':report['blindBoundary']|{'frozenRule':report['frozenRule'],'randomSeed':SEED,'sample':meta},'result':{'verdict':verdict,'overall':overall,'byRegime':regimes},'blindScore':overall,'KEEP':['default passive arbitration']+(['recent-Taker gated by absent Maker recovery'] if verdict=='KEEP' else []),'OBSERVE':['Maker recovery availability','recent Taker as urgency component'],'REJECT':([] if verdict!='REJECT' else ['recent Taker + maker_parents_since_last_taker<=1 as hard Taker trigger']),'nextQuestion':'If rejected, stop binary Taker-trigger composition and test whether strict-past Maker recovery features predict MAKER vs WAIT conditional on PASSIVE, then keep Taker escalation as a separate hazard component.'}
 LOG.write_text(json.dumps(log,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'verdict':verdict,'excluded':len(ex),'sample':meta,'overall':overall,'byRegime':regimes},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
