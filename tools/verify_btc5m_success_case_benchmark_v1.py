"""Final V39 artifact/source/accounting consistency; no replay or fitting."""
import collections

from btc5m_success_case_benchmark_v1 import ROOT,R,STEM,BASE,PACKAGE,SOURCE,read,sha,dump,jobs,worker,same


def main():
    m=read(PACKAGE/'manifest.json');base=read(BASE/'manifest.json')
    assert all(sha(PACKAGE/n)==h for n,h in m['files'].items())
    assert all(sha(BASE/n)==h==m['files'][n] for n,h in base['files'].items())
    assert len([n for n in base['files'] if n.endswith('.py')])==20
    r=read(R/(STEM+'_RESULT.json'));progress=read(R/(STEM+'_PROGRESS.json'))
    assert r['status']=='COMPLETE' and r['verification']=='PASS' and not r['pending_jobs']
    assert progress['status']=='COMPLETE' and progress['native_submissions']==progress['native_jobs_completed']==4
    assert r['learning_status']=='TARGET_CORE_LOOP_NOT_REPRODUCED_ON_SELECTED_SUCCESS_CASES'
    assert r['model_fits']==r['parameter_search']==r['policy_changes']==r['local_native_jobs']==r['figures']==0
    w=worker();frames=0;raw_receipts=0;news=0;missing=[];counts=collections.Counter();elapsed=0.;evidence={}
    for j in jobs():
        sub=read(w.artifact(j,'SUBMIT'));collect=read(w.artifact(j,'COLLECT'));post=read(w.artifact(j,'POSTCHECK'))
        au=read(w.artifact(j,'AUDIT'));ca=read(w.artifact(j,'CONTINUATION_AUDIT'));model=r['completed'][j['job_id']]
        assert sub['accepted'] and sub['job_id']==j['job_id'] and collect['status']['state']=='succeeded'
        assert au['execution_status']=='PASS' and ca['status']==post['status']=='PASS'
        assert post['native_sha256']==m['native_sha256'] and post['shared_files_unchanged']
        assert not any(x['pid_exists'] for x in post['nonterminal']) and not post['other_processes']
        assert au['all_owners_terminal'] and au['all_pending_zero'] and au['frame_coverage']['source_prefix_exact']
        assert au['frame_coverage']['closure_has_no_new']
        for k in ('up','down','cost','loss_to_gain'):same(model['summary']['terminal'][k],au['terminal'][k])
        same(model['summary']['negative_floor_area'],au['trajectory']['negative_floor_area_currency_seconds'])
        same(model['summary']['both_positive_seconds'],au['trajectory']['both_positive_seconds'])
        assert model['result_sha256']==au['result_sha256'] and model['trace_sha256']==au['trace_sha256']
        counts.update(ca['rows']);frames+=au['frames'];raw_receipts+=au['raw_receipts'];news+=au['submits'];elapsed+=au['native_elapsed_seconds']
        missing.extend(dict(job=j['job_id'],key=k) for k in model['missing_terminal_clocks'])
        evidence[j['job_id']]={tag:sha(w.artifact(j,tag)) for tag in ('SUBMIT','COLLECT','POSTCHECK','AUDIT','CONTINUATION_AUDIT')}
    same(elapsed,r['native_total_seconds']);assert frames==2*(1245+1340)
    assert all(r['paired_path_equality']['1942969'].values()) and not any(r['paired_path_equality']['1977248'].values())
    assert r['targets']['1977248']['sizing']['maker_qty_below_floor']==415.
    same(r['targets']['1977248']['sizing']['maker_cash_below_floor'],18.2)
    assert r['targets']['1942969']['sizing']['maker_legs_below_fixed15_new_price_floor']==0
    assert r['targets']['1942969']['summary']['both_positive_seconds']==196.
    assert len(r['targets']['1942969']['summary']['net_direction_flips'])==4
    protocol=read(R/(STEM+'_PROTOCOL.json'))
    assert protocol['source_quality']['2084104']['quality_status']=='INCOMPLETE_FORWARD'
    assert protocol['source_quality']['2084104']['max_source_gap_ms']==5350
    out=dict(status='PASS',native_jobs=4,single_submit_receipts=4,source_frames=frames,raw_receipts=raw_receipts,
        new_orders=news,continuation_decision_rows=dict(counts),native_seconds=elapsed,
        missing_exact_owner_terminal_clocks=missing,exact_drain_clock='NOT_PERSISTED_BY_FROZEN_RUNNER',
        all_canonical_owners_terminal=True,all_reservations_zero=True,all_policy_files_unchanged=20,
        all_inherited_manifest_files_unchanged=len(base['files']),native_binary_sha256=m['native_sha256'],
        input_package_manifest_sha256=sha(PACKAGE/'manifest.json'),source_bundle_manifest_sha256=sha(SOURCE/'manifest.json'),
        result_sha256=sha(R/(STEM+'_RESULT.json')),evidence=evidence,
        tools={n:sha(ROOT/'tools'/n) for n in ('btc5m_success_case_benchmark_v1.py','report_btc5m_success_case_benchmark_v1.py','verify_btc5m_success_case_benchmark_v1.py')},
        learning_status=r['learning_status'],old_failure_control_rerun=False,worker_idle_at_final_postcheck=True)
    dump('FINAL_VALIDATION',out);print(__import__('json').dumps(out))


if __name__=='__main__':main()
