"""V28 retrospective Target/OUR alignment; no policy changes, fits or native jobs."""
import bisect
import collections
import json
import math
from pathlib import Path
from audit_btc5m_post_exposure_response_v1 import reconstruct,sum_flow,close
from prepare_btc5m_repair_maintenance_scope_v1 import ROOT,R,START,ARMS,config,read,sha,get,RET,dump_for
from verify_btc5m_active_repair_opportunity_v1 import canonical_legs
from btc5m_exposure_suppression_metrics_v1 import geometry,path_summary

STEM='BTC5M_TARGET_READDITION_ALIGNMENT_V1_20260913'
INPUT=ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz'
INPUT_SHA='d2ce3d621241f0de94aeb888cb2d059345e3dc0afd677d0d1b70547f798c578b'


def flow_totals(rows):
    f=sum_flow(rows);out={}
    for side in ('UP','DOWN'):
        qty=math.fsum(v['qty'] for v in f[side].values());cash=math.fsum(v['cash'] for v in f[side].values())
        out[side]=dict(qty=qty,cash=cash,vwap=cash/qty if qty else None,gross_branch_contribution=qty-cash,by_route=f[side])
    return out


def at(rows,sec):
    t=START+round(sec*1000);idx=bisect.bisect_right([r['t'] for r in rows],t)-1
    if idx<0:return dict(t=t,source_t=None,seconds=sec,inv=dict(UP=0.,DOWN=0.),cost=0.,geometry=geometry(dict(UP=0.,DOWN=0.),0.))
    return dict(rows[idx],source_t=rows[idx]['t'],t=t,seconds=sec)


def span(rows,lo,hi):
    a,b=at(rows,lo),at(rows,hi);flows=flow_totals([r for r in rows if a['t']<r['t']<=b['t']])
    cash=sum(f['cash'] for f in flows.values())
    for side in ('UP','DOWN'):
        close(b['inv'][side]-a['inv'][side],flows[side]['qty'])
        close(b['geometry'][side.lower()]-a['geometry'][side.lower()],flows[side]['qty']-cash)
    close(b['cost']-a['cost'],cash)
    return dict(start_seconds=lo,end_seconds=hi,before=a['geometry'],after=b['geometry'],flow=flows,
        down_repair_lift=flows['DOWN']['qty']-flows['DOWN']['cash'],up_acquisition_consumes_down=flows['UP']['cash'],
        net_down_payoff_change=b['geometry']['down']-a['geometry']['down'])


def target_check(source):
    actions=source['targetActions'];parents=source['targetParents']
    assert len(actions)==275 and len(parents)==190 and len({a['source_leg_id'] for a in actions})==275
    assert source['market']['market_id']==2026085 and source['market']['quality_status']=='COMPLETE_FORWARD_V1'
    assert all(a['quote_type']=='BID' and a['side'] in ('UP','DOWN') and a['role'] in ('MAKER','TAKER') and
        a['event_ms']%1000==0 and START<=a['event_ms']<START+300000 and 0<a['price']<1 and a['shares']>0 and
        a['observed_at_ms']>=a['event_ms'] for a in actions)
    grouped=collections.defaultdict(list)
    def key(a):return (a['role'],a['side'],a['quote_type'],a['order_hash'])
    for a in actions:grouped[key(a)].append(a)
    assert len(grouped)==len(parents) and len({key(p) for p in parents})==len(parents)
    for p in parents:
        batch=grouped[key(p)];qty=math.fsum(a['shares'] for a in batch);cash=math.fsum(a['shares']*a['price'] for a in batch)
        assert p['fill_legs']==len(batch) and p['first_event_ms']==min(a['event_ms'] for a in batch) and p['last_event_ms']==max(a['event_ms'] for a in batch)
        close(qty,p['shares']);close(cash,p['average_price']*p['shares'])
    legs=[dict(t=a['event_ms'],side=a['side'],route=a['role'],qty=a['shares'],cash=a['shares']*a['price']) for a in actions]
    rows=reconstruct(legs);assert rows==reconstruct(list(reversed(legs))) and len(rows)==100
    prior=read(R/'BTC5M_POST_EXPOSURE_RESPONSE_V1_20260913_RESULT.json')['target']['2026085']
    assert prior['source_sha256']==INPUT_SHA and rows==prior['curve'], 'Target source/legacy accounting drift'
    return rows,dict(status='PASS',source_legs=len(actions),parent_groups=len(parents),event_seconds=len(rows),
        unique_source_ids=True,parent_quantity_cash_timing_reconciled=True,same_second_order_invariant=True,
        prior_v17_entire_curve_identical=True,fees='UNOBSERVED_EXCLUDED',private_orders_pending_cancels='UNKNOWN')


