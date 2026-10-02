"""Same-anchor joint observed-flow labels; no fitting or native execution here."""
import collections,json,math,shutil
from pathlib import Path
from audit_btc5m_target_core_loop_topology_v1 import ROOT,R,read,sha

PACKAGE=ROOT/'.lan_worker_v1/concurrent_flow_probe_20260913_v1'
STEM='BTC5M_CONCURRENT_FLOW_PROBE_V1_20260913'


def signature(legs,strong):
    weak='DOWN' if strong=='UP' else 'UP'
    q={s:math.fsum(a['shares'] for a in legs if a['side']==s) for s in ('UP','DOWN')}
    cash=math.fsum(a['shares']*a['price'] for a in legs)
    token='BOTH' if q[weak]>1e-9 and q[strong]>1e-9 else 'WEAK_ONLY' if q[weak]>1e-9 else 'STRONG_ONLY'
    delta=q[weak]-cash
    sign='GAIN' if delta>1e-9 else 'SPEND' if delta<-1e-9 else 'UNCHANGED'
    return token,token+'_'+sign


def main():
    old_package=ROOT/'.lan_worker_v1/target_event_memory_20260912_v1'
    old_manifest=read(old_package/'manifest.json')
    assert sha(old_package/'dataset.json')==old_manifest['files']['dataset.json']
    old=read(old_package/'dataset.json');by_market={};sources=[]
    for item in old['sources']:
        p=ROOT/item['path'];assert sha(p)==item['sha256'];s=read(p);by=collections.defaultdict(list);first={}
        for a in s['targetActions']:
            assert a['quote_type']=='BID' and a['role'] in ('MAKER','TAKER')
            by[a['event_ms']].append(a)
            key=(a['role'],a['side'],a['order_hash']);first[key]=min(first.get(key,a['event_ms']),a['event_ms'])
        by_market[s['market']['market_id']]=(by,first);sources.append(item)
    rows=[];previous={};counts=collections.Counter();mismatch=0
    for original in old['rows']:
        r=dict(original);by,first=by_market[r['market']];strong=r['strong_side'];weak='DOWN' if strong=='UP' else 'UP'
        assert r['book_received_ms']<r['anchor_ms']<r['next_fill_bucket']
        assert r['last_fill_bucket']+999==r['anchor_ms']
        token,cash_token=signature(by[r['last_fill_bucket']],strong)
        prev=previous.get(r['market']);continuous=bool(prev and prev['next_fill_bucket']==r['last_fill_bucket'])
        pt,pc=signature(by[prev['last_fill_bucket']],strong) if continuous else ('UNKNOWN','UNKNOWN')
        f=r['features'];legacy=('P' if f['last_had_payment'] else '')+('B' if f['last_had_birth'] else '') or 'D'
        oldpt=prev['legacy_token'] if continuous else 'UNKNOWN'
        r.update(legacy_token=legacy,legacy_prior=oldpt,side_token=token,side_prior=pt,cash_token=cash_token,cash_prior=pc)
        next_legs=by[r['next_fill_bucket']];joint,_=signature(next_legs,strong)
        yweak=int(any(a['side']==weak for a in next_legs));ystrong=int(any(a['side']==strong for a in next_legs))
        assert yweak==original['labels']['next_weak_payment']
        assert int(joint=='STRONG_ONLY')==original['labels']['next_clean_strong_add']==1-yweak
        mismatch+=int(ystrong!=1-yweak);counts[joint]+=1
        r['joint_label']=joint;r['side_labels']=dict(weak=yweak,strong=ystrong,both=int(joint=='BOTH'))
        r['next_all_parents_first_observed_here']=all(first[(a['role'],a['side'],a['order_hash'])]==r['next_fill_bucket'] for a in next_legs)
        r['next_surplus_crossing']=math.fsum(a['shares'] for a in next_legs if a['side']==weak)>math.fsum(a['shares'] for a in next_legs if a['side']==strong)+f['net_fraction']*(1+math.expm1(f['gross_log']))+1e-7
        # Explicit feature names only; current/next observed labels never form context keys.
        rows.append(r);previous[r['market']]=r
    assert len(rows)==len(old['rows'])==1097
    ret=R/'lan_worker_returns/target-event-memory-20260912-v1';prior=read(ret/'result.json');assert prior['status']=='COMPLETE'
    assert sha(ret/'predictions.json')==prior['predictions_sha256']
    expected=[p for p in read(ret/'predictions.json') if p['model']=='EVENT2' and p['label']=='next_weak_payment']
    assert not PACKAGE.exists(),'immutable package exists'
    PACKAGE.mkdir()
    (PACKAGE/'dataset.json').write_text(json.dumps(dict(rows=rows,sources=sources),indent=2)+'\n',encoding='utf-8')
    (PACKAGE/'legacy_expected.json').write_text(json.dumps(expected,indent=2)+'\n',encoding='utf-8')
    shutil.copy2(ROOT/'tools/run_btc5m_concurrent_flow_probe_v1.py',PACKAGE/'worker.py')
    manifest=dict(version=STEM,rows=1097,joint_classes=['WEAK_ONLY','STRONG_ONLY','BOTH'],
        models=['INTERCEPT','LEGACY_EVENT2','SIDE_EVENT1','SIDE_EVENT2','SIDE_CASH_EVENT2'],
        smoothing='One count per joint class; unseen context uses train-only joint global distribution.',
        hypotheses=['Explicit physical-side context may retain information that FIFO birth/payment tokens conflate.',
            'Past weak-branch cash gain/spend may add predictive information beyond physical-side flow history.'],
        comparisons=[['SIDE_EVENT2','INTERCEPT'],['SIDE_EVENT2','LEGACY_EVENT2'],['SIDE_EVENT2','SIDE_EVENT1'],['SIDE_CASH_EVENT2','SIDE_EVENT2']],
        gate='At least6/8 LOMO joint log-loss gains, positive LOMO median, positive forward market-median gain, lower forward pooled joint log loss, no worse forward weak/strong/both marginal Brier.',
        folds='Eight LOMO plus original TRAIN2022527/2022538 to following consumed six markets. No row random split.',
        prereq='Legacy binary weak EVENT2 predictions must exactly match saved old predictions before new joint fits.',
        labels='Conditioned on next observed fill bucket. BOTH is joint outcome, not mutually exclusive objectives. No unobserved HOLD label.',
        history='Previous two observed buckets recoded relative to the current anchor surplus side; cash sign uses past filled qty minus all past-bucket spent cash for the anchor weak payoff.',
        limits=['No original Target orders/pending/intent or decision-time reconstruction. First observed is not new submission.',
            'All1097 old anchors retained; crossing and first-observed-parent sensitivities are outcomes only, never context features.',
            'Book clocks preserved, but these fixed table models do not use prices or claim public price effects.',
            'Correct joint label coverage is necessary representation, not evidence of predictive gain or a private controller.',
            'Small consumed-sample predictive probe; not a runtime selector, capital grant, HFT or economic promotion.'],
        source_dataset_sha256=sha(old_package/'dataset.json'),legacy_result_sha256=sha(ret/'result.json'),
        files={p.name:sha(p) for p in PACKAGE.iterdir()})
    for p in (PACKAGE/'manifest.json',R/(STEM+'_PREREGISTERED.json')):p.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    audit=dict(status='COMPLETE',rows=len(rows),counts=dict(counts),legacy_complement_misses_concurrent_strong=mismatch,
        selected_counts=dict(collections.Counter(r['joint_label'] for r in rows if r['market']==2026085)),
        first_observed_only_counts=dict(collections.Counter(r['joint_label'] for r in rows if r['next_all_parents_first_observed_here'])),
        expected_legacy_predictions=len(expected),source_hashes_verified=True)
    (R/(STEM+'_LABEL_AUDIT.json')).write_text(json.dumps(audit,indent=2)+'\n',encoding='utf-8')
    wave=dict(progress_artifact='data/research/CONCURRENT_FLOW_PROBE_PROGRESS_20260913.json',jobs=[dict(
        job_id='concurrent-flow-probe-20260913-v1',argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{PACKAGE.name}/worker.py'],
        cwd='.',max_threads=4,min_free_ram_gb=6,max_start_cpu_pct=90,required_artifact='result.json')])
    (R/'concurrent_flow_probe_wave_20260913.json').write_text(json.dumps(wave,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(audit))


if __name__=='__main__':main()
