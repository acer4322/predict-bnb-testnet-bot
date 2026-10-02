from __future__ import annotations
import argparse, json, random
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]

def main():
 p=argparse.ArgumentParser(); p.add_argument('--states',required=True); p.add_argument('--mode',choices=['blind','reveal'],required=True); p.add_argument('--blind',required=True); p.add_argument('--reveal'); p.add_argument('--report'); p.add_argument('--seed',type=int,default=202608181455); a=p.parse_args()
 d=pd.read_csv(a.states)
 # Eligibility and sampling use strict-past fields only.
 e=d[(d.time_since_last_taker_ms>=30000)&(d.seconds_left>=10)&(d.seconds_left<=90)].copy()
 rng=random.Random(a.seed); rows=[]
 for mid,g in e.groupby('market_id'):
  idx=rng.randrange(len(g)); r=g.iloc[idx]
  pred='TAKER' if float(r.seconds_left)<=30 else 'PASSIVE'
  rows.append({'market_id':int(mid),'sample_ms':int(r.sample_ms),'seconds_left':float(r.seconds_left),'time_since_last_taker_ms':float(r.time_since_last_taker_ms),'maker_parents_since_last_taker':int(r.maker_parents_since_last_taker),'risk_deficit':float(r.risk_deficit),'prediction':pred})
 b=pd.DataFrame(rows).sort_values('market_id')
 if a.mode=='blind': b.to_csv(a.blind,index=False); print(json.dumps({'markets':len(b),'predictedTaker':int((b.prediction=='TAKER').sum()),'blind':a.blind},indent=2)); return
 if not Path(a.blind).exists(): raise SystemExit('blind file missing')
 b=pd.read_csv(a.blind)
 labels=d[['market_id','sample_ms','taker_within_5s','repair_within_5s','add_within_5s','next_taker_delay_ms','next_taker_purpose']]
 r=b.merge(labels,on=['market_id','sample_ms'],how='left',validate='one_to_one')
 r['target']='TAKER'; r.loc[r.taker_within_5s.fillna(0).astype(int)==0,'target']='PASSIVE'
 r['correct']=r.prediction==r.target
 if a.reveal: r.to_csv(a.reveal,index=False)
 tp=int(((r.prediction=='TAKER')&(r.target=='TAKER')).sum()); pp=int((r.prediction=='TAKER').sum()); actual=int((r.target=='TAKER').sum())
 out={'reportVersion':'TARGET_FRESH_INTERVENTION_DEADLINE_CLEANUP_V0','frozenRule':'time_since_last_taker>=30s; sampled state 10-90s left; seconds_left<=30 => TAKER else PASSIVE','parameterSweep':False,'markets':len(r),'accuracy':float(r.correct.mean()),'alwaysPassiveAccuracy':float((r.target=='PASSIVE').mean()),'predictedTaker':pp,'targetTaker':actual,'truePositive':tp,'precision':tp/pp if pp else None,'recall':tp/actual if actual else None}
 if a.report: Path(a.report).write_text(json.dumps(out,indent=2),encoding='utf-8')
 print(json.dumps(out,indent=2))
if __name__=='__main__': main()
