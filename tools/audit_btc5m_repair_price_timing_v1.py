"""V29 observed repair price/timing audit. No fitting, native calls or actor edits."""
import collections
import json
import math

from audit_btc5m_target_readdition_alignment_v1 import (
    ROOT, R, START, INPUT, INPUT_SHA, ARMS, RET, config, read, sha, get,
    target_check, reconstruct, canonical_legs, flow_totals, span, close, dump_for,
)

STEM = 'BTC5M_REPAIR_PRICE_TIMING_V1_20260913'
END = START + 300000
WINDOWS = [(i, i+50) for i in range(0, 300, 50)] + [(0, 150), (150, 300), (0, 300)]


def intervals(books, clock):
    assert all(a[clock] < b[clock] for a, b in zip(books, books[1:]))
    out = []
    for i, b in enumerate(books):
        left = max(START, b[clock])
        right = min(END, books[i+1][clock] if i+1 < len(books) else END)
        if right <= left:
            continue
        bid, ask = b['best_bid'], b['best_ask']
        assert bid is not None and 0 < bid < 1
        assert ask is None or bid < ask < 1
        out.append(dict(left=left, right=right, source_ms=b['source_ms'],
                        received_ms=b['received_ms'], down_ask=1-bid,
                        down_mid=None if ask is None else 1-(bid+ask)/2))
    return out


def book_window(parts, lo, hi):
    clipped = []
    for p in parts:
        left, right = max(START+1000*lo, p['left']), min(START+1000*hi, p['right'])
        if right > left:
            clipped.append(dict(p, left=left, right=right, seconds=(right-left)/1000))
    coverage = math.fsum(p['seconds'] for p in clipped)
    mid_seconds = math.fsum(p['seconds'] for p in clipped if p['down_mid'] is not None)
    near = math.fsum(p['seconds'] for p in clipped if p['down_mid'] is not None and .4 <= p['down_mid'] <= .6)
    ask_mean = math.fsum(p['seconds']*p['down_ask'] for p in clipped)/coverage
    mid_mean = math.fsum(p['seconds']*p['down_mid'] for p in clipped if p['down_mid'] is not None)/mid_seconds
    # Integrate age over the actual interval; an update's fixed transport lag is not its entire held age.
    age_mean = math.fsum(p['seconds']*((p['left']+p['right'])/2-p['source_ms'])/1000 for p in clipped)/coverage
    return dict(coverage_seconds=coverage, missing_seconds=hi-lo-coverage,
                mid_coverage_seconds=mid_seconds, mid_missing_seconds=hi-lo-mid_seconds,
                mean_down_ask=ask_mean, mean_down_mid=mid_mean,
                near_half_mid_seconds=near, near_half_fraction_of_valid_mid=near/mid_seconds,
                mean_abs_mid_distance_from_half=math.fsum(p['seconds']*abs(p['down_mid']-.5) for p in clipped if p['down_mid'] is not None)/mid_seconds,
                mean_source_age_seconds=age_mean,
                maximum_source_age_seconds=max((p['right']-p['source_ms'])/1000 for p in clipped))


def enriched_span(rows, lo, hi):
    out = span(rows, lo, hi)
    for f in out['flow'].values():
        f['shares_per_cash'] = f['qty']/f['cash'] if f['cash'] else None
        f['branch_lift_per_cash'] = (f['qty']-f['cash'])/f['cash'] if f['cash'] else None
        for route in f['by_route'].values():
            route['vwap'] = route['cash']/route['qty'] if route['qty'] else None
    return out


def early_roles(result, trace, rows):
    """Attribute canonical early fills only, with a checked constant Maker execution price."""
    new = {}
    for p in trace['plans']:
        for o in p['operations']:
            if o['kind'] == 'NEW':
                assert o['key'] not in new
                new[o['key']] = dict(o, t=p['t'])
    all_receipts = collections.defaultdict(list)
    for r in trace['demand_final']['full_raw_receipts']:
        if r['qty'] > 0:
            all_receipts[r['key']].append(r)
    output = {}
    for hi in (50, 150):
        grouped = collections.defaultdict(lambda: dict(qty=0., cash=0., owners=set()))
        for e in result['atomic_responsibility_events']:
            if not START < e['t'] <= START+hi*1000:
                continue
            for f in e['fill_rows']:
                if f['side'] != 'DOWN' or f['fill_increment'] <= 0:
                    continue
                o = new[f['key']]
                assert o['route'] == 'PASSIVE' and o['qty'] == 15 and o['price']*o['qty'] >= 1-1e-9
                assert all(r['maker'] == 1 and abs(r['contractPrice']-o['price']) < 1e-10 and r['fee'] == 0 for r in all_receipts[f['key']])
                g = grouped[o['role']]
                g['qty'] += f['fill_increment']; g['cash'] += f['fill_increment']*o['price']; g['owners'].add(f['key'])
        for g in grouped.values():
            g['owners'] = sorted(g['owners'])
            g['owner_count'] = len(g['owners'])
        totals = span(rows, 0, hi)['flow']['DOWN']
        close(sum(g['qty'] for g in grouped.values()), totals['qty'])
        close(sum(g['cash'] for g in grouped.values()), totals['cash'])
        output[str(hi)] = dict(grouped)
    return output


