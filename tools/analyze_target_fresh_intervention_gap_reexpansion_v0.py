from __future__ import annotations
import argparse,csv,json,random
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STATES=ROOT/'data/research/target_controller_hazard_v21_20260818_states.csv'
BLIND=ROOT/'data/research/target_fresh_intervention_gap_reexpansion_v0_blind.csv'
REVEAL=ROOT/'data/research/target_fresh_intervention_gap_reexpansion_v0_reveal.csv'
REPORT=ROOT/'data/research/target_fresh_intervention_gap_reexpansion_v0_report.json'
SEED=202608181356
REGIME='ORDINARY_2026_08_17'  # label retained by V21 script; source window is 2026-08-18 05:20-11:35 Asia/Taipei

def read_rows():
    with STATES.open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))

def eligible(r):
    if r.get('regime')!=REGIME:return False
    try:
        t=float(r['time_since_last_taker_ms']); sl=float(r['seconds_left']); mx=float(r['max_gap_since_taker_reset'])
    except:return False
    return t>=30000 and 15<=sl<=240 and mx>0

def features(r):
    gap=float(r['abs_payoff_gap']); mx=float(r['max_gap_since_taker_reset']); growth=float(r['gap_growth_5s'])
    ratio=gap/mx
    pred='TAKER' if ratio>=0.80 and growth>0 else 'PASSIVE'
    return ratio,growth,pred

def write_csv(path,rows,fields):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)

def blind():
    by={}
    for r in read_rows():
        if eligible(r):by.setdefault(int(float(r['market_id'])),[]).append(r)
    rng=random.Random(SEED); out=[]
    for mid in sorted(by):
        r=rng.choice(by[mid]); ratio,growth,pred=features(r)
        out.append({'market_id':mid,'sample_ms':int(float(r['sample_ms'])),'seconds_left':float(r['seconds_left']),
                    'time_since_last_taker_ms':float(r['time_since_last_taker_ms']),
                    'maker_parents_since_last_taker':float(r['maker_parents_since_last_taker']),
                    'risk_deficit':float(r['risk_deficit']),'abs_payoff_gap':float(r['abs_payoff_gap']),
                    'max_gap_since_taker_reset':float(r['max_gap_since_taker_reset']),
                    'gap_peak_ratio':ratio,'gap_growth_5s':growth,'blind_prediction':pred})
    write_csv(BLIND,out,list(out[0].keys()) if out else [])
    print(json.dumps({'phase':'BLIND','markets':len(out),'predictions':dict(Counter(r['blind_prediction'] for r in out)),'file':str(BLIND)},indent=2))

def reveal():
    if not BLIND.exists():raise SystemExit('blind file missing')
    blind_rows=list(csv.DictReader(BLIND.open(encoding='utf-8',newline='')))
    lookup={(int(float(r['market_id'])),int(float(r['sample_ms']))):r for r in read_rows()}
    out=[]
    for b in blind_rows:
        key=(int(float(b['market_id'])),int(float(b['sample_ms']))); s=lookup[key]
        actual='TAKER' if int(float(s['taker_within_5s']))==1 else 'PASSIVE'
        row=dict(b);row['target_5s']=actual;row['target_purpose']=s.get('next_taker_purpose','');row['match']=int(b['blind_prediction']==actual);out.append(row)
    write_csv(REVEAL,out,list(out[0].keys()) if out else [])
    n=len(out); hits=sum(r['match'] for r in out); actual_t=sum(r['target_5s']=='TAKER' for r in out); pred_t=sum(r['blind_prediction']=='TAKER' for r in out)
    tp=sum(r['blind_prediction']=='TAKER' and r['target_5s']=='TAKER' for r in out)
    precision=tp/pred_t if pred_t else None; recall=tp/actual_t if actual_t else None
    baseline=sum(r['target_5s']=='PASSIVE' for r in out)/n if n else None
    report={'reportVersion':'TARGET_FRESH_INTERVENTION_GAP_REEXPANSION_V0','researchOnly':True,'liveChanges':False,'parameterSweep':False,'modelFit':False,
      'topic':'Fresh Taker intervention after sustained Maker-only run',
      'sourceWindow':'2026-08-18 05:20-11:35 Asia/Taipei (V21 output retains ORDINARY_2026_08_17 regime label)',
      'hypothesis':'After >=30s since prior Taker, Target re-enters with Taker when current whole-portfolio abs payoff gap is back near its post-Taker maximum and still expanding.',
      'frozenRule':'time_since_last_taker_ms>=30000 AND abs_payoff_gap/max_gap_since_taker_reset>=0.80 AND gap_growth_5s>0 => TAKER; else PASSIVE',
      'blindBoundary':{'randomSeed':SEED,'oneRandomEligibleStatePerMarket':True,'blindWrittenBeforeReveal':True,'strictPastInputsOnly':True},
      'result':{'markets':n,'accuracy':hits/n if n else None,'alwaysPassiveAccuracy':baseline,'predictedTaker':pred_t,'targetTaker':actual_t,'truePositive':tp,'takerPrecision':precision,'takerRecall':recall},
      'verdict':'KEEP' if n and hits/n>baseline and precision is not None and precision>=0.5 else 'OBSERVE' if tp>0 else 'REJECT'}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

def main():
    p=argparse.ArgumentParser();p.add_argument('--phase',choices=['blind','reveal'],required=True);a=p.parse_args(); blind() if a.phase=='blind' else reveal()
if __name__=='__main__':main()
