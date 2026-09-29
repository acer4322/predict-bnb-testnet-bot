from __future__ import annotations
import csv,json,random
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STATES=ROOT/'data/research/target_controller_hazard_v21_states.csv'
EXCLUDES=[ROOT/'data/research/target_architecture_blind_random_v0_reveal.csv',ROOT/'data/research/target_taker_escalation_burst_urgency_v0_reveal.csv']
BLIND=ROOT/'data/research/target_taker_escalation_recent_taker_v1_blind.csv'
REVEAL=ROOT/'data/research/target_taker_escalation_recent_taker_v1_reveal.csv'
REPORT=ROOT/'data/research/target_taker_escalation_recent_taker_v1_report.json'
SEED=2026081803; PER=15

def i(v):
 try:return int(float(v))
 except:return None
def f(v):
 try:return float(v)
 except:return None
def excluded():
 s=set()
 for p in EXCLUDES:
  if p.exists():
   with p.open(encoding='utf-8',newline='') as h:
    for r in csv.DictReader(h):
     x=i(r.get('market_id'))
     if x is not None:s.add(x)
 return s
def write(path,rows):
 fs=list(rows[0]) if rows else []
 with path.open('w',encoding='utf-8',newline='') as h:
  w=csv.DictWriter(h,fieldnames=fs);w.writeheader();w.writerows(rows)
def summ(rows):
 if not rows:return {'rows':0}
 tp=sum(r['blind_action']=='TAKER' and r['target_action_5s']=='TAKER' for r in rows)
 pp=sum(r['blind_action']=='TAKER' for r in rows); pos=sum(r['target_action_5s']=='TAKER' for r in rows)
 return {'rows':len(rows),'accuracy':sum(r['blind_action']==r['target_action_5s'] for r in rows)/len(rows),
 'alwaysPassiveAccuracy':sum(r['target_action_5s']=='PASSIVE' for r in rows)/len(rows),'takerPrecision':tp/pp if pp else None,'takerRecall':tp/pos if pos else None,
 'predicted':dict(Counter(r['blind_action'] for r in rows)),'target':dict(Counter(r['target_action_5s'] for r in rows)),'confusion':dict(Counter(f"{r['blind_action']}->{r['target_action_5s']}" for r in rows))}
def main():
 ex=excluded(); by=defaultdict(lambda:defaultdict(list))
 with STATES.open(encoding='utf-8',newline='') as h:
  for r in csv.DictReader(h):
   mid=i(r.get('market_id')); prior=i(r.get('prior_taker_parents')); sec=f(r.get('seconds_left')); tsl=f(r.get('time_since_last_taker_ms')); reg=r.get('regime')
   if mid is None or mid in ex or prior is None or prior<1 or sec is None or tsl is None or not (15<=sec<=240) or reg not in {'ORDINARY_2026_08_17','STRESS_2026_08_16'}:continue
   # Feature-only copy, no future labels.
   by[reg][mid].append({'market_id':mid,'regime':reg,'sample_ms':i(r['sample_ms']),'seconds_left':sec,'time_since_last_taker_ms':tsl,
    'maker_streak_age_ms':f(r.get('maker_streak_age_ms')),'risk_deficit':f(r.get('risk_deficit')),'abs_payoff_gap':f(r.get('abs_payoff_gap')),
    'risk_growth_5s':f(r.get('risk_growth_5s')),'gap_growth_5s':f(r.get('gap_growth_5s'))})
 rng=random.Random(SEED); blind=[]; meta={}
 for reg in ('ORDINARY_2026_08_17','STRESS_2026_08_16'):
  mids=sorted(by[reg]); chosen=rng.sample(mids,min(PER,len(mids)));meta[reg]={'eligibleMarkets':len(mids),'sampledMarkets':len(chosen)}
  for mid in chosen:
   r=rng.choice(by[reg][mid]); action='TAKER' if r['time_since_last_taker_ms']<=10000 else 'PASSIVE';blind.append({**r,'blind_action':action})
 blind.sort(key=lambda x:(x['regime'],x['market_id']));write(BLIND,blind)
 labels={}
 with STATES.open(encoding='utf-8',newline='') as h:
  for r in csv.DictReader(h):
   key=(i(r.get('market_id')),i(r.get('sample_ms')));labels[key]=i(r.get('taker_within_5s')) or 0
 rev=[]
 for r in blind:
  lab=labels[(r['market_id'],r['sample_ms'])]; target='TAKER' if lab else 'PASSIVE';rev.append({**r,'target_action_5s':target,'target_taker_within_5s':lab,'exact_5s':int(r['blind_action']==target)})
 write(REVEAL,rev)
 rep={'reportVersion':'TARGET_TAKER_ESCALATION_RECENT_TAKER_V1','researchOnly':True,'liveChanges':False,'parameterSweep':False,'randomSeed':SEED,
 'hypothesis':'Immediate Taker escalation is primarily a short-lived continuation state: if a prior Taker occurred within 10 seconds, another Taker is likely; otherwise default passive.',
 'frozenRule':'time_since_last_taker_ms <= 10000 => TAKER; else PASSIVE','blindBoundary':{'excludedPriorBlindMarkets':len(ex),'futureLabelsReadOnlyAfterBlindCsvWritten':True,'oneRandomStrictPastStatePerMarket':True,'secondsLeftRange':[15,240]},
 'sample':meta,'overall':summ(rev),'byRegime':{reg:summ([r for r in rev if r['regime']==reg]) for reg in ('ORDINARY_2026_08_17','STRESS_2026_08_16')},
 'outputs':{'blind':str(BLIND),'reveal':str(REVEAL),'report':str(REPORT)}}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