def render(source, parts, paths):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':['Microsoft JhengHei','DejaVu Sans'], 'axes.unicode_minus':False, 'font.size':10})
    fig, axs = plt.subplots(3, 1, figsize=(12, 11), layout='constrained', sharex=True)
    x = [(p['left']-START)/1000 for p in parts]+[300]
    axs[0].step(x, [p['down_ask'] for p in parts]+[parts[-1]['down_ask']], where='post', color='#8194aa', linewidth=1, label='公開 DOWN ask（接收時鐘）')
    for role, color, marker in [('MAKER','#2376ac','o'), ('TAKER','#ce6a30','^')]:
        aa = [a for a in source['targetActions'] if a['side']=='DOWN' and a['role']==role]
        axs[0].scatter([(a['event_ms']-START)/1000 for a in aa], [a['price'] for a in aa],
                       s=[12+2*a['shares'] for a in aa], color=color, marker=marker, alpha=.72, label='目標 '+role+'（面積隨份額增加）')
    axs[0].axhspan(.4,.6,color='#e5dbbb',alpha=.28,label='0.5 ± 0.1 描述區間')
    axs[0].set(ylabel='DOWN 價格', ylim=(0,max(.61,max(p['down_ask'] for p in parts)+.025)), title='本場很早離開中線；後段較便宜，但成交不能揭示掛單意圖')
    styles = [('target','#202937','目標'), ('raw','#2376ac','模型 raw'), ('legal','#b84a3b','模型 legal')]
    for name, color, label in styles:
        rows = paths[name]
        q = [r['inv']['DOWN'] for r in rows]
        cash, total = [], 0.
        for r in rows:
            total += sum(v['cash'] for v in r['flow']['DOWN'].values()); cash.append(total)
        xs = [0]+[(r['t']-START)/1000 for r in rows]+[300]
        for ax, values in [(axs[1],q),(axs[2],cash)]:
            ax.step(xs,[0]+values+[values[-1]],where='post',color=color,label=label,linewidth=1.7,linestyle='--' if name=='legal' else '-')
    axs[1].set(ylabel='DOWN 累計份額', title='目標前半場 575.94 份；後半場 540.78 份')
    axs[2].set(ylabel='DOWN 累計支出（元）', xlabel='開局後秒數', title='模型相對目標的主要成本差，在前 50 秒已出現')
    for ax in axs:
        ax.set_xlim(0,300); ax.axvline(150,color='#8f949b',linestyle=':',linewidth=1)
        ax.grid(alpha=.15); ax.legend(fontsize=8,loc='best')
    fig.suptitle('2026085｜修復價格與時機：固定半場分段的離線核對',fontsize=15)
    fig.supxlabel('目標成交為秒桶，公開 book 有約 2.3–3.4 秒傳輸延遲；不作逐筆可成交價或私人下單時序判斷。',fontsize=9)
    for suffix in ('.png','.svg'):
        fig.savefig(R/(STEM+suffix),dpi=145)
    plt.close(fig)


