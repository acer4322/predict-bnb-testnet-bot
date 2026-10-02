from __future__ import annotations
import bisect,csv,json,random
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STATES=ROOT/'data/research/target_controller_hazard_v21_states.csv'
EXCLUDES=[ROOT/'data/research/target_architecture_blind_random_v0_reveal.csv',ROOT/'data/research/target_taker_escalation_burst_urgency_v0_reveal.csv',ROOT/'data/research/target_taker_escalation_recent_taker_v1_reveal.csv']
BLIND=ROOT/'data/research/target_taker_escalation_burst_mode_v2_blind.csv';REVEAL=ROOT/'data/research/target_taker_escalation_burst_mode_v2_reveal.csv';REPORT=ROOT/'data/research/target_taker_escalation_burst_mode_v2_report.json';LOG=ROOT/'data/research/target_taker_escalation_research_log_v3.json'
SEED=2026081804;PER=10

def iv(v):
 try:return int(float(v))
 except:return None
def fv(v):
 try:return float(v)
 except:return None
def excluded():
 s=set()
 for p in EXCLUDES:
  if p.exists():
   with p.open(encoding='utf-8',newline='') as h:
    for r in csv.DictReader(h):
     x=iv(r.get('market_id'))
     if x is not None:s.add(x)
 return s
def write(path,rows):
 fs=list(rows[0]) if rows else []
 with path.open('w',encoding='utf-8',newline='') as h:
  w=csv.DictWriter(h,fieldnames=fs);w.writeheader();w.writerows(rows)
def summ(rows):
 if not rows:return {'rows':0}
 tp=sum(r['blind_action']=='TAKER' and r['target_action_5s']=='TAKER' for r in rows); pp=sum(r['blind_action']=='TAKER' for r in rows); pos=sum(r['target_action_5s']=='TAKER' for r in rows)
 return {'rows':len(rows),'accuracy':sum(r['blind_action']==r['target_action_5s'] for r in rows)/len(rows),'alwaysPassiveAccuracy':sum(r['target_action_5s']=='PASSIVE' for r in rows)/len(rows),
 'takerPrecision':tp/pp if pp else None,'takerRecall':tp/pos if pos else None,'predicted':dict(Counter(r['blind_action'] for r in rows)),'target':dict(Counter(r['target_action_5s'] for r in rows)),'confusion':dict(Counter(f"{r['blind_action']}->{r['target_action_5s']}" for r in rows))}
