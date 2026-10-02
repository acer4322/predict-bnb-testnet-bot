from __future__ import annotations
import csv,json,random
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; R=ROOT/'data/research'
A=R/'target_controller_prediction_recent_independent_v276_states.csv'; B=R/'target_controller_prediction_raw_unseen_v277_states.csv'
BLIND=R/'target_execution_arbitration_maker_activity_v4_blind.csv'; REVEAL=R/'target_execution_arbitration_maker_activity_v4_reveal.csv'; REPORT=R/'target_execution_arbitration_maker_activity_v4_report.json'; SEED=2026081813

def read(p):
 with p.open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
def n(r,k,d=None):
 try:return float(r.get(k,''))
 except:return d
def elig(rows):
 return [r for r in rows if n(r,'seconds_left',-1)>=15 and n(r,'seconds_left',999)<=240 and n(r,'prior_taker_parents',0)>0 and n(r,'time_since_last_taker_ms') is not None and n(r,'maker_parents_since_last_taker') is not None and n(r,'time_since_last_maker_ms') is not None]
def pick(rows,seed,limit=None,exclude=set()):
 by={}
 for r in rows:
  m=int(float(r['market_id']))
  if m not in exclude:by.setdefault(m,[]).append(r)
 q=random.Random(seed); ids=sorted(by)
 if limit and len(ids)>limit:ids=sorted(q.sample(ids,limit))
 return [dict(q.choice(by[m])) for m in ids]
def pred(r):
 recent=n(r,'time_since_last_taker_ms',1e18)<=10000; no_rec=n(r,'maker_parents_since_last_taker',999)<=1; cold=n(r,'time_since_last_maker_ms',0)>2000
 return ('TAKER' if recent and no_rec and cold else 'PASSIVE',recent,no_rec,cold)
def save(p,rows):
 fs=[]
 for r in rows:
  for k in r:
   if k not in fs:fs.append(k)
 with p.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=fs);w.writeheader();w.writerows(rows)
def summ(rows):
 z=len(rows); tp=sum(r['prediction']=='TAKER' and r['target']=='TAKER' for r in rows); fp=sum(r['prediction']=='TAKER' and r['target']=='PASSIVE' for r in rows); fn=sum(r['prediction']=='PASSIVE' and r['target']=='TAKER' for r in rows); ok=sum(r['prediction']==r['target'] for r in rows); base=sum(r['target']=='PASSIVE' for r in rows)
 return {'rows':z,'accuracy':ok/z if z else None,'alwaysPassiveAccuracy':base/z if z else None,'takerPrecision':tp/(tp+fp) if tp+fp else None,'takerRecall':tp/(tp+fn) if tp+fn else None,'predicted':dict(Counter(r['prediction'] for r in rows)),'target':dict(Counter(r['target'] for r in rows)),'confusion':dict(Counter(r['prediction']+'->'+r['target'] for r in rows))}
def main():
 a=pick(elig(read(A)),SEED); used={int(float(r['market_id'])) for r in a}; b=pick(elig(read(B)),SEED+1,30,used); out=[]
 for c,rows in [('V276_RECENT',a),('V277_RAW_UNSEEN_UNIQUE',b)]:
  for r in rows:
   p,x,y,z=pred(r); out.append({'cohort':c,'market_id':int(float(r['market_id'])),'sample_ms':int(float(r['sample_ms'])),'seconds_left':n(r,'seconds_left'),'time_since_last_taker_ms':n(r,'time_since_last_taker_ms'),'maker_parents_since_last_taker':n(r,'maker_parents_since_last_taker'),'time_since_last_maker_ms':n(r,'time_since_last_maker_ms'),'maker_streak_age_ms':n(r,'maker_streak_age_ms'),'risk_deficit':n(r,'risk_deficit'),'abs_payoff_gap':n(r,'abs_payoff_gap'),'recent_taker':int(x),'no_recovery':int(y),'maker_cold':int(z),'prediction':p})
 save(BLIND,out)
 labels={}
 for src in (A,B):
  for r in read(src):
   try:labels[(int(float(r['market_id'])),int(float(r['sample_ms'])))]=('TAKER' if int(float(r['taker_within_5s'])) else 'PASSIVE')
   except:pass
 rev=[]
 for r in out:
  q=dict(r);q['target']=labels.get((r['market_id'],r['sample_ms']),'MISSING');rev.append(q)
 save(REVEAL,rev); valid=[r for r in rev if r['target']!='MISSING']; by={c:summ([r for r in valid if r['cohort']==c]) for c in ('V276_RECENT','V277_RAW_UNSEEN_UNIQUE')}; overall=summ(valid)
 verdict='KEEP' if overall['accuracy']>overall['alwaysPassiveAccuracy'] and all(v['accuracy']>=v['alwaysPassiveAccuracy'] for v in by.values()) else ('OBSERVE' if overall['accuracy']>overall['alwaysPassiveAccuracy'] else 'REJECT')
 rep={'reportVersion':'TARGET_EXECUTION_ARBITRATION_MAKER_ACTIVITY_V4','researchOnly':True,'liveChanges':False,'parameterSweep':False,'modelFit':False,'hypothesis':'Suppress recent-Taker escalation while Maker is still recently active.','frozenRule':'last Taker<=10s AND Maker parents since last Taker<=1 AND last Maker>2s => TAKER; else PASSIVE','blindBoundary':{'predictionWrittenBeforeLabelsLoaded':True,'oneRandomStrictPastStatePerMarket':True,'v277ExcludesV276SelectedMarkets':True,'randomSeed':SEED},'sample':{'V276_RECENT':len(a),'V277_RAW_UNSEEN_UNIQUE':len(b)},'overall':overall,'byCohort':by,'verdict':verdict}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
