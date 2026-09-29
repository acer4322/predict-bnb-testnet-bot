"""V30: cross-case price/repair-size observations and an existing OUR NEW audit.

Only saved, previously consumed data. No database/network/native/model fitting.
"""
import collections
import json
import math
import statistics

from audit_btc5m_target_readdition_alignment_v1 import (
    ROOT, R, INPUT, INPUT_SHA, START, read, sha, get, config, RET,
    target_check, reconstruct, canonical_legs, close, flow_totals, dump_for,
)
from audit_btc5m_terminal_casepack_core_loop_v1 import verify_reconstruction, bucket_geometry

STEM = 'BTC5M_PRICE_RETENTION_CASEPANEL_V1_20260913'
D = R/'target_casepacks_v1'
PACK = D/'TARGET_TERMINAL_SMALL_LOSS_AND_LOCK_CASEPACK_V1_20260913.json'
AUDIT = D/'TARGET_NORMAL_SMALL_OUTCOME_PATH_AUDIT_V1_20260913.json'
ALL_CASES = D/'TARGET_ONE_NORMAL_ONE_SMALL_OUTCOME_ALL_V1_20260913.json'
EXPECTED = (1871599,2202440,1962121,2210304,2084104,1977248,1963934,1884370,1942969,1941817,1760051,2059306)
EPS = 1e-8


def opposite(s):
    return 'DOWN' if s=='UP' else 'UP'


def totals(rows,side):
    f=flow_totals(rows)[side]
    return dict(f, shares_per_cash=f['qty']/f['cash'] if f['cash'] else None,
                lift_per_cash=(f['qty']-f['cash'])/f['cash'] if f['cash'] else None)


def load_paths():
    supplied=read(PACK); old=read(AUDIT)
    assert sha(ALL_CASES)==old['source_sha256']
    old4=read(D/'TARGET_CASEPACK_CORE_LOOP_ANALYSIS_V1_20260913.json')
    assert sha(PACK)==old4['source_sha256'] and old4['validation']['all_case_accounting']=='PASS'
    assert old['validation']['all_checks']=='PASS'
    paths={}; metadata={}
    for case in supplied['cases']:
        rows,start,end=verify_reconstruction(case); mid=case['summary']['marketId']
        for row in rows:
            row['t']-=start
        paths[mid]=rows
        metadata[mid]=dict(origin='V21 supplied second-level casepack',fill_legs=case['summary']['fillCount'],event_seconds=len(rows),
                           original_start_ms=start,source_summary=case['summary'])
    for case in old['paths']:
        legs=[dict(t=round(r['seconds']*1000),side=s,route=k,qty=v['qty'],cash=v['cash'])
              for r in case['curve'] for s,ff in r['flow'].items() for k,v in ff.items() if v['qty']]
        rows=reconstruct(legs)
        assert len(rows)==len(case['curve']) and rows==reconstruct(list(reversed(legs)))
        for row,old_row in zip(rows,case['curve']):
            for k,v in old_row['geometry'].items():
                if isinstance(v,(int,float)) and not isinstance(v,bool):
                    close(row['geometry'][k],v)
                else:
                    assert row['geometry'][k]==v
            row['flow']=old_row['flow']
        mid=case['market_id']; paths[mid]=rows
        metadata[mid]=dict(origin='V22 verified full curve; original DB not requeried',fill_legs=case['recorded_fill_legs'],
                           event_seconds=len(rows),source_rows_sha256=case['source_rows_sha256'],source_summary=case['terminal'])
    assert tuple(paths)==EXPECTED
    assert sha(INPUT)==INPUT_SHA
    primary,checks=target_check(read(INPUT))
    for row in primary:
        row['t']-=START
    paths[2026085]=primary;metadata[2026085]=dict(origin='V29 pinned main market',fill_legs=275,event_seconds=100,original_start_ms=START,checks=checks)
    for mid,rows in paths.items():
        assert all(0<=r['t']<300000 for r in rows)
        assert sum(v['legs'] for r in rows for ff in r['flow'].values() for v in ff.values())==metadata[mid]['fill_legs']
    return paths,metadata


