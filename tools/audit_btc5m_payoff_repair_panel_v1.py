"""Offline payoff coordinate audit and four-arm native verification; no fitting."""
import argparse,collections,json,math,statistics
from pathlib import Path
from audit_btc5m_target_core_loop_topology_v1 import ROOT,R,RET,MIDS,read,sha,topology,target_rows
from aggregate_btc5m_exposure_intent_ablation_v1 import get,STREAMS
from aggregate_btc5m_causal_clock_smoke_v1 import PARITY_FIELDS,compact
from audit_btc5m_repair_wait_and_dust_v1 import cohort
from btc5m_payoff_repair_gate_v1 import capacity

PACKAGE=ROOT/'.lan_worker_v1/payoff_repair_2026085_20260913_v1'
STEM='BTC5M_PAYOFF_REPAIR_V1_20260913'


def coordinates(inv,cost):
    u,d=inv['UP']-cost,inv['DOWN']-cost
    assert abs((u-d)-(inv['UP']-inv['DOWN']))<1e-7
    return dict(up=u,down=d,floor=min(u,d),best=max(u,d),gap=abs(inv['UP']-inv['DOWN']))


def describe(rows):
    floors=[x['floor'] for x in rows]
    return dict(n=len(rows),nonflat=sum(x['gap']>1e-9 for x in rows),
        both_positive=sum(x['floor']>1e-9 for x in rows),nonnegative=sum(x['floor']>=-1e-9 for x in rows),
        small_negative_between_minus1_and_zero=sum(-1<=x['floor']<-1e-9 for x in rows),
        below_minus1=sum(x['floor']<-1 for x in rows),
        floor_median=statistics.median(floors) if floors else None)