def render(paths):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':['Microsoft JhengHei','DejaVu Sans'],'axes.unicode_minus':False,'font.size':10})
    fig,axes=plt.subplots(2,2,figsize=(13,8),layout='constrained')
    styles={'target':dict(color='#202937',linewidth=2.1,label='目標（成交秒桶）'),
        'raw':dict(color='#1976b9',linewidth=1.5,label='模型：原維護價'),
        'legal':dict(color='#b84a3b',linewidth=1.5,linestyle='--',label='模型：合法維護價')}
    titles=['UP 累計成交份額','DOWN 累計成交份額','UP 條件收益','DOWN 條件收益']
    for index,ax in enumerate(axes.flat):
        for name,rows in paths.items():
            # UP quantities coincide in both OUR arms; show the common line once.
            if index==0 and name=='legal':continue
            values=[r['inv']['UP' if index==0 else 'DOWN'] if index<2 else r['geometry']['up' if index==2 else 'down'] for r in rows]
            x=[0]+[(r['t']-START)/1000 for r in rows]+[300]
            style=dict(styles[name])
            if index==0 and name=='raw':style['label']='模型（兩組 UP 成交流相同）'
            ax.step(x,[0]+values+[values[-1]],where='post',**style)
        ax.axvline(126,color='#88909a',linewidth=.8,linestyle=':')
        ax.axvline(249,color='#bec4cc',linewidth=.8,linestyle=':')
        if index>=2:ax.axhline(0,color='#a5adb5',linewidth=.7)
        ax.set(title=titles[index],xlabel='開局後秒數',xlim=(0,300),ylabel='份額' if index<2 else '條件收益（元）')
        ax.grid(alpha=.18);ax.legend(fontsize=8,loc='best')
    fig.suptitle('2026085：差距在前段已形成，後段模型其實買入更多 UP',fontsize=15)
    fig.supxlabel('僅離線觀察；目標秒桶與模型 canonical 時鐘精度不同。目標 fees/rebates 未觀察，模型費用為 0。',fontsize=9)
    png=R/(STEM+'.png');svg=R/(STEM+'.svg');fig.savefig(png,dpi=145);fig.savefig(svg);plt.close(fig)
    return dict(png=str(png.relative_to(ROOT)),svg=str(svg.relative_to(ROOT)))