def side_set(row):
    return {s for s in ('UP','DOWN') if sum(v['qty'] for v in row['flow'][s].values())>EPS}


def classify_price(g):
    margin=g['realized_weak_average_price']-g['no_addition_constant_price_feasibility_threshold']
    return 'ABOVE' if margin>EPS else 'BELOW' if margin < -EPS else 'EQUAL'


def acquisition_runs(rows):
    """Observed pure-weak fill runs, ending at a different side, crossing, or EOF.

    This is not an inference about private repair work items or order stopping.
    """
    out=[]; i=0
    while i<len(rows):
        first=rows[i]; g=bucket_geometry(first,0)
        if not(g and g['pure_weak_acquisition'] and g['before_weak_payoff']<0<g['before_strong_payoff']):
            i+=1;continue
        weak=g['before_weak_side'];strong=opposite(weak); j=i
        while j+1<len(rows) and rows[j]['inv'][strong]>rows[j]['inv'][weak]+EPS and side_set(rows[j+1])=={weak}:
            j+=1
        f=totals(rows[i:j+1],weak);G=g['before_strong_payoff'];L=-g['before_weak_payoff'];p=f['vwap'];q=f['qty']
        after=rows[j]['geometry'];next_row=rows[j+1] if j+1<len(rows) else None
        close(after[weak.lower()],-L+(1-p)*q);close(after[strong.lower()],G-p*q)
        next_add=next_row is not None and strong in side_set(next_row)
        partial=after[weak.lower()] < -EPS and after[strong.lower()]>0
        out.append(dict(first_seconds=first['t']/1000,last_seconds=rows[j]['t']/1000,weak=weak,strong=strong,
            before=first['before']['geometry'],after=after,flow=f,buckets=j-i+1,realized_price=p,
            price_threshold=G/(G+L),price_margin=p-G/(G+L),
            q_to_weak_zero=L/(1-p),observed_q_over_q_zero=q/(L/(1-p)),
            residual_loss_over_start_loss=max(0.,-after[weak.lower()])/L,
            strong_payoff_retained=after[strong.lower()]/G,
            original_net_retained=(rows[j]['inv'][strong]-rows[j]['inv'][weak])/(G+L),
            next_seconds=next_row['t']/1000 if next_row else None,
            next_flow=next_row['flow'] if next_row else None,next_has_original_strong_acquisition=next_add,
            next_add_before_weak_zero=partial and next_add,
            next_both_sides=next_row is not None and len(side_set(next_row))==2,
            end_reason='BALANCE_CROSSED' if rows[j]['inv'][strong]<=rows[j]['inv'][weak]+EPS else 'NEXT_SIDE_CHANGED' if next_row else 'END_OF_OBSERVATIONS',
            interpretation='Observed fill run only; no cancel, stop or intended ticket inferred'))
        i=j+1
    return out