def main():
    protocol = dict(status='RETROSPECTIVE_AUDIT', market=2026085, runtime_eligible=False,
        hypothesis='Did Target acquire DOWN later at lower cost, and was the first half near 0.5?',
        fixed_windows=WINDOWS, fill_window='(start, end]; no fills at market start',
        book_method='Time weighted piecewise constant quotes, received clock primary; source clock retrospective sensitivity only. Do not backfill missing start, or infer a missing ask.',
        midpoint_band=[.4,.6], band_is_descriptive_not_actor_threshold=True,
        dedup='V17/V18 raised price timing; V28 compared trough-aligned inventory and UP readdition. New scope is fixed-half/50s flow, time-weighted actual book, clock/missingness sensitivity, and early OUR DOWN role attribution. No native replay.',
        native_jobs=0, model_fits=0, parameter_search=0, policy_changes=0,
        restrictions='Target prices, quantities, event times and future path remain offline; no fixed-midgame wait rule, artificial cash cap, or assumed future cheap fill.')
    dump_for(STEM,'PROTOCOL',protocol)
    assert sha(INPUT)==INPUT_SHA
    source=read(INPUT); target, checks=target_check(source)
    paths=dict(target=target); sources={}; roles={}
    for arm in ARMS:
        c=config(arm); audit=read(R/(c['STEM']+'_RESULT.json'))
        assert audit['verification']=='PASS' and sha(RET/c['JOB']/'result.json')==audit['candidate']['result_sha256']
        n,t=get(RET/c['JOB']); rows=reconstruct(canonical_legs(n,t))
        prior=read(R/'BTC5M_TARGET_READDITION_ALIGNMENT_V1_20260913_RESULT.json')['summary'][arm]
        for side in ('UP','DOWN'):
            close(rows[-1]['inv'][side],prior['terminal']['inventory_'+side.lower()])
        close(rows[-1]['cost'],prior['terminal']['cost'])
        paths[arm]=rows; roles[arm]=early_roles(n,t,rows)
        sources[arm]=dict(job=c['JOB'],result_sha256=sha(RET/c['JOB']/'result.json'),trace_sha256=sha(RET/c['JOB']/'clock_trace.json.gz'))
    flows={name:{f'{lo}_{hi}':enriched_span(rows,lo,hi) for lo,hi in WINDOWS} for name,rows in paths.items()}
    for name,windows in flows.items():
        for side in ('UP','DOWN'):
            for key in ('qty','cash'):
                close(sum(windows[f'{lo}_{hi}']['flow'][side][key] for lo,hi in WINDOWS[:6]),windows['0_300']['flow'][side][key])
                close(sum(windows[w]['flow'][side][key] for w in ('0_150','150_300')),windows['0_300']['flow'][side][key])
    b=source['books']; book={}
    assert all(r['source_ms']<=r['received_ms'] for r in b)
    for clock in ('received_ms','source_ms'):
        ps=intervals(b,clock)
        book[clock]={f'{lo}_{hi}':book_window(ps,lo,hi) for lo,hi in WINDOWS}
        for key in ('coverage_seconds','mid_coverage_seconds','near_half_mid_seconds'):
            close(sum(book[clock][f'{lo}_{hi}'][key] for lo,hi in WINDOWS[:6]),book[clock]['0_300'][key])
    a=flows['target']['0_150']['flow']['DOWN']; z=flows['target']['150_300']['flow']['DOWN']
    ratios=dict(second_over_first_qty=z['qty']/a['qty'],second_over_first_cash=z['cash']/a['cash'],
                second_over_first_shares_per_cash=z['shares_per_cash']/a['shares_per_cash'],
                second_over_first_lift_per_cash=z['branch_lift_per_cash']/a['branch_lift_per_cash'])
    gaps={arm:{w:flows[arm][w]['flow']['DOWN']['cash']-flows['target'][w]['flow']['DOWN']['cash'] for w in ('0_50','0_150','150_300','0_300')} for arm in ARMS}
    batches=[]
    for r in target:
        f=flow_totals([r])['DOWN']
        if f['qty']:
            batches.append(dict(seconds=(r['t']-START)/1000,**f))
    result=dict(status='COMPLETE', verification='PASS', native_jobs=0, model_fits=0, parameter_search=0, policy_changes=0,
        source_sha256=INPUT_SHA, checks=checks, our_sources=sources, windows=flows,
        book=book, transport_lag_ms=dict(min=min(r['received_ms']-r['source_ms'] for r in b),max=max(r['received_ms']-r['source_ms'] for r in b)),
        target_half_ratios=ratios, our_minus_target_down_cash=gaps, early_our_down_roles=roles,
        target_down_event_batches=batches,
        interpretation='Later DOWN fills are cheaper, not more numerous in aggregate shares than the first half. First-half prices are closer to 0.5 than second-half prices but spend little time near it in this market. Time, market price and own exposure co-vary; intentional waiting and private orders remain unidentified. Early passive repair dominates the OUR cost excess.',
        next_test='Design one price-sensitive marginal repair tradeoff control using current received book plus own realized and reserved branch outcomes. Retain fixed15 passive, variable Active, cash gates OFF, no forced zero floor or Target timing. Separate from UP Active changes; locate executable divergence first, then at most one distinct native treatment against a retained baseline.',
        limits='Single consumed market; no claim about what usually happens across markets, or profitability/generalization. Target observed buys from zero, fees/rebates excluded, no private NEW/cancel/queue/zero-fill evidence. Book source-clock analysis is retrospective, not an actor input. No future fill/cost saving is simulated by deleting or postponing fills.')
    render(source,intervals(b,'received_ms'),paths)
    dump_for(STEM,'RESULT',result)
    dump_for(STEM,'PROGRESS',dict(status='COMPLETE',verification='PASS',native_jobs=0,model_fits=0,parameter_search=0,policy_changes=0,
        target_legs_checked=275,book_updates_checked=len(b),our_results_reused=list(ARMS),next_candidate_frozen=False,next_native_dispatched=False))
    print(json.dumps(dict(status='PASS',half_ratios=ratios,down_cash_gaps=gaps,
        book_halves={c:{w:book[c][w] for w in ('0_150','150_300')} for c in book}),indent=2))


if __name__=='__main__':
    main()
