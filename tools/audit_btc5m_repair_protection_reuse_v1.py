"""Consumed-only cash contribution and observed W/S/W run audit.

No fitted rule, synthetic fill, native execution, fixed timer, or Target actor input.
Before/after payout coordinates and same-second decomposition are order independent.
"""
import collections,json,math,statistics,time
from audit_btc5m_target_core_loop_topology_v1 import ROOT,R,MIDS,read,sha,SIDES,side_of

STEM='BTC5M_REPAIR_PROTECTION_REUSE_V1_20260913'
EPS=1e-9


def stats(values):
    xs=sorted(float(x) for x in values if x is not None)
    if not xs:return dict(n=0)
    def quantile(p):
        pos=p*(len(xs)-1);lo=int(pos);hi=math.ceil(pos)
        return xs[lo]+(xs[hi]-xs[lo])*(pos-lo)
    return dict(n=len(xs),min=xs[0],p25=quantile(.25),median=quantile(.5),p75=quantile(.75),max=xs[-1])


def reconstruct(source):
    grouped=collections.defaultdict(list)
    for a in source['targetActions']:
        assert a['quote_type']=='BID' and a['side'] in SIDES and 0<a['price']<1 and a['shares']>0
        grouped[a['event_ms']].append(a)
    inv=dict.fromkeys(SIDES,0.);cost=0.;out=[]
    for t,actions in sorted(grouped.items()):
        preinv=dict(inv);precost=cost;before=side_of(inv)
        q={s:math.fsum(a['shares'] for a in actions if a['side']==s) for s in SIDES}
        cash={s:math.fsum(a['shares']*a['price'] for a in actions if a['side']==s) for s in SIDES}
        for s in SIDES:inv[s]+=q[s]
        cost+=sum(cash.values());after=side_of(inv)
        pre={s:preinv[s]-precost for s in SIDES};post={s:inv[s]-cost for s in SIDES}
        row=dict(t=t,pre_inventory=preinv,post_inventory=dict(inv),pre_cost=precost,post_cost=cost,
            pre_payoff=pre,post_payoff=post,pre_surplus=before,post_surplus=after,
            q=q,cash=cash,maker_qty={s:math.fsum(a['shares'] for a in actions if a['side']==s and a['role']=='MAKER') for s in SIDES},
            taker_qty={s:math.fsum(a['shares'] for a in actions if a['side']==s and a['role']=='TAKER') for s in SIDES},kind='FLAT_PREFIX')
        if before!='FLAT':
            strong=before;weak='DOWN' if strong=='UP' else 'UP'
            gain=q[weak]-cash[weak];spend=cash[strong]
            assert abs((post[weak]-pre[weak])-(gain-spend))<1e-7
            assert abs((post[strong]-pre[strong])-(q[strong]-cash[strong]-cash[weak]))<1e-7
            kind='WEAK_ONLY' if q[weak]>EPS and q[strong]<=EPS else 'STRONG_ONLY' if q[strong]>EPS and q[weak]<=EPS else 'BOTH'
            row.update(kind=kind,strong=strong,weak=weak,same_surplus=before==after,
                standalone_weak_does_not_cross=q[weak]<=abs(preinv['UP']-preinv['DOWN'])+EPS,
                weak_payoff_repair_gain=gain,strong_acquisition_weak_payoff_spend=spend,
                weak_payoff_net_change=post[weak]-pre[weak],strong_payoff_net_change=post[strong]-pre[strong],
                strong_payoff_repair_spend=cash[weak],strong_payoff_add_gain=q[strong]-cash[strong])
        out.append(row)
    return out


def runs_and_loops(rows,mid):
    runs=[]
    for row in rows:
        key=(row['kind'],row['pre_surplus']) if row.get('same_surplus') else ('BOUNDARY',row['t'])
        if not runs or runs[-1]['key']!=key:runs.append(dict(key=key,rows=[]))
        runs[-1]['rows'].append(row)
    weak_ends=[];loops=[]
    for i,run in enumerate(runs):
        if run['key'][0]!='WEAK_ONLY':continue
        first,last=run['rows'][0],run['rows'][-1];strong=first['strong'];weak=first['weak']
        gain=sum(r['weak_payoff_repair_gain'] for r in run['rows'])
        nxt=runs[i+1] if i+1<len(runs) else None
        end=dict(market=mid,start=first['t'],end=last['t'],strong=strong,batches=len(run['rows']),
            start_weak_payoff=first['pre_payoff'][weak],end_weak_payoff=last['post_payoff'][weak],
            end_strong_payoff=last['post_payoff'][strong],end_cost=last['post_cost'],
            end_weak_payoff_per_cost=last['post_payoff'][weak]/last['post_cost'],
            end_gap=abs(last['post_inventory']['UP']-last['post_inventory']['DOWN']),
            repair_gain=gain,repair_spend=sum(r['cash'][weak] for r in run['rows']),
            next_run_kind=nxt['key'][0] if nxt else 'END_OF_OBSERVATIONS',
            next_same_surplus_strong_fill=bool(nxt and nxt['key']==('STRONG_ONLY',strong)))
        weak_ends.append(end)
        if not end['next_same_surplus_strong_fill']:continue
        b=nxt['rows'];spent=sum(r['cash'][strong] for r in b)
        after=runs[i+2] if i+2<len(runs) else None
        returned=bool(after and after['key']==('WEAK_ONLY',strong))
        loop=dict(end,strong_start=b[0]['t'],strong_end=b[-1]['t'],strong_batches=len(b),
            strong_spend=spent,spend_over_preceding_repair_gain=spent/gain,
            after_strong_weak_payoff=b[-1]['post_payoff'][weak],
            after_strong_strong_payoff=b[-1]['post_payoff'][strong],
            returned_to_weak=returned,next_weak_start=after['rows'][0]['t'] if returned else None)
        assert abs((loop['after_strong_weak_payoff']-end['start_weak_payoff'])-(gain-spent))<1e-7
        loops.append(loop)
    return weak_ends,loops


