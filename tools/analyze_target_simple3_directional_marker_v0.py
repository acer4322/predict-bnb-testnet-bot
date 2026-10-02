from __future__ import annotations
import bisect,csv,json,statistics
from collections import Counter,defaultdict
from pathlib import Path
import analyze_target_maker_directional_inventory_tolerance_v0 as directional
import analyze_target_maker_taker_inventory_lifecycle_v1 as lifecycle
ROOT=Path(__file__).resolve().parents[1]
RISK=ROOT/'data/research/target_maker_taker_repair_hazard_v2_risk.csv'
PUBLIC=ROOT/'data/research/target_taker_action_burst_hazard_v1.csv'
REPORT=ROOT/'data/research/target_simple3_directional_marker_v0_report.json'

def mean(xs): return statistics.fmean(xs) if xs else None
def blocked(rows,key):
 g=defaultdict(list)
 for r in rows:g[r['market_id']].append(float(r[key]))
 return mean([statistics.fmean(v) for v in g.values() if v])
def load_public():
 d=defaultdict(list)
 with PUBLIC.open(encoding='utf-8',newline='') as f:
  for r in csv.DictReader(f):
   try:d[int(r['market_id'])].append((int(r['decision_sampled_at_ms']),float(r['predict_up_mid']),float(r['spot_minus_strike_bps']),float(r['chainlink_minus_strike_bps'])))
   except:pass
 for v in d.values():v.sort()
 return d
def within(rows):
 g=defaultdict(lambda:defaultdict(list))
 for r in rows:
  if r['state'] in {'FAVORABLE','UNFAVORABLE'}:g[r['market_id']][r['state']].append(r['hold_5s'])
 ds=[];votes=Counter()
 for s in g.values():
  if s['FAVORABLE'] and s['UNFAVORABLE']:
   d=statistics.fmean(s['FAVORABLE'])-statistics.fmean(s['UNFAVORABLE']);ds.append(d);votes['FAVORABLE_HIGHER' if d>0 else 'UNFAVORABLE_HIGHER' if d<0 else 'TIE']+=1
 return {'pairedMarkets':len(ds),'meanHoldDiffFavMinusUnfav':mean(ds),'medianHoldDiff':statistics.median(ds) if ds else None,'votes':dict(votes)}
def summarize(rows):
 return {'rows':len(rows),'markets':len({r['market_id'] for r in rows}),'marketBlockedHold5s':blocked(rows,'hold_5s') if rows else None,'marketBlockedTowardSignalNet5s':blocked(rows,'toward_net_5s') if rows else None}
def main():
 pub=load_public();aidx=directional._load_actions();rows=[]
 with RISK.open(encoding='utf-8',newline='') as f:
  for r in csv.DictReader(f):
   if r['lifecycle_state']!='POST_FIRST_TAKER' or r['phase'] not in {'MID','TAIL'}:continue
   mid=int(r['market_id']);t=int(r['sampled_ms']);arr=pub.get(mid,[]);ts=[x[0] for x in arr];p=bisect.bisect_left(ts,t)-1
   if p<0 or t-arr[p][0]>2000:continue
   _,pm,spot,cl=arr[p]
   signs=[1 if pm>.5 else -1 if pm<.5 else 0,1 if spot>0 else -1 if spot<0 else 0,1 if cl>0 else -1 if cl<0 else 0]
   votes=sum(signs);sig=1 if votes>0 else -1 if votes<0 else 0
   combined=float(r['combined_delta']);alignment=combined*sig;state='FAVORABLE' if alignment>0 else 'UNFAVORABLE' if alignment<0 else 'BALANCED'
   acts,times=aidx.get(mid,([],[]));lo=bisect.bisect_right(times,t);hi=bisect.bisect_right(times,t+5000);future=acts[lo:hi];net=0.0
   for a in future:net+=float(lifecycle._directional_effect(str(a['side']),str(a['quote_type']),float(a['shares'])))
   rows.append({'market_id':mid,'regime':r['regime'],'state':state,'voteStrength':abs(votes),'hold_5s':int(not future),'toward_net_5s':net*sig})
 report={'reportVersion':'TARGET_SIMPLE3_DIRECTIONAL_MARKER_V0','researchOnly':True,'liveChanges':False,'parameterSweep':False,'modelFit':False,'definition':{'signals':['predict_up_mid vs 0.5','spot_minus_strike_bps sign','chainlink_minus_strike_bps sign'],'direction':'2-of-3 majority vote','strong':'3-of-3 agreement','use':'direction marker only; whole-portfolio FAVORABLE iff combined_delta points with marker'},'coverage':{'rows':len(rows),'markets':len({r['market_id'] for r in rows})},'regimes':{}}
 for reg in ('ORDINARY_PRE_SPECIAL','SPECIAL'):
  xs=[r for r in rows if r['regime']==reg]
  report['regimes'][reg]={'overall':{s:summarize([r for r in xs if r['state']==s]) for s in ('FAVORABLE','UNFAVORABLE','BALANCED')},'withinMarket':within(xs),'byVoteStrength':{str(k):{s:summarize([r for r in xs if r['state']==s and r['voteStrength']==k]) for s in ('FAVORABLE','UNFAVORABLE')} for k in (1,3)}}
 REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
