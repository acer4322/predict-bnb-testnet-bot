from __future__ import annotations
import json,pathlib,collections,math
ROOT=pathlib.Path(__file__).resolve().parents[1]
R=ROOT/'data/research/market_capsule_v1/source_bundle_v49_generalization_c30_20260914_v1'
QREF=2436.779291626123
results=[json.loads(x) for x in (R/'market_results.jsonl').read_text().splitlines() if x.strip()]
winners={int(x['market_id']):x['winner'] for x in results}
# exact windows from public snapshots
windows={}
for line in (R/'public_snapshots.jsonl').read_text().splitlines():
    if not line.strip(): continue
    x=json.loads(line);m=int(x['market_id'])
    if m not in windows:
        s=json.loads(x['snapshot_json']);end=int(s['windowEndMs']);windows[m]=(end-300000,end)
acts=[]
for line in (R/'target_actions.jsonl').read_text().splitlines():
    if not line.strip():continue
    a=json.loads(line)
    if a.get('quote_type')=='BID' and a.get('side') in ('UP','DOWN') and float(a.get('shares') or 0)>0:acts.append(a)
by=collections.defaultdict(list)
for a in acts:by[int(a['market_id'])].append(a)
for m in by:by[m].sort(key=lambda a:int(a['event_ms']))
# fixed prior split
S=json.loads((ROOT/'.lan_worker_v1/repair_only_microworld_v2_price_20260914/data_summary.json').read_text())
train=set(S['train_markets']);valid=set(S['validation_markets'])
out=[]
for m,w in winners.items():
    if m not in windows:continue
    weak='DOWN' if w=='UP' else 'UP';start,end=windows[m];aa=by[m]
    # first 25% only = 75 seconds
    for sec in range(75):
        t=start+sec*1000
        past=[a for a in aa if int(a['event_ms'])<t]
        current=[a for a in aa if t<=int(a['event_ms'])<t+1000]
        label=int(any(a['side']==weak for a in current))
        strong_qty=sum(float(a['shares']) for a in past if a['side']==w);weak_qty=sum(float(a['shares']) for a in past if a['side']==weak)
        strong_cost=sum(float(a['shares'])*float(a['price']) for a in past if a['side']==w);weak_cost=sum(float(a['shares'])*float(a['price']) for a in past if a['side']==weak)
        total_cost=strong_cost+weak_cost;gross=strong_qty+weak_qty;gap=strong_qty-weak_qty
        repair_gain=max(0.0,weak_qty-weak_cost);coverage=repair_gain/strong_cost if strong_cost>1e-9 else (1.0 if repair_gain>0 else 0.0)
        # last repair time, amount since, recent flows
        repar=[a for a in past if a['side']==weak];strong=[a for a in past if a['side']==w]
        lr=max((int(a['event_ms']) for a in repar),default=None);ls=max((int(a['event_ms']) for a in strong),default=None)
        strong_since=sum(float(a['shares']) for a in strong if lr is None or int(a['event_ms'])>lr)
        weak_since=sum(float(a['shares']) for a in repar if lr is None or int(a['event_ms'])>lr)
        def recent(side,ms):return sum(float(a['shares']) for a in past if a['side']==side and int(a['event_ms'])>=t-ms)
        # consecutive same-side event streak in chronological action sequence
        streak_side=None;streak_qty=0.;streak_n=0
        for a in reversed(past):
            if streak_side is None:streak_side=a['side']
            if a['side']!=streak_side:break
            streak_qty+=float(a['shares']);streak_n+=1
        out.append(dict(market_id=m,sec=sec,phase=sec/300.0,winner=w,weak=weak,label_repair=label,
          strong_qref=strong_qty/QREF,weak_qref=weak_qty/QREF,gross_qref=gross/QREF,gap_qref=gap/QREF,abs_gap_qref=abs(gap)/QREF,
          weak_to_strong=(weak_qty/strong_qty if strong_qty>1e-9 else 0.0),protection_coverage=min(5.0,max(0.0,coverage)),
          strong_margin_qref=(strong_qty-total_cost)/QREF,signed_weak_margin_qref=(weak_qty-total_cost)/QREF,
          strong_since_repair_qref=strong_since/QREF,seconds_since_repair=(999.0 if lr is None else (t-lr)/1000.0),seconds_since_strong=(999.0 if ls is None else (t-ls)/1000.0),
          strong_fill_3_qref=recent(w,3000)/QREF,strong_fill_5_qref=recent(w,5000)/QREF,strong_fill_10_qref=recent(w,10000)/QREF,
          weak_fill_3_qref=recent(weak,3000)/QREF,weak_fill_5_qref=recent(weak,5000)/QREF,weak_fill_10_qref=recent(weak,10000)/QREF,
          last_side_strong=int(streak_side==w),last_side_weak=int(streak_side==weak),same_side_streak_qty_qref=streak_qty/QREF,same_side_streak_n=streak_n,
          last_repair_price=(float(repar[-1]['price']) if repar else 0.0),last_strong_price=(float(strong[-1]['price']) if strong else 0.0),
          split='train' if m in train else 'validation'))
out.sort(key=lambda r:(r['market_id'],r['sec']))
P=ROOT/'.lan_worker_v1/target_opening_repair_admission_v1_20260915';P.mkdir(exist_ok=True)
(P/'data.jsonl').write_text('\n'.join(json.dumps(r,separators=(',',':')) for r in out)+'\n')
features=['strong_qref','weak_qref','gross_qref','gap_qref','abs_gap_qref','weak_to_strong','protection_coverage','strong_margin_qref','signed_weak_margin_qref','strong_since_repair_qref','seconds_since_repair','seconds_since_strong','strong_fill_3_qref','strong_fill_5_qref','strong_fill_10_qref','weak_fill_3_qref','weak_fill_5_qref','weak_fill_10_qref','last_side_strong','last_side_weak','same_side_streak_qty_qref','same_side_streak_n','last_repair_price','last_strong_price']
summary={'status':'PASS','rows':len(out),'train_rows':sum(r['split']=='train' for r in out),'validation_rows':sum(r['split']=='validation' for r in out),'positive_train':sum(r['split']=='train' and r['label_repair'] for r in out),'positive_validation':sum(r['split']=='validation' and r['label_repair'] for r in out),'features':features,'strict_past':True,'window':'first_25_percent','phase_diagnostic_only':True}
(P/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