def measure(rows):
    final=rows[-1]['geometry'];terminal_strong='UP' if final['up_net']>0 else 'DOWN';terminal_weak=opposite(terminal_strong)
    windows={}
    for lo,hi in ((0,50),(50,100),(100,150),(150,200),(200,250),(250,300),(0,150),(150,300),(0,300)):
        # Include any second-zero fills rather than dropping the first bucket in another market.
        selected=[r for r in rows if (lo*1000<r['t'] or lo==0 and r['t']==0) and r['t']<=hi*1000]
        windows[f'{lo}_{hi}']={s:totals(selected,s) for s in ('UP','DOWN')}
    for s in ('UP','DOWN'):
        for k in ('qty','cash'):
            close(windows['0_150'][s][k]+windows['150_300'][s][k],windows['0_300'][s][k])
    pure=[]
    for r in rows:
        g=bucket_geometry(r,0)
        if not(g and g['pure_weak_acquisition'] and g['before_weak_payoff']<0<g['before_strong_payoff']):
            continue
        weak=g['before_weak_side'];strong=opposite(weak);G=g['before_strong_payoff'];L=-g['before_weak_payoff'];p=g['realized_weak_average_price'];q=g['weak_qty']
        ag=g['after'][strong.lower()];af=g['after'][weak.lower()]
        margin=p-G/(G+L)
        g.update(price_condition=classify_price(g),price_margin=margin,
            q_over_q_zero=q/g['no_addition_constant_price_q_to_weak_zero'],
            strong_payoff_retained=ag/G,
            signed_loss_gain_before=L/G,signed_loss_gain_after=-af/ag if ag>EPS else None)
        if ag>EPS:
            delta=(-af/ag)-(L/G)
            predicted=q*(p*(G+L)-G)/(G*ag)
            close(delta,predicted)
            g['signed_loss_gain_delta']=delta
        pure.append(g)
    good=[g for g in pure if g['price_condition']!='ABOVE'];bad=[g for g in pure if g['price_condition']=='ABOVE']
    runs=acquisition_runs(rows);partial_runs=[x for x in runs if x['next_add_before_weak_zero']]
    signs=[1 if r['geometry']['up_net']>EPS else -1 if r['geometry']['up_net'] < -EPS else 0 for r in rows]
    nz=[s for s in signs if s]
    count=dict(pure_loss_repair_buckets=len(pure),price_not_above=len(good),price_above=len(bad),
        price_above_by_more_than_001=sum(g['price_margin']>.01+EPS for g in bad),
        price_above_by_more_than_003=sum(g['price_margin']>.03+EPS for g in bad),
        good_price_still_weak_negative=sum(g['after'][g['before_weak_side'].lower()] < -EPS for g in good),
        repair_runs=len(runs),next_strong_before_weak_zero=len(partial_runs),
        good_price_run_then_strong_before_weak_zero=sum(x['price_margin']<=EPS for x in partial_runs),
        both_side_event_seconds=sum(len(side_set(r))==2 for r in rows),
        net_direction_sign_changes=sum(a!=b for a,b in zip(nz,nz[1:])))
    return dict(terminal=final,terminal_higher_side=terminal_strong,terminal_lower_side=terminal_weak,
        terminal_label='Post-hoc accounting label, not an identified fixed episode intention',
        windows=windows,counts=count,pure_repair_buckets=pure,repair_runs=runs,
        good_price_partial_run_q_zero_fractions=[x['observed_q_over_q_zero'] for x in partial_runs if x['price_margin']<=EPS])


