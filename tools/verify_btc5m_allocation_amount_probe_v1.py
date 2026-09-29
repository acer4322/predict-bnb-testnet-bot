"""Independent source and saved-prediction audit; never fit locally."""
import collections
import json
import math
import statistics
from audit_btc5m_target_core_loop_topology_v1 import ROOT,R,RET,read,sha

PACKAGE=ROOT/'.lan_worker_v1/allocation_amount_probe_20260913_v1'
STEM='BTC5M_ALLOCATION_AMOUNT_PROBE_V1_20260913'
LABELS=('weak_cash_fraction','log_total_cash')


def metrics(preds):
    if not preds:return dict(n=0)
    return dict(n=len(preds),targets={k:dict(mse=math.fsum((p['p'][k]-p['y'][k])**2 for p in preds)/len(preds),
        mae=math.fsum(abs(p['p'][k]-p['y'][k]) for p in preds)/len(preds)) for k in LABELS})


def close(a,b):
    assert a.keys()==b.keys()
    for k,v in a.items():
        if isinstance(v,dict):close(v,b[k])
        else:assert abs(v-b[k])<1e-12,(k,v,b[k])


def main():
    m=read(PACKAGE/'manifest.json');assert m==read(R/(STEM+'_PREREGISTERED.json'))
    assert all(sha(PACKAGE/n)==h for n,h in m['files'].items())
    parent=ROOT/'.lan_worker_v1/concurrent_flow_probe_20260913_v1/dataset.json'
    assert sha(parent)==m['parent_dataset_sha256']
    data=read(PACKAGE/'dataset.json');rows=data['rows'];old=read(parent)['rows'];assert len(rows)==len(old)==1097
    raw={}
    for item in data['sources']:
        path=ROOT/item['path'];assert sha(path)==item['sha256'];s=read(path)
        raw[s['market']['market_id']]=s['targetActions']
    previous={};availability=0
    for r,o in zip(rows,old):
        assert all(r[k]==v for k,v in o.items())
        acts=raw[r['market']];past=[a for a in acts if a['event_ms']<r['anchor_ms']]
        weak='DOWN' if r['strong_side']=='UP' else 'UP'
        costs=math.fsum(a['shares']*a['price'] for a in past)
        quantities={s:math.fsum(a['shares'] for a in past if a['side']==s) for s in ('UP','DOWN')}
        close(r['amount_money'],dict(cost_log=math.log1p(costs),
            strong_payoff_per_cost=(quantities[r['strong_side']]-costs)/costs,weak_payoff_per_cost=(quantities[weak]-costs)/costs))
        def cash_at(t):
            legs=[a for a in acts if a['event_ms']==t];total=math.fsum(a['shares']*a['price'] for a in legs)
            return dict(weak_cash_fraction=math.fsum(a['shares']*a['price'] for a in legs if a['side']==weak)/total,log_total_cash=math.log1p(total))
        close(r['amount_labels'],cash_at(r['next_fill_bucket']))
        last=cash_at(r['last_fill_bucket'])
        assert abs(r['amount_state']['last_weak_cash_fraction']-last['weak_cash_fraction'])<1e-12
        assert abs(r['amount_state']['last_cash_log']-last['log_total_cash'])<1e-12
        prev=previous.get(r['market']);continuous=prev and prev['next_fill_bucket']==r['last_fill_bucket']
        p=cash_at(prev['last_fill_bucket']) if continuous else dict(weak_cash_fraction=-1.,log_total_cash=-1.)
        assert abs(r['amount_state']['prior_weak_cash_fraction']-p['weak_cash_fraction'])<1e-12
        assert abs(r['amount_state']['prior_cash_log']-p['log_total_cash'])<1e-12
        assert all(r['amount_state'][k]==r['features'][k] for k in data['state_features'][:7])
        assert r['book_received_ms']<r['anchor_ms']<r['next_fill_bucket']
        availability+=int(all(a['observed_at_ms']<r['anchor_ms'] for a in past))
        previous[r['market']]=r
    folder=RET/'allocation-amount-probe-20260913-v1';res=read(folder/'result.json')
    assert res['status']=='COMPLETE' and not res['native'] and not res['runtime_eligible']
    assert res['manifest_sha256']==sha(PACKAGE/'manifest.json')
    assert res['predictions_sha256']==sha(folder/'predictions.json')
    preds=read(folder/'predictions.json');assert len(preds)==7456
    byrow={(r['market'],r['anchor_ms']):r for r in rows};groups=collections.defaultdict(list);seen=set()
    for p in preds:
        key=(p['fold'],p['model'],p['market'],p['anchor']);assert key not in seen;seen.add(key)
        assert p['y']==byrow[(p['market'],p['anchor'])]['amount_labels']
        assert 0<=p['p']['weak_cash_fraction']<=1 and p['p']['log_total_cash']>=0
        groups[(p['fold'],p['model'])].append(p)
    for fold in res['results']:
        f=fold['fold'];assert set(f['train']).isdisjoint(f['test'])
        if f['name'].startswith('FORWARD'):
            assert max(r['next_fill_bucket'] for r in rows if r['market'] in f['train'])<min(r['anchor_ms'] for r in rows if r['market'] in f['test'])
        for model,saved in fold['models'].items():
            ps=groups[(f['name'],model)]
            assert {(p['market'],p['anchor']) for p in ps}=={k for k in byrow if k[0] in f['test']}
            close(metrics(ps),saved['score'])
            for mid,s in saved['per_market'].items():close(metrics([p for p in ps if p['market']==int(mid)]),s)
            for name,field,value in [('both_only','joint_label','BOTH'),('first_observed_parents','next_all_parents_first_observed_here',True),('no_surplus_cross','next_surplus_crossing',False)]:
                close(metrics([p for p in ps if byrow[(p['market'],p['anchor'])][field]==value]),saved[name])
    gates={};fw=res['results'][-1]['models']
    for candidate,baseline in m['comparisons']:
        passed=True;details={}
        for k in LABELS:
            gains=[metrics(groups[(f['fold']['name'],baseline)])['targets'][k]['mse']-metrics(groups[(f['fold']['name'],candidate)])['targets'][k]['mse'] for f in res['results'][:-1]]
            fg=[fw[baseline]['per_market'][mid]['targets'][k]['mse']-v['targets'][k]['mse'] for mid,v in fw[candidate]['per_market'].items()]
            c,b=(fw[x]['score']['targets'][k] for x in (candidate,baseline))
            ok=sum(v>0 for v in gains)>=6 and statistics.median(gains)>0 and statistics.median(fg)>0 and c['mse']<b['mse'] and c['mae']<=b['mae']
            passed &= ok;details[k]=dict(passed=ok,lomo_wins=sum(v>0 for v in gains),forward_wins=sum(v>0 for v in fg),relative_forward_mse_reduction=(b['mse']-c['mse'])/b['mse'])
        c,b=(fw[x]['both_only']['targets']['weak_cash_fraction'] for x in (candidate,baseline))
        passed &= c['mse']<=b['mse'] and c['mae']<=b['mae']
        key=candidate+'_vs_'+baseline
        assert passed==(res['comparisons'][key]['verdict']=='SUPPORTED_AMOUNT_INFORMATION_CLUE')
        gates[key]=dict(passed=passed,targets=details)
    output=dict(status='PASS',fit=False,predictions=len(preds),rows=len(rows),source_hashes=True,original_rows_preserved=True,
        independently_recomputed_cash_features_labels_scores=True,strict_market_splits=True,gates=gates,
        observer_available_cumulative_target_rows=availability,
        note='Target event-time retrospective features only, not observer-time availability or private decision reconstruction.',
        result_sha256=sha(folder/'result.json'))
    (R/(STEM+'_VERIFICATION.json')).write_text(json.dumps(output,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(output))


if __name__=='__main__':main()