def summarize(rows,ends,loops):
    stable=[r for r in rows if r.get('same_surplus')]
    both=[r for r in stable if r['kind']=='BOTH']
    eligible_both=[r for r in both if r['standalone_weak_does_not_cross']]
    strong=[r for r in stable if r['q'][r['strong']]>EPS]
    def mixed(xs):return dict(n=len(xs),repair_gain_positive_net_weak_payoff_negative=sum(r['weak_payoff_repair_gain']>EPS and r['weak_payoff_net_change']<-EPS for r in xs),
        weak_repair_gain=sum(r['weak_payoff_repair_gain'] for r in xs),strong_spend=sum(r['strong_acquisition_weak_payoff_spend'] for r in xs))
    return dict(batches=len(rows),categories=dict(collections.Counter(r['kind'] for r in rows)),
        same_surplus_batches=len(stable),mixed_same_surplus=mixed(both),mixed_standalone_no_cross=mixed(eligible_both),
        strong_fill_batches=len(strong),strong_fill_pre_weak_payoff_negative=sum(r['pre_payoff'][r['weak']]<-EPS for r in strong),
        pure_weak_runs=len(ends),pure_weak_ends_negative=sum(r['end_weak_payoff']<-EPS for r in ends),
        pure_weak_end_payoff=stats(r['end_weak_payoff'] for r in ends),pure_weak_end_payoff_per_cost=stats(r['end_weak_payoff_per_cost'] for r in ends),
        pure_weak_next_runs=dict(collections.Counter(r['next_run_kind'] for r in ends)),
        pure_weak_then_strong=len(loops),weak_then_strong_before_break_even=sum(r['end_weak_payoff']<-EPS for r in loops),
        weak_then_strong_spend_exceeds_repair_gain=sum(r['strong_spend']>r['repair_gain']+EPS for r in loops),
        weak_strong_weak_loops=sum(r['returned_to_weak'] for r in loops),
        spend_over_preceding_repair_gain=stats(r['spend_over_preceding_repair_gain'] for r in loops))


def main():
    started=time.perf_counter();prereg=read(R/(STEM+'_PREREGISTERED.json'))
    previous=read(R/'BTC5M_PAYOFF_REPAIR_V1_20260913_TARGET.json')
    allrows=[];allends=[];allloops=[];out=dict(status='COMPLETE',markets={})
    for mid in MIDS:
        sourcepath=ROOT/prereg['sources'][str(mid)]['path'];assert sha(sourcepath)==prereg['sources'][str(mid)]['sha256']
        source=read(sourcepath);rows=reconstruct(source);ends,loops=runs_and_loops(rows,mid)
        end=rows[-1];old=previous['markets'][str(mid)]['terminal']
        assert abs(end['post_cost']-old['cost'])<1e-7
        assert all(abs(end['post_payoff'][s]-old[s.lower()])<1e-7 for s in SIDES)
        # Full reconstruction is invariant to same-clock source-leg order.
        reversed_source=dict(source,targetActions=list(reversed(source['targetActions'])))
        assert reconstruct(reversed_source)==rows
        # Side relabeling cannot change these aggregate economic conclusions.
        swapped=dict(source,targetActions=[dict(a,side='DOWN' if a['side']=='UP' else 'UP') for a in source['targetActions']])
        sr=reconstruct(swapped);se,sl=runs_and_loops(sr,mid)
        assert summarize(rows,ends,loops)==summarize(sr,se,sl)
        out['markets'][str(mid)]=dict(summary=summarize(rows,ends,loops),rows=rows,weak_run_ends=ends,weak_strong_pairs=loops)
        allrows+=rows;allends+=ends;allloops+=loops
    out.update(summary=summarize(allrows,allends,allloops),elapsed_seconds=time.perf_counter()-started,
        prereg_sha256=sha(R/(STEM+'_PREREGISTERED.json')),tool_sha256=sha(__import__('pathlib').Path(__file__)),
        verification=dict(previous_terminal_payoff_parity=True,side_swap_invariance=True,within_second_order_invariance=True,
            batch_cash_delta_conservation=True,weak_strong_pair_cash_delta_conservation=True),
        interpretation='Observed cash paths and counterexamples to strict serial observed-flow models; no private submission/HOLD labels or identified cash budget.',
        limits=prereg['limits'])
    (R/(STEM+'_RESULT.json')).write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=out['status'],elapsed_seconds=out['elapsed_seconds'],summary=out['summary'],
        selected=out['markets']['2026085']['summary'],selected_pairs=out['markets']['2026085']['weak_strong_pairs'])))


if __name__=='__main__':main()
