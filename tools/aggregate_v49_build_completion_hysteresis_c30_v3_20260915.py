from __future__ import annotations
import json,gzip,statistics,math,pathlib
ROOT=pathlib.Path(__file__).resolve().parents[1];R=ROOT/'data/research'
CAP=R/'market_capsule_v1/source_bundle_v49_generalization_c30_20260914_v1'
markets=[json.loads(x) for x in (CAP/'market_results.jsonl').read_text().splitlines() if x.strip()]
WM={int(x['market_id']):x['winner'] for x in markets};IDS=[int(x['market_id']) for x in markets]
acts=[json.loads(x) for x in (CAP/'target_actions.jsonl').read_text().splitlines() if x.strip()]
by={m:[] for m in IDS}
for a in acts:
 m=int(a['market_id'])
 if m in by and a.get('quote_type')=='BID' and a.get('side') in ('UP','DOWN') and float(a.get('shares') or 0)>0:by[m].append(a)
baseline=json.loads((R/'BTC5M_V49_C30_OFFICIAL_WINNER_ORACLE_FIXED_V1_20260914_RAW.json').read_text())
B={int(x['market_id']):x for x in baseline['rows']}
teacher=json.loads((ROOT/'.lan_worker_v1/target_build_complete_teacher_v1_20260915/summary.json').read_text())
train=set(int(x) for x in teacher['train_markets']);unseen=set(IDS)-train
rows=[]
for m in IDS:
 p=R/'lan_worker_returns'/f'v49-build-complete-c30-{m}-20260915-v3'
 d=json.loads((p/'result.json').read_text());tr=json.load(gzip.open(p/'clock_trace.json.gz','rt'))
 w=WM[m];opp='DOWN' if w=='UP' else 'UP';inv=d['final_inventory'];cost=float(d['final_cost']);actual=float(inv[w])-cost;op=float(inv[opp])-cost
 # Target economics
 tinv={'UP':0.0,'DOWN':0.0};tcost=0.0
 for a in by[m]:tinv[a['side']]+=float(a['shares']);tcost+=float(a['shares'])*float(a['price'])
 ta=tinv[w]-tcost;to=tinv[opp]-tcost
 gr=tr.get('addition_growth_rows',[]);events=[]
 for z in gr:
  ev=z.get('build_complete_event')
  if ev and ev not in events:events.append(ev)
 latch=events[0] if events else None
 start=min((int(s['t']) for s in tr.get('states',[]) if 't' in s),default=None);end=max((int(s['t']) for s in tr.get('states',[]) if 't' in s),default=None)
 latch_phase=((int(latch['t'])-start)/(end-start) if latch and start is not None and end and end>start else None)
 b=B[m]
 rows.append(dict(market_id=m,winner=w,teacher_train=m in train,status=d['status'],safety=bool(d.get('safety_gate',{}).get('pass')),actual=actual,opposite=op,cost=cost,roi=actual/cost if cost else None,event_batches=(d.get('our_profile') or {}).get('event_batches'),submits=d.get('submits'),active_submits=d.get('active_native_submits'),passive_fill_qty=d.get('passive_fill_qty'),active_fill_qty=d.get('active_fill_qty'),latch=bool(latch),latch_phase=latch_phase,latch_t=(latch or {}).get('t'),latch_probability=(latch or {}).get('probability'),baseline_actual=float(b['actual']),baseline_opposite=float(b['opposite']),baseline_cost=float(b['cost']),target_actual=ta,target_opposite=to,target_cost=tcost,target_events=len(by[m])))

def summary(sel):
 x=[r for r in rows if r['market_id'] in sel];n=len(x)
 def avg(k):return sum(float(r[k]) for r in x)/n
 return dict(n=n,safety_pass=sum(r['safety'] for r in x),wins=sum(r['actual']>0 for r in x),win_rate=sum(r['actual']>0 for r in x)/n,both_positive=sum(r['actual']>0 and r['opposite']>0 for r in x),both_negative=sum(r['actual']<0 and r['opposite']<0 for r in x),avg_actual=avg('actual'),median_actual=statistics.median(r['actual'] for r in x),total_actual=sum(r['actual'] for r in x),avg_opposite=avg('opposite'),median_opposite=statistics.median(r['opposite'] for r in x),avg_cost=avg('cost'),avg_roi=avg('roi'),avg_event_batches=sum((r['event_batches'] or 0) for r in x)/n,avg_target_events=avg('target_events'),latch_markets=sum(r['latch'] for r in x),median_latch_phase=statistics.median([r['latch_phase'] for r in x if r['latch_phase'] is not None]) if any(r['latch_phase'] is not None for r in x) else None,improve_actual_vs_baseline=sum(r['actual']>r['baseline_actual'] for r in x),improve_opposite_vs_baseline=sum(r['opposite']>r['baseline_opposite'] for r in x),improve_both_vs_baseline=sum(r['actual']>r['baseline_actual'] and r['opposite']>r['baseline_opposite'] for r in x),avg_baseline_actual=avg('baseline_actual'),avg_baseline_opposite=avg('baseline_opposite'),avg_target_actual=avg('target_actual'),avg_target_opposite=avg('target_opposite'))
# standard target one-sided wins subset
one_sided={r['market_id'] for r in rows if r['target_actual']>0 and r['target_opposite']<0}
S={'status':'PASS','rows':rows,'summary_full':summary(set(IDS)),'summary_teacher_train':summary(train),'summary_unseen':summary(unseen),'summary_target_one_sided':summary(one_sided),'teacher_train_markets':sorted(train),'unseen_markets':sorted(unseen),'target_one_sided_markets':sorted(one_sided)}
(R/'BTC5M_BUILD_COMPLETION_HYSTERESIS_C30_V3_20260915_RESULT.json').write_text(json.dumps(S,indent=2,ensure_ascii=False)+'\n')
print(json.dumps({k:v for k,v in S.items() if k.startswith('summary_')},indent=2,ensure_ascii=False))
print('WORST_ACTUAL',[(r['market_id'],round(r['actual'],1),round(r['opposite'],1),round(r['baseline_actual'],1),round(r['baseline_opposite'],1),r['latch'],None if r['latch_phase'] is None else round(r['latch_phase'],3)) for r in sorted(rows,key=lambda z:z['actual'])[:10]])
print('BEST_ACTUAL',[(r['market_id'],round(r['actual'],1),round(r['opposite'],1),round(r['baseline_actual'],1),round(r['baseline_opposite'],1)) for r in sorted(rows,key=lambda z:-z['actual'])[:10]])
print('UNSEEN',[(r['market_id'],round(r['actual'],1),round(r['opposite'],1),round(r['baseline_actual'],1),round(r['baseline_opposite'],1),r['latch']) for r in rows if r['market_id'] in unseen])
