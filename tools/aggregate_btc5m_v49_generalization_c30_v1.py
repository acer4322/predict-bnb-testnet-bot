from __future__ import annotations
import json, math, statistics
from pathlib import Path
from collections import defaultdict

ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research'
SOURCE=R/'market_capsule_v1/source_bundle_v49_generalization_c30_20260914_v1'
OUT=R/'BTC5M_V49_GENERALIZATION_C30_20260914_RESULT.json'
MIDS=[2032653,2032652,2021342,2021315,2021302,2021225,2021217,2021179,2021158,2021100,2020843,2020778,2020760,2020725,2020718,2020682,2020663,2019427,2019143,2019120,2019018,2019008,2018991,2018988,2018854,2018847,2018839,2018666,2018407,2018404]

def load_jsonl(p):
    out=[]
    with p.open(encoding='utf-8') as f:
        for line in f:
            line=line.strip()
            if line: out.append(json.loads(line))
    return out

def mean(xs): return sum(xs)/len(xs) if xs else None

def med(xs): return statistics.median(xs) if xs else None

def close(a,b,tol=1e-6): return abs(float(a)-float(b))<=tol

SERIAL={2021315,2021302,2020663,2019427,2019008,2018991,2018988,2018854,2018847,2018839,2018666,2018407,2018404}
def job_id(mid): return f'v49-c30-{mid}-20260914-v3-serial' if mid in SERIAL else f'v49-c30-{mid}-20260914-v2'