def main():
 ex=excluded(); raw=defaultdict(list); labels={}
 with STATES.open(encoding='utf-8',newline='') as h:
  for r in csv.DictReader(h):
   mid=iv(r.get('market_id')); t=iv(r.get('sample_ms')); prior=iv(r.get('prior_taker_parents')); sec=fv(r.get('seconds_left')); reg=r.get('regime')
   if None in (mid,t,prior) or reg not in {'ORDINARY_2026_08_17','STRESS_2026_08_16'}:continue
   raw[mid].append((t,prior,reg,sec,fv(r.get('time_since_last_taker_ms')),fv(r.get('maker_streak_age_ms')),fv(r.get('risk_deficit')),fv(r.get('abs_payoff_gap'))))
   labels[(mid,t)]=iv(r.get('taker_within_5s')) or 0
 for a in raw.values():a.sort()
 by=defaultdict(lambda:defaultdict(list))
 for mid,a in raw.items():
  if mid in ex:continue
  times=[x[0] for x in a]
  for t,prior,reg,sec,tsl,msa,risk,gap in a:
   if prior<1 or sec is None or tsl is None or not (15<=sec<=240):continue
   pos=bisect.bisect_right(times,t-30000)-1
   prior30=a[pos][1] if pos>=0 else 0
   activity=max(0,prior-prior30)
   by[reg][mid].append({'market_id':mid,'regime':reg,'sample_ms':t,'seconds_left':sec,'time_since_last_taker_ms':tsl,'taker_parent_activity_30s':activity,'prior_taker_parents':prior,'maker_streak_age_ms':msa,'risk_deficit':risk,'abs_payoff_gap':gap})
 rng=random.Random(SEED); blind=[]; meta={}
 for reg in ('ORDINARY_2026_08_17','STRESS_2026_08_16'):
  mids=sorted(by[reg]);chosen=rng.sample(mids,min(PER,len(mids)));meta[reg]={'eligibleMarkets':len(mids),'sampledMarkets':len(chosen)}
  for mid in chosen:
   r=rng.choice(by[reg][mid]); recent=r['time_since_last_taker_ms']<=10000; mode=r['taker_parent_activity_30s']>=2;action='TAKER' if recent and mode else 'PASSIVE';blind.append({**r,'recent_taker_le_10s':int(recent),'burst_mode_30s_ge_2':int(mode),'blind_action':action})
 blind.sort(key=lambda x:(x['regime'],x['market_id']));write(BLIND,blind)
 # blind is on disk before any future label is attached
 rev=[]
 for r in blind:
  lab=labels[(r['market_id'],r['sample_ms'])];target='TAKER' if lab else 'PASSIVE';rev.append({**r,'target_action_5s':target,'target_taker_within_5s':lab,'exact_5s':int(r['blind_action']==target)})
 write(REVEAL,rev)
 rep={'reportVersion':'TARGET_TAKER_ESCALATION_BURST_MODE_V2','researchOnly':True,'liveChanges':False,'parameterSweep':False,'randomSeed':SEED,
 'hypothesis':'Recent Taker is only actionable when strict-past trailing Taker density shows an active intervention mode.',
 'frozenRule':'time_since_last_taker_ms <= 10000 AND increase in prior_taker_parents over prior 30s >= 2 => TAKER; else PASSIVE',
 'blindBoundary':{'excludedPriorBlindMarkets':len(ex),'futureLabelsAttachedOnlyAfterBlindCsvWritten':True,'oneRandomStrictPastStatePerMarket':True,'secondsLeftRange':[15,240]},'sample':meta,'overall':summ(rev),'byRegime':{reg:summ([r for r in rev if r['regime']==reg]) for reg in ('ORDINARY_2026_08_17','STRESS_2026_08_16')},'outputs':{'blind':str(BLIND),'reveal':str(REVEAL),'report':str(REPORT),'log':str(LOG)}}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
 verdict='KEEP' if rep['overall']['accuracy']>rep['overall']['alwaysPassiveAccuracy'] and all(rep['byRegime'][x]['accuracy']>=rep['byRegime'][x]['alwaysPassiveAccuracy'] for x in rep['byRegime']) else 'OBSERVE' if rep['overall']['accuracy']>=rep['overall']['alwaysPassiveAccuracy'] else 'REJECT'
 log={'reportVersion':'TARGET_TAKER_ESCALATION_RESEARCH_LOG_V3','researchOnly':True,'liveChanges':False,'topic':'strict-past latent intervention mode for WAIT/MAKER vs TAKER arbitration',
 'hypothesis':rep['hypothesis'],'method':rep['blindBoundary']|{'frozenRule':rep['frozenRule'],'randomSeed':SEED,'sample':meta},'result':{'verdict':verdict,'overall':rep['overall'],'byRegime':rep['byRegime']},
 'blindScore':{'accuracy':rep['overall']['accuracy'],'alwaysPassiveBaseline':rep['overall']['alwaysPassiveAccuracy'],'takerPrecision':rep['overall']['takerPrecision'],'takerRecall':rep['overall']['takerRecall']},
 'KEEP':['FAVORABLE/UNFAVORABLE remains portfolio state only','default passive arbitration','Taker burst/state concept'] if verdict!='REJECT' else ['default passive arbitration'],
 'OBSERVE':['recent Taker <=10s','strict-past trailing Taker activity density','risk/gap growth as secondary urgency rather than hard trigger'],
 'REJECT':['long time-since-Taker as escalation trigger','long Maker persistence as escalation trigger','recent Taker alone as universal hard trigger'],
 'nextQuestion':'Test whether trailing intervention-mode state can improve exact WAIT/MAKER/TAKER arbitration when combined with Maker recovery availability, using fresh chronological markets; do not change thresholds unless this V2 is rejected.'}
 LOG.write_text(json.dumps(log,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'verdict':verdict,'overall':rep['overall'],'byRegime':rep['byRegime'],'sample':meta,'report':str(REPORT),'log':str(LOG)},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