def main():
    assert sha(INPUT)==INPUT_SHA
    protocol=dict(status='RETROSPECTIVE_ANALYSIS',market=2026085,source_sha256=INPUT_SHA,
        question='Does Target continue UP acquisition while repairing DOWN, and is OUR late UP under-acquisition the main current mismatch?',
        baseline='Reused verified V27 raw/legal; no replay',native_jobs=0,model_fits=0,parameter_search=0,policy_changes=0,
        boundaries='Target event-time second buckets offline only; no private order or same-second sequence inferred. Global trough is descriptive, not a causal trigger.',
        measurements=['Validate every Target leg and parent, reproduce historical Target curve',
            'Target whole-episode timing and continued UP during recovery, compare common clocks without equal-state claim',
            'Early/late quantities, paid cost and terminal accounting decomposition against V27',
            'No chosen future Target time or quantity enters an executable policy'])
    dump_for(STEM,'PROTOCOL',protocol)
    source=read(INPUT);target,checked=target_check(source);paths=dict(target=target);evidence={};last={}
    for arm in ARMS:
        c=config(arm);audit=read(R/(c['STEM']+'_RESULT.json'));assert audit['verification']=='PASS'
        n,t=get(RET/c['JOB']);assert sha(RET/c['JOB']/'result.json')==audit['candidate']['result_sha256']
        rows=reconstruct(canonical_legs(n,t));state_at={s['t']:s for s in t['states']}
        for row in rows:
            close(row['cost'],state_at[row['t']]['cost'])
            for side in ('UP','DOWN'):close(row['inv'][side],state_at[row['t']]['inv'][side])
        paths[arm]=rows;evidence[arm]=dict(job=c['JOB'],result_sha256=sha(RET/c['JOB']/'result.json'),trace_sha256=sha(RET/c['JOB']/'clock_trace.json.gz'))
        owners={o['key']:o for o in t['demand_final']['all_final_carriers']}
        assert all(o['fees']==0 for o in owners.values())
        last[arm]={}
        for side in ('UP','DOWN'):
            rr=[r for r in t['demand_final']['full_raw_receipts'] if r['qty']>1e-8 and owners[r['key']]['side']==side]
            last[arm][side]=dict(exchange_seconds=(max(r['exchange_ts'] for r in rr)/1e6-START)/1000,
                receive_seconds=(max(r['receive_ts'] for r in rr)/1e6-START)/1000,
                canonical_seconds=(max(r['t'] for r in rows if sum(v['qty'] for v in r['flow'][side].values())>1e-8)-START)/1000)
    target_totals=flow_totals(target);trough=min(target,key=lambda r:r['geometry']['down']);trough_seconds=(trough['t']-START)/1000
    target_last={side:max((a['event_ms']-START)/1000 for a in source['targetActions'] if a['side']==side) for side in ('UP','DOWN')}
    up_taker=[a for a in source['targetActions'] if a['side']=='UP' and a['role']=='TAKER']
    taker_bytime=collections.defaultdict(list)
    for a in up_taker:taker_bytime[a['event_ms']].append(a)
    taker_evidence=dict(legs=len(up_taker),parents=len({a['order_hash'] for a in up_taker}),
        first_seconds=(min(taker_bytime)-START)/1000,last_seconds=(max(taker_bytime)-START)/1000,
        qty=math.fsum(a['shares'] for a in up_taker),cash=math.fsum(a['shares']*a['price'] for a in up_taker),
        event_seconds=[dict(seconds=(t-START)/1000,qty=math.fsum(a['shares'] for a in batch),
            cash=math.fsum(a['shares']*a['price'] for a in batch),prices=sorted({a['price'] for a in batch}))
            for t,batch in sorted(taker_bytime.items())])
    windows=[(0,126),(126,300),(230,249),(249,300)]
    clocks=(126,141,156,212,230,231,238,245,249,259.758,290,300)
    summaries={};gaps={}
    for name,rows in paths.items():
        totals=flow_totals(rows);g=rows[-1]['geometry']
        summaries[name]=dict(terminal=g,totals=totals,common_clocks={str(sec):at(rows,sec)['geometry'] for sec in clocks},
            windows={f'{lo}_{hi}':span(rows,lo,hi) for lo,hi in windows},
            early_up_fraction=at(rows,126)['inv']['UP']/g['inventory_up'],
            both_positive_seconds=path_summary(dict(inv=dict(UP=0.,DOWN=0.),cost=0.),rows,START,START+300000)['both_positive_seconds'])
        if name=='target':continue
        up_gross_gap=target_totals['UP']['gross_branch_contribution']-totals['UP']['gross_branch_contribution']
        down_cost_excess=totals['DOWN']['cash']-target_totals['DOWN']['cash']
        close(target[-1]['geometry']['up']-g['up'],up_gross_gap+down_cost_excess)
        gaps[name]=dict(target_minus_our_up_payoff=target[-1]['geometry']['up']-g['up'],
            target_minus_our_up_gross=up_gross_gap,our_minus_target_down_cost=down_cost_excess,
            our_minus_target_up_qty=g['inventory_up']-target[-1]['inv']['UP'],
            our_minus_target_down_qty=g['inventory_down']-target[-1]['inv']['DOWN'])
    late_up=[dict(seconds=(r['t']-START)/1000,flow=r['flow'],before=r['before']['geometry'],after=r['geometry'])
        for r in target if r['t']>trough['t'] and sum(v['qty'] for v in r['flow']['UP'].values())>0]
    raw_ours_up=[r for r in canonical_legs(*get(RET/config('raw')['JOB'])) if r['side']=='UP']
    legal_ours_up=[r for r in canonical_legs(*get(RET/config('legal')['JOB'])) if r['side']=='UP']
    assert raw_ours_up==legal_ours_up
    visual=render(paths)
    out=dict(status='COMPLETE',verification='PASS',market=2026085,native_jobs=0,model_fits=0,parameter_search=0,policy_changes=0,
        source_sha256=INPUT_SHA,checks=checked,our_sources=evidence,summary=summaries,terminal_gaps=gaps,
        target_trough=dict(seconds=trough_seconds,state=trough['geometry'],selection='Global observed DOWN-payoff trough, descriptive only'),
        target_after_trough=span(target,trough_seconds,300),target_up_acquisition_after_trough=late_up,
        target_last_event_seconds=target_last,our_last_fill_seconds=last,
        target_up_taker_evidence=taker_evidence,
        target_entire_path=dict(both_positive_batches=sum(r['geometry']['floor']>0 for r in target),
            all_up_net_positive=all(r['geometry']['up_net']>0 for r in target),all_down_payoff_negative=all(r['geometry']['down']<0 for r in target)),
        target_all_event_rows=target,visual=visual,
        interpretation='Target re-adds UP during partial DOWN recovery, yet has no UP fills after second 249. OUR already acquires more UP after second 126 and continues later; large early inventory allocation and paid DOWN cost differences precede V26/V27 maintenance changes. Retain the pending-maintenance finding but broaden next investigation to early directional accumulation and parallel repair.',
        limits='Observed buys from zero inventory; Target fees/rebates, new/cancel orders, unfilled universe, private receipts and same-second ordering UNKNOWN. Common time is not common state. No hypothetical UP fills or Target-derived execution schedule.')
    dump_for(STEM,'RESULT',out)
    dump_for(STEM,'PROGRESS',dict(status='COMPLETE',native_jobs=0,model_fits=0,parameter_search=0,policy_changes=0,
        target_legs_checked=275,parent_groups_checked=190,reused_our_results=list(ARMS),next_native_dispatched=False,
        next_priority='Investigate early directional accumulation and the currently untested UP Active path alongside pending repair maintenance; do not infer a late UP-only fix or imitate Target event times.'))
    print(json.dumps(dict(status='PASS',checks=checked,trough=out['target_trough'],target_last=target_last,our_last=last,
        terminal_gaps=gaps,summary={name:dict(terminal=s['terminal'],totals=s['totals'],early_up_fraction=s['early_up_fraction']) for name,s in summaries.items()})))


if __name__=='__main__':main()