def our_new_probe():
    c=config('legal');package=ROOT/'.lan_worker_v1/fixed15_maintenance_legal_2026085_20260913_v1'
    manifest=read(package/'manifest.json')
    assert all(sha(package/name)==h for name,h in manifest['files'].items())
    assert manifest['cash_budget_enabled'] is False and all(manifest[k] is None for k in ('active_cash_cap','passive_cash_cap','capital_cap'))
    audit=read(R/(c['STEM']+'_RESULT.json'));assert audit['verification']=='PASS'
    result,trace=get(RET/c['JOB']);assert sha(RET/c['JOB']/'result.json')==audit['candidate']['result_sha256']
    owners={o['key']:o for o in trace['demand_final']['all_final_carriers']}
    source_books={b['received_ms']:b for b in read(INPUT)['books']}
    rows=collections.defaultdict(list)
    for row in trace['money_rows']:
        rows[row['t'],row['side'],round(row['price'],8)].append(row)
    out=[]
    for plan in trace['plans']:
        if plan['t']>START+150000:continue
        for op in plan['operations']:
            if op['kind']!='NEW' or op['side']!='DOWN':continue
            found=[r for r in rows[plan['t'],'DOWN',round(op['price'],8)] if r['capped']>=op['qty']-EPS]
            assert len(found)==1
            row=found[0];state=row['state'];G=state['payoff']['UP'];F=state['payoff']['DOWN'];p=op['price'];q=op['qty']
            assert op['route']=='PASSIVE' and q==15 and p*q>=1-EPS
            book=source_books[plan['t']];assert book['source_ms']<=book['received_ms']==plan['t']
            assert p<round(1-book['best_bid'],10)
            threshold=G/(G-F) if F<0<G else None
            classification='RATIO_IMPROVES' if threshold is not None and p<threshold-EPS else 'RATIO_WORSENS' if threshold is not None and p>threshold+EPS else 'RATIO_EQUAL' if threshold is not None else 'BOTH_POSITIVE' if G>0 and F>0 else 'OTHER'
            # Keep every reservation. Full-fill scenarios are neither inventory credit nor forecasts.
            cq=state['pending_qty']['DOWN'];cc=state['pending_cash']['DOWN'];uc=state['pending_cash']['UP']
            scenario=dict(confirmed_down=F,down_pending_only=F+cq-cc,up_pending_only=F-uc,
                all_pending=F+cq-cc-uc,all_pending_plus_ticket=F+cq-cc-uc+(1-p)*q,
                up_after_down_pending_and_ticket=G-cc-p*q)
            out.append(dict(seconds=(plan['t']-START)/1000,op=op,before_state=state,
                price_threshold=threshold,classification=classification,book=book,
                reservation_scenarios=scenario,actual_owner_terminal=owners[op['key']]))
    assert len(out)==142 and len({r['op']['key'] for r in out})==142
    bad=[r for r in out if r['classification']=='RATIO_WORSENS']
    original={r['t']:r['desired'] for r in trace['observations']}
    shape=[];effective_changes=0
    for r in trace['intent']:
        u,d=r['inv']['UP'],r['inv']['DOWN']
        amplitude=abs(math.tanh(result['theta'][3]*(u-d)/(1+u+d)))
        close(amplitude,r['applied_exposure'])
        wanted=original[r['t']]
        close((wanted['UP']-wanted['DOWN'])/(wanted['UP']+wanted['DOWN']),amplitude)
        effective_changes+=wanted!=r['desired']
        shape.append(dict(seconds=(r['t']-START)/1000,inv=r['inv'],amplitude=amplitude,
                          original_desired=wanted,effective_desired=r['desired']))
    checkpoints={str(sec):next(r for r in reversed(shape) if r['seconds']<=sec) for sec in (16,50,126,150,245,300)}
    shadow=held_amplitude_shadow(shape,result['theta'][3])
    return dict(status='PASS',source_job=c['JOB'],manifest_sha256=sha(package/'manifest.json'),
        result_sha256=sha(RET/c['JOB']/'result.json'),trace_sha256=sha(RET/c['JOB']/'clock_trace.json.gz'),
        counts=dict(collections.Counter(r['classification'] for r in out)),new_rows=out,
        above_threshold_actual_qty=math.fsum(r['actual_owner_terminal']['filled'] for r in bad),
        above_threshold_actual_payment=math.fsum(r['actual_owner_terminal']['payment'] for r in bad),
        first_above=bad[0] if bad else None,
        direction_amplitude=dict(status='PASS',intent_rows=len(shape),theta3=result['theta'][3],
            effective_desired_changed_by_finite_goal_rows=effective_changes,checkpoints=checkpoints,
            formula='abs(tanh(theta3*(U-D)/(1+U+D))); original desired UP fraction=(1+amplitude)/2',
            dedup='Previously diagnosed in V13/V16; current V27 trace confirms it remains after the later repair changes. Finite-goal effective desired is checked separately, not confused with original desired.'),
        held_amplitude_shadow=shadow,
        limits='Accepted original NEWs at pre-NEW current OWN state and received book. These original fills do not predict a modified path; deleting them is not a causal saving. Pending scenarios are separately retained, never credited as confirmed fills.')