def target_audit():
    out=dict(status='COMPLETE',scope='Previously consumed eight markets only; observed acquisitions, zero starting inventory, fees unavailable.',markets={})
    allrepairs=[];allends=[]
    for mid in MIDS:
        bundle='open_funding_recovery_train_20260911_v3' if mid in MIDS[:3] else 'v20_consumed_btc5_transfer5_20260912_v1'
        path=ROOT/'.lan_worker_v1'/bundle/f'input_{mid}.json.gz';s=read(path)
        by=collections.defaultdict(list)
        for a in s['targetActions']:
            assert a['quote_type']=='BID'
            by[a['event_ms']].append(a)
        inv={'UP':0.,'DOWN':0.};cost=0.;money={}
        for t,actions in sorted(by.items()):
            for a in actions:inv[a['side']]+=a['shares'];cost+=a['price']*a['shares']
            money[t]=dict(t=t,**coordinates(inv,cost),cost=cost)
        events=topology(target_rows(s))['events'];repairs=[];ends=[]
        for i,e in enumerate(events):
            if 'P' not in e['label']:continue
            row=dict(money[e['t']],label=e['label'],before=e['before'],after=e['after'])
            repairs.append(row)
            # End of an observed payment run, not an inferred cancellation/HOLD.
            if i+1==len(events) or 'P' not in events[i+1]['label']:ends.append(row)
        out['markets'][str(mid)]=dict(source_sha256=sha(path),payment_batches=describe(repairs),
            observed_payment_run_ends=describe(ends),terminal=money[max(money)],run_end_rows=ends)
        allrepairs+=repairs;allends+=ends
    out['combined']=dict(payment_batches=describe(allrepairs),observed_payment_run_ends=describe(allends))
    out['limitations']=['Conditional settlement endpoints under this reconstruction, not liquidatable current mark-to-market.',
        'Net endpoint difference equals share gap identically; endpoints additionally depend on cumulative cost.',
        'Observed payment run ends do not identify intended stopping, pending, zero fills or Target private orders.',
        'The -1 descriptive bin comes from the earlier aspiration; not an inferred strategy threshold.',
        'No fresh markets, no fees inferred, no threshold fit or new Target runtime features.']
    (R/(STEM+'_TARGET.json')).write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=out['status'],combined=out['combined'],selected=out['markets']['2026085'])))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('stage',choices=('target','control','finish'));a=ap.parse_args()
    if a.stage=='target':target_audit();return
    m=read(PACKAGE/'manifest.json');old,ot=get(RET/'oracle-repair-2026085-up-baseline-20260913-v1')
    control,ct=get(RET/'payoff-repair-2026085-control-20260913-v1')
    parity={k:old[k]==control[k] for k in PARITY_FIELDS};parity.update({k:ot[k]==ct[k] for k in STREAMS})
    assert all(parity.values()),parity
    out=dict(status='COMPLETE',control_parity=parity,oracle=True,runtime_eligible=False,arms={})
    for tag in (('control',) if a.stage=='control' else ('control','quantity','zero','minus-one')):
        folder=RET/f'payoff-repair-2026085-{tag}-20260913-v1';d,tr=get(folder);cut=m['selection']['t'];end=1788758400000
        assert d['clock_smoke']['manifest_sha256']==sha(PACKAGE/'manifest.json')
        assert d['oracle'] and not d['runtime_eligible'] and d['target_direction_input'] and d['target_runtime_access']
        assert d['active_native_submits']==0
        mode=d['clock_smoke']['money_mode']
        start=tr['money_events'][0];assert start['t']==cut and start==ct['money_events'][0]
        prefix={k:[r for r in tr[k] if r['t']<cut]==[r for r in ct[k] if r['t']<cut] for k in STREAMS};assert all(prefix.values())
        new=[(p['t'],o) for p in tr['plans'] if p['t']>=cut for o in p['operations'] if o['kind']=='NEW']
        if tag!='control':assert all(o['side']=='DOWN' for t,o in new)
        limited=[];subminimum=[]
        for row in tr['money_rows']:
            value,_=capacity(row['state'],row['side'],row['price'],row['requested'],mode)
            expected=row['requested'] if tag=='control' else min(row['requested'],round(math.floor((value+1e-10)/.01)*.01,8))
            assert abs(expected-row['capped'])<1e-7
            if row['capped']<row['requested']-1e-9:limited.append(row)
            if row['side']=='DOWN' and 0<row['capped']<max(18.,1./row['price'])-1e-9:subminimum.append(row)
            # Independently re-add reservation quantities and cash from all live owners.
            for side in ('UP','DOWN'):
                q=sum(x['qty'] for x in row['state']['owners'] if x['side']==side)
                c=sum(x['qty']*x['limit'] for x in row['state']['owners'] if x['side']==side)
                assert abs(q-row['state']['pending_qty'][side])<1e-7
                assert abs(c-row['state']['pending_cash'][side])<1e-7
        # Every realized NEW in the isolated arms came through the cap; minima are still mandatory.
        for t,o in new:
            if tag=='control':continue
            assert any(r['t']==t and r['side']==o['side'] and r['price']==o['price'] and r['capped']==o['qty'] for r in tr['money_rows'])
            assert o['qty']>=18 and o['price']*o['qty']>=1.-1e-9
        previous=cut;prev=coordinates(start['state']['inv'],start['state']['cost']);area=0.;first_zero=first_minus=None;curve=[]
        for r in tr['states']:
            if r['t']<=cut or r['t']>end:continue
            area+=max(0.,-prev['floor'])*(r['t']-previous)/1000.
            coord=coordinates(r['inv'],r['cost']);previous=r['t']
            if coord['floor']>=0 and first_zero is None:first_zero=r['t']-cut
            if coord['floor']>=-1 and first_minus is None:first_minus=r['t']-cut
            if coord!=prev:curve.append(dict(t=r['t'],**coord))
            prev=coord
        area+=max(0.,-prev['floor'])*(end-previous)/1000.
        final=coordinates(d['final_inventory'],d['final_cost'])
        out['arms'][tag]=dict(metrics=compact(d),payoff=final,negative_floor_area_currency_seconds=area,
            first_nonnegative_floor_ms=first_zero,first_floor_at_least_minus_one_ms=first_minus,
            old_fifo_cohort=cohort(d,tr,m['selection'],.01),prefix_parity=prefix,
            candidate_cap_visits=len(tr['money_rows']),reduced_candidate_visits=len(limited),subminimum_down_visits=len(subminimum),
            first_subminimum_down=subminimum[0] if subminimum else None,last_subminimum_down=subminimum[-1] if subminimum else None,
            postcut_new_orders=len(new),postcut_new_up=sum(o['side']=='UP' for t,o in new),
            payoff_curve=curve,source_sha256=sha(folder/'result.json'),trace_sha256=sha(folder/'clock_trace.json.gz'))
    out['limitations']=m['limits']
    (R/(STEM+('_CONTROL.json' if a.stage=='control' else '_RESULT.json'))).write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=out['status'],control_parity_pass=all(parity.values()),arms={k:{n:v[n] for n in ('metrics','payoff','negative_floor_area_currency_seconds','subminimum_down_visits','postcut_new_up','first_nonnegative_floor_ms')} for k,v in out['arms'].items()})))


if __name__=='__main__':main()