def main():
    targets={int(x['market_id']):x for x in load_jsonl(SOURCE/'market_results.jsonl')}
    actions=defaultdict(lambda:defaultdict(lambda:{'legs':0,'shares':0.0,'notional':0.0}))
    for x in load_jsonl(SOURCE/'target_actions.jsonl'):
        mid=int(x['market_id']); role=str(x['role']).upper(); side=str(x['side']).upper()
        z=actions[mid][role]; z['legs']+=1; z['shares']+=float(x['shares']); z['notional']+=float(x['shares'])*float(x['price'])
        z2=actions[mid][role+'_'+side]; z2['legs']+=1; z2['shares']+=float(x['shares']); z2['notional']+=float(x['shares'])*float(x['price'])
    rows=[]; failures=[]
    for mid in MIDS:
        p=R/'lan_worker_returns'/job_id(mid)/'result.json'
        if not p.exists():
            failures.append({'market_id':mid,'reason':'missing_result','job_id':job_id(mid)}); continue
        d=json.loads(p.read_text(encoding='utf-8'))
        if d.get('status')!='COMPLETE':
            failures.append({'market_id':mid,'reason':'model_status_'+str(d.get('status')),'error':d.get('error'),'job_id':job_id(mid)}); continue
        t=targets[mid]
        inv={k:float(v) for k,v in d['final_inventory'].items()}; cost=float(d['final_cost'])
        our_up=inv['UP']-cost; our_down=inv['DOWN']-cost
        winner=t['winner']; our_actual=our_up if winner=='UP' else our_down
        target_cost=float(t['buy_notional_usdt'])-float(t.get('sell_proceeds_usdt') or 0.0)
        target_up=float(t['up_position_shares'])-target_cost; target_down=float(t['down_position_shares'])-target_cost
        target_actual=target_up if winner=='UP' else target_down
        assert close(target_actual,t['net_pnl_usdt'],1e-5),(mid,target_actual,t['net_pnl_usdt'])
        our_net_side='UP' if inv['UP']>inv['DOWN']+1e-8 else 'DOWN' if inv['DOWN']>inv['UP']+1e-8 else 'FLAT'
        target_net_side='UP' if float(t['up_position_shares'])>float(t['down_position_shares'])+1e-8 else 'DOWN' if float(t['down_position_shares'])>float(t['up_position_shares'])+1e-8 else 'FLAT'
        our_dir=our_up if our_net_side=='UP' else our_down if our_net_side=='DOWN' else max(our_up,our_down)
        our_opp=our_down if our_net_side=='UP' else our_up if our_net_side=='DOWN' else min(our_up,our_down)
        tar_dir=target_up if target_net_side=='UP' else target_down if target_net_side=='DOWN' else max(target_up,target_down)
        tar_opp=target_down if target_net_side=='UP' else target_up if target_net_side=='DOWN' else min(target_up,target_down)
        safety=bool((d.get('safety_gate') or {}).get('pass')) and bool(d.get('execution_accounting_valid')) and int(d.get('unresolved_owners') or 0)==0
        row=dict(
            market_id=mid,title=t.get('title'),winner=winner,job_id=job_id(mid),safety_pass=safety,
            our=dict(up=our_up,down=our_down,actual=our_actual,cost=cost,roi=(our_actual/cost if cost else None),inventory=inv,
                net_side=our_net_side,directional_branch=our_dir,opposite_branch=our_opp,
                best_branch=max(our_up,our_down),worst_branch=min(our_up,our_down),
                branch_regime=('BOTH_POSITIVE' if min(our_up,our_down)>0 else 'BOTH_NEGATIVE' if max(our_up,our_down)<0 else 'ONE_POSITIVE_ONE_NEGATIVE'),win=our_actual>0,
                active_submits=int(d.get('active_native_submits') or 0),active_fill_qty=float(d.get('active_fill_qty') or 0.0),
                passive_submits=int(d.get('passive_native_submits') or 0),passive_fill_qty=float(d.get('passive_fill_qty') or 0.0),
                submits=int(d.get('submits') or 0),cancels=int(d.get('cancel_requests') or 0),terminal_worst=float(d.get('terminal_worst'))),
            target=dict(up=target_up,down=target_down,actual=target_actual,cost=target_cost,roi=float(t['net_roi']),
                inventory={'UP':float(t['up_position_shares']),'DOWN':float(t['down_position_shares'])},net_side=target_net_side,
                directional_branch=tar_dir,opposite_branch=tar_opp,best_branch=max(target_up,target_down),worst_branch=min(target_up,target_down),
                branch_regime=('BOTH_POSITIVE' if min(target_up,target_down)>0 else 'BOTH_NEGATIVE' if max(target_up,target_down)<0 else 'ONE_POSITIVE_ONE_NEGATIVE'),win=target_actual>0,
                fill_count=int(t['fill_count']),parent_count=int(t['parent_count']),maker=actions[mid]['MAKER'],taker=actions[mid]['TAKER']),
            matched_actual_pnl_delta=our_actual-target_actual,net_side_agreement=(our_net_side==target_net_side and our_net_side!='FLAT')
        )
        rows.append(row)
    complete=len(rows); assert complete+len(failures)==len(MIDS)
    ours=[r['our']['actual'] for r in rows]; tars=[r['target']['actual'] for r in rows]; deltas=[r['matched_actual_pnl_delta'] for r in rows]
    summary=dict(
        status='COMPLETE' if complete==30 and not failures else 'PARTIAL',market_count=30,complete=complete,failures=len(failures),
        safety_pass=sum(r['safety_pass'] for r in rows),
        our=dict(win_count=sum(r['our']['win'] for r in rows),win_rate=(sum(r['our']['win'] for r in rows)/complete if complete else None),
            avg_actual_pnl=mean(ours),median_actual_pnl=med(ours),total_actual_pnl=sum(ours),avg_roi=mean([r['our']['roi'] for r in rows]),
            avg_cost=mean([r['our']['cost'] for r in rows]),
            avg_directional_branch=mean([r['our']['directional_branch'] for r in rows]),avg_opposite_branch=mean([r['our']['opposite_branch'] for r in rows]),
            avg_best_branch=mean([r['our']['best_branch'] for r in rows]),avg_worst_branch=mean([r['our']['worst_branch'] for r in rows]),
            both_positive_count=sum(r['our']['branch_regime']=='BOTH_POSITIVE' for r in rows),both_negative_count=sum(r['our']['branch_regime']=='BOTH_NEGATIVE' for r in rows),one_positive_one_negative_count=sum(r['our']['branch_regime']=='ONE_POSITIVE_ONE_NEGATIVE' for r in rows),
            active_market_count=sum(r['our']['active_submits']>0 for r in rows),active_submit_total=sum(r['our']['active_submits'] for r in rows),
            active_fill_qty_total=sum(r['our']['active_fill_qty'] for r in rows),passive_submit_total=sum(r['our']['passive_submits'] for r in rows)),
        target=dict(win_count=sum(r['target']['win'] for r in rows),win_rate=(sum(r['target']['win'] for r in rows)/complete if complete else None),
            avg_actual_pnl=mean(tars),median_actual_pnl=med(tars),total_actual_pnl=sum(tars),avg_roi=mean([r['target']['roi'] for r in rows]),
            avg_cost=mean([r['target']['cost'] for r in rows]),
            avg_directional_branch=mean([r['target']['directional_branch'] for r in rows]),avg_opposite_branch=mean([r['target']['opposite_branch'] for r in rows]),
            avg_best_branch=mean([r['target']['best_branch'] for r in rows]),avg_worst_branch=mean([r['target']['worst_branch'] for r in rows]),
            both_positive_count=sum(r['target']['branch_regime']=='BOTH_POSITIVE' for r in rows),both_negative_count=sum(r['target']['branch_regime']=='BOTH_NEGATIVE' for r in rows),one_positive_one_negative_count=sum(r['target']['branch_regime']=='ONE_POSITIVE_ONE_NEGATIVE' for r in rows),
            maker_legs_total=sum(r['target']['maker']['legs'] for r in rows),taker_legs_total=sum(r['target']['taker']['legs'] for r in rows)),
        matched=dict(avg_actual_pnl_delta=mean(deltas),median_actual_pnl_delta=med(deltas),total_actual_pnl_delta=sum(deltas),
            our_better_count=sum(x>0 for x in deltas),target_better_count=sum(x<0 for x in deltas),ties=sum(abs(x)<=1e-9 for x in deltas),
            final_net_side_agreement_count=sum(r['net_side_agreement'] for r in rows),final_net_side_agreement_rate=(sum(r['net_side_agreement'] for r in rows)/complete if complete else None),
            both_win_count=sum(r['our']['win'] and r['target']['win'] for r in rows),our_only_win_count=sum(r['our']['win'] and not r['target']['win'] for r in rows),
            target_only_win_count=sum((not r['our']['win']) and r['target']['win'] for r in rows),both_lose_count=sum((not r['our']['win']) and (not r['target']['win']) for r in rows)),
        accounting_note='OUR zero-fee native research accounting versus Target FILLED_CASHFLOW_PLUS_WINNER_V1_NO_EXPLICIT_FEE; same-market comparison, not live fee-adjusted PnL.'
    )
    out={'version':'BTC5M_V49_GENERALIZATION_C30_RESULT_V1_20260914','summary':summary,'failures':failures,'rows':rows}
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(summary,indent=2,ensure_ascii=False,allow_nan=False))
if __name__=='__main__': main()