def held_amplitude_shadow(shape,theta3):
    """One no-fit upstream allocation diagnostic, never an executable policy."""
    def apply(inputs):
        held=None;birth=None;output=[]
        for r in inputs:
            inv=r['inv'];net=inv['UP']-inv['DOWN'];gross=1+inv['UP']+inv['DOWN']
            if held is None and abs(net)>EPS:
                held=abs(math.tanh(theta3*net/gross));birth=r['seconds']
            amplitude=held if held is not None else r['amplitude']
            desired_gross=sum(r['original_desired'].values())
            proposed={'UP':desired_gross*(1+amplitude)/2,'DOWN':desired_gross*(1-amplitude)/2}
            close(sum(proposed.values()),desired_gross)
            output.append(dict(seconds=r['seconds'],held_amplitude=amplitude,birth_seconds=birth,
                inv=inv,original_desired=r['original_desired'],shadow_original_desired=proposed,
                delta_desired={s:proposed[s]-r['original_desired'][s] for s in proposed}))
        return output
    shadow=apply(shape)
    # Prefix outputs may not change when later inventory observations are absent.
    for cut in (1,10,len(shape)//2,len(shape)-1):
        assert apply(shape[:cut])==shadow[:cut]
    assert any(abs(r['delta_desired']['UP'])>EPS for r in shadow)
    first=next(r for r in shadow if abs(r['delta_desired']['UP'])>EPS)
    snapshots={str(sec):next(r for r in reversed(shadow) if r['seconds']<=sec) for sec in (16,50,126,150,245,300)}
    # This diagnostic intentionally distinguishes history at the same current inventory.
    a=dict(seconds=0,inv=dict(UP=100.,DOWN=0.),amplitude=0.,original_desired=dict(UP=100.,DOWN=100.))
    b=dict(a,inv=dict(UP=100.,DOWN=50.))
    current=dict(a,seconds=1,inv=dict(UP=100.,DOWN=99.))
    assert apply([a,current])[-1]['shadow_original_desired']!=apply([b,current])[-1]['shadow_original_desired']
    return dict(status='SHADOW_ONLY',runtime_eligible=False,candidates=1,parameters_fit=0,
        source='First nonzero confirmed OUR inventory supplies an amplitude reference; the already authorized oracle UP sign is unchanged. No Target quantities, timing, prices or final exposure.',
        dedup='V6 held direction sign while retaining current amplitude. This candidate holds the first OWN amplitude as well; V13/V16 diagnosed the missing intensity without this treatment.',
        prefix_invariance='PASS at four truncation points',same_current_state_different_history='PASS',
        first_upstream_allocation_difference=first,checkpoints=snapshots,
        row_count=len(shadow),changed_upstream_allocation_rows=sum(abs(r['delta_desired']['UP'])>EPS for r in shadow),
        limits='Arithmetic on baseline states only. Not a predicted inventory, fill, cancellation or PnL path. Existing finite-goal repair overrides, reservations, sizing and plan legality still require separate integration. First OWN seed is arbitrary and may retain too much exposure; it is not identified Target intent.',
        native_dispatched=False,executable_candidate_frozen=False)


def render(measured):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':['Microsoft JhengHei','DejaVu Sans'],'axes.unicode_minus':False,'font.size':10})
    from matplotlib.ticker import FuncFormatter
    fig,axes=plt.subplots(1,2,figsize=(14,7),layout='constrained')
    labels=[];first=[];second=[]
    for mid in (2026085,)+EXPECTED:
        m=measured[mid];w=m['terminal_lower_side'];labels.append(str(mid))
        first.append(m['windows']['0_150'][w]['vwap']);second.append(m['windows']['150_300'][w]['vwap'])
    yy=list(range(len(labels)))
    for y,a,b in zip(yy,first,second):axes[0].plot([a,b],[y,y],color='#aab2bf',lw=1)
    axes[0].scatter(first,yy,color='#2878ad',label='前半場');axes[0].scatter(second,yy,color='#d46d32',label='後半場')
    axes[0].set_yticks(yy,labels);axes[0].invert_yaxis();axes[0].set(xlim=(0,1),xlabel='終局較少份額一側的成交均價',title='有便宜修復，也有後半場更貴的案例')
    axes[0].legend();axes[0].grid(axis='x',alpha=.15)
    groups=[(True,'#2878ad','修復仍未到零，下一成交秒桶已有原強側成交'),(False,'#929ca8','其他可觀察修復段')]
    for flag,color,label in groups:
        points=[r for mid,m in measured.items() if mid!=2026085 for r in m['repair_runs'] if r['next_add_before_weak_zero']==flag]
        axes[1].scatter([r['realized_price']/r['price_threshold'] for r in points],
                        [r['observed_q_over_q_zero'] for r in points],s=24,alpha=.7,color=color,label=label)
    primary=measured[2026085]['repair_runs']
    axes[1].scatter([r['realized_price']/r['price_threshold'] for r in primary],
                    [r['observed_q_over_q_zero'] for r in primary],marker='x',s=55,color='#b33d3d',label='2026085')
    axes[1].axvline(1,color='#6c7581',ls=':',lw=1);axes[1].axhline(1,color='#6c7581',ls=':',lw=1)
    axes[1].set(xlabel='該段均價 / 起始損益價格條件',ylabel='已成交量 / 單價固定時補到零所需量',
                title='價格條件可比較效率，仍無法決定修復量',yscale='log',xscale='log')
    axes[1].yaxis.set_major_formatter(FuncFormatter(lambda v,pos:f'{v:g}'))
    axes[1].xaxis.set_major_formatter(FuncFormatter(lambda v,pos:f'{v:g}'))
    axes[1].grid(alpha=.15);axes[1].legend(fontsize=8,loc='best')
    fig.suptitle('12 場既有案例 + 2026085｜修復價格、份額與再次加倉',fontsize=15)
    fig.supxlabel('成交後的描述性比較；同秒內順序、未成交掛單及可成交深度未知。左圖終局方向不能當作整局固定意圖。',fontsize=9)
    for suffix in ('.png','.svg'):fig.savefig(R/(STEM+suffix),dpi=140)
    plt.close(fig)


def main():
    protocol=dict(status='OFFLINE_DESCRIPTIVE_DIAGNOSTIC',runtime_eligible=False,
        case_ids=list(EXPECTED),primary=2026085,selection='All 12 already reconstructed V21/V22 cases, fixed before this price/size analysis. Outcome selected, not held out.',
        inputs='Saved V21 casepack, V22 verified curves, V29 pinned source and V27 legal original trace; no DB extraction or new replay',
        dedup='V21 already derived p*=G/(G+L); V22 studied final buckets and V29 main-market fixed halves. New: all-case half-price contrast, full pure-weak acquisition runs ending in renewed strong acquisition, price-margin sensitivity, and 142 actual early OUR NEW decisions. V13/V16 direction amplitude limitation is rechecked on V27, not claimed as a new discovery.',
        price_formula='p*=G/(G+L), G>0,L>0; pure weak buy q changes strong G-pq and weak -L+(1-p)q. This describes signed loss/gain slope and zero feasibility, not intent or a veto.',
        quantity_formula='q(r)=(L-r*G)/(1-p-r*p), if the denominator and residual regime permit. Desired residual ratio r is unidentified; r=0 is not assumed.',
        run_definition='Maximal consecutive observed buckets buying only the pre-run weaker side, stopping at another side, net crossing or observed EOF. No inferred order stopping.',
        sensitivity_price_margins=[.01,.03],sensitivity_is_descriptive=True,
        native_jobs=0,model_fits=0,parameter_search=0,policy_changes=0,upstream_shadow_candidates=1,
        invariants='Fixed15 main-market Passive; variable Active; NEW>=1; cash gates OFF; all pending/UNKNOWN reserved. No Target or settlement labels in actor.')
    dump_for(STEM,'PROTOCOL',protocol)
    paths,metadata=load_paths();measured={mid:measure(rows) for mid,rows in paths.items()}
    ours=our_new_probe();cases=[measured[mid] for mid in EXPECTED]
    summed={k:sum(c['counts'][k] for c in cases) for k in cases[0]['counts']}
    half_cheaper=sum(c['windows']['150_300'][c['terminal_lower_side']]['vwap']<c['windows']['0_150'][c['terminal_lower_side']]['vwap'] for c in cases)
    fractions=[q for c in cases for q in c['good_price_partial_run_q_zero_fractions']]
    summary=dict(case_markets=12,source_fill_legs=sum(metadata[mid]['fill_legs'] for mid in EXPECTED),
        event_seconds=sum(metadata[mid]['event_seconds'] for mid in EXPECTED),counts=summed,
        terminal_lower_side_cheaper_second_half_markets=half_cheaper,
        markets_with_above_price_repair=sum(c['counts']['price_above']>0 for c in cases),
        markets_with_above_price_margin_003=sum(c['counts']['price_above_by_more_than_003']>0 for c in cases),
        markets_with_good_price_partial_run_then_add=sum(c['counts']['good_price_run_then_strong_before_weak_zero']>0 for c in cases),
        markets_with_net_sign_changes=sum(c['counts']['net_direction_sign_changes']>0 for c in cases),
        partial_run_q_fraction_median=statistics.median(fractions) if fractions else None,
        partial_run_q_fraction_min=min(fractions) if fractions else None,partial_run_q_fraction_max=max(fractions) if fractions else None)
    hashes={str(p.relative_to(ROOT)):sha(p) for p in (PACK,AUDIT,ALL_CASES,INPUT)}
    result=dict(status='COMPLETE',verification='PASS',runtime_eligible=False,native_jobs=0,model_fits=0,parameter_search=0,policy_changes=0,
        source_sha256=hashes,metadata=metadata,summary=summary,markets=measured,our_early_new_probe=ours,
        findings='Price and own outcome geometry explain marginal exchange efficiency but not a unique repair quantity. Cross-case exceptions reject a universal midgame-cheap or p<=p* hard-veto imitation. Favorable-price partial repair runs followed by strong acquisition motivate measuring episode repair targets/history. Most original early OUR NEWs already satisfy the favorable price condition.',
        next_scope='A single no-fit first-OWN-amplitude shadow isolates the known direction-intensity collapse. It changes upstream allocations but has no execution evidence; integrate finite-goal overrides and pending reservations before freezing any native treatment. Retain price as diagnostic rather than unexplained veto.',
        unresolved='Desired remaining loss/gain ratio, urgency, private intentions, contemporaneous executable depth in casepack markets, queue/zero-fill universe, fees/rebates. No counterfactual fill outcome or causal savings demonstrated.')
    render(measured);dump_for(STEM,'RESULT',result)
    dump_for(STEM,'PROGRESS',dict(status='COMPLETE',verification='PASS',native_jobs=0,model_fits=0,parameter_search=0,policy_changes=0,
        case_markets=12,primary_market=2026085,new_decisions_checked=142,upstream_shadow_candidates=1,next_candidate_frozen=False,next_native_dispatched=False))
    print(json.dumps(dict(status='PASS',summary=summary,primary=measured[2026085]['counts'],our_counts=ours['counts'],
        our_above_price_original_qty=ours['above_threshold_actual_qty'],our_above_price_original_payment=ours['above_threshold_actual_payment'],
        shadow_first=ours['held_amplitude_shadow']['first_upstream_allocation_difference'],
        shadow_mid=ours['held_amplitude_shadow']['checkpoints']['150']),indent=2))


if __name__=='__main__':main()
