"""Read-only completion audit over the six already collected V45 jobs."""
import ast
import re

import btc5m_frozen_new_market_panel_v1 as d


def main():
    result=d.read(d.R/(d.STEM+'_RESULT.json'))
    progress=d.read(d.R/(d.STEM+'_PROGRESS.json'))
    protocol=d.read(d.R/(d.STEM+'_PROTOCOL.json'))
    assert result['status']=='COMPLETE' and result['execution']=='PASS'
    assert progress==dict(status='COMPLETE',submissions=6,native_completed=6,pending_jobs=[])
    jobs=d.jobs();assert len(jobs)==len({j['job_id'] for j in jobs})==len(result['results'])==6
    parent_hashes={};package_hashes={}
    for v,p in d.BASES.items():
        assert d.sha(p/'manifest.json')==protocol['parent_manifest_sha256'][v]
        parent=d.read(p/'manifest.json');assert all(d.sha(p/k)==h for k,h in parent['files'].items())
        child=d.PACKAGES[v];m=d.read(child/'manifest.json')
        assert all(d.sha(child/k)==h for k,h in m['files'].items())
        assert [k for k,h in parent['files'].items() if m['files'][k]!=h]==(['renewed_work.py'] if v=='V43' else [])
        assert d.sha(child/'manifest.json')==d.read(d.R/(d.STEM+'_'+v+'_COMPONENT.json'))['manifest_sha256']
        parent_hashes[v]=d.sha(p/'manifest.json');package_hashes[v]=d.sha(child/'manifest.json')
    pins=[];seconds=0;frame_counts=[];artifact_hashes={}
    for j,row in zip(jobs,result['results']):
        assert row['job_id']==j['job_id'];w=d.worker(j['version'],j)
        artifacts={name:d.read(w.artifact(j,name)) for name in ('SUBMIT','COLLECT','POSTCHECK','AUDIT','CONTINUATION_AUDIT','MECHANISM_AUDIT')}
        assert artifacts['SUBMIT']['accepted'] and artifacts['SUBMIT']['job_id']==j['job_id']
        assert artifacts['COLLECT']['status']['state']=='succeeded'
        assert artifacts['POSTCHECK']['status']=='PASS' and artifacts['POSTCHECK']['shared_files_unchanged']
        assert artifacts['AUDIT']['execution_status']=='PASS'
        assert artifacts['CONTINUATION_AUDIT']['status']=='PASS' and artifacts['MECHANISM_AUDIT']['status']=='PASS'
        assert row['all_owners_terminal'] and row['all_pending_zero'] and not row['missing_terminal_clocks']
        assert row['mechanism']['adaptive_decisions']==row['mechanism']['renewed_services']==0
        t=row['terminal'];assert abs(t['inventory_up']-t['cost']-t['up'])<1e-7
        assert abs(t['inventory_down']-t['cost']-t['down'])<1e-7
        assert abs(sum(x['flow'][s]['qty'] for x in row['flow_windows'] for s in ('UP','DOWN'))-t['inventory_up']-t['inventory_down'])<1e-7
        assert abs(sum(x['flow'][s]['cash'] for x in row['flow_windows'] for s in ('UP','DOWN'))-t['cost'])<1e-7
        hashes=result['source_hashes'][j['job_id']];folder=d.R/'lan_worker_returns'/j['job_id']
        assert hashes['result']==d.sha(folder/'result.json') and hashes['trace']==d.sha(folder/'clock_trace.json.gz')
        assert hashes['audit']==d.sha(w.artifact(j,'AUDIT')) and hashes['mechanism']==d.sha(w.artifact(j,'MECHANISM_AUDIT'))
        post=artifacts['POSTCHECK'];pins.append(dict(native=post['native_sha256'],private=post['private_files']))
        seconds+=row['native_seconds'];frame_counts.append(row['frames'])
        artifact_hashes[j['job_id']]={k:d.sha(w.artifact(j,k)) for k in artifacts}
    assert all(p==pins[0] for p in pins)
    assert abs(seconds-result['native_seconds'])<1e-9
    assert not post['other_processes'] and not any(r['pid_exists'] for r in post['nonterminal'])
    pairs=result['paired_tail_effect'];assert all(p['pre_intervention_states_exact'] and p['first_tail_difference_seconds']>=200 for p in pairs)
    assert all(pairs[1]['known_vs_no_direction_action_equality'].values())
    assert not any(pairs[0]['known_vs_no_direction_action_equality'].values())
    opening=d.read(d.R/(d.opening.STEM+'_RESULT.json'));validation=d.read(d.R/(d.opening.STEM+'_VALIDATION.json'))
    assert validation['status']=='PASS' and validation['markets']==83 and validation['latest80']==80
    assert opening['raw_sha256']==d.sha(d.R/(d.opening.STEM+'_RAW.json.gz'))
    assert opening['protocol_sha256']==d.sha(d.R/(d.opening.STEM+'_PROTOCOL.json'))
    assert result['opening_study_sha256']==d.sha(d.R/(d.opening.STEM+'_RESULT.json'))
    docs=['BTC5M_MICROWORLD_CORE_LOOP_HANDOFF_V45_20260914.md',d.opening.STEM+'.md']
    doc_hashes={};checked_links=0
    for name in docs:
        p=d.R/name;body=p.read_text(encoding='utf-8');doc_hashes[name]=d.sha(p)
        for target in re.findall(r'\]\(([^)]+)\)',body):
            if target==d.STEM+'_FINAL_VALIDATION.json':continue
            assert (p.parent/target).is_file(),target
            checked_links+=1
    for name in ('BTC5M_MICROWORLD_CORE_LOOP_FULL_HANDOFF_V1_20260912.md','BTC5M_MICROWORLD_CORE_LOOP_HANDOFF_V44_20260913.md'):
        assert 'BTC5M_MICROWORLD_CORE_LOOP_HANDOFF_V45_20260914.md' in (d.R/name).read_text(encoding='utf-8').splitlines()[0]
    tool_hashes={}
    for name in ('btc5m_opening_passive_direction_v1.py','btc5m_frozen_new_market_panel_v1.py','verify_btc5m_frozen_panel_mechanisms_v1.py','report_btc5m_frozen_new_market_panel_v1.py','verify_btc5m_frozen_panel_completion_v1.py'):
        p=d.ROOT/'tools'/name;ast.parse(p.read_text(encoding='utf-8'));tool_hashes[name]=d.sha(p)
    out=dict(status='PASS',native_jobs=6,native_seconds=seconds,frames=frame_counts,pending_jobs=[],all_accounting_and_mechanisms='PASS',
        parent_manifest_hashes=parent_hashes,package_manifest_hashes=package_hashes,shared_worker_pins=pins[0],
        terminal_owner_clocks_missing=0,drain_clock='NOT_PERSISTED_BY_FROZEN_RUNNER',
        final_worker_actual_processes=0,historical_dead_pid_records=len(post['nonterminal']),
        paired_pre_intervention_exact=True,second_market_known_and_no_direction_actions_identical=True,
        opening_source_and_conservation=validation,artifact_hashes=artifact_hashes,report_sha256=d.sha(d.R/(d.STEM+'_RESULT.json')),
        document_hashes=doc_hashes,checked_local_links=checked_links,tool_hashes=tool_hashes,
        research_claim='No core-loop graduation, direction predictor, adaptive-slippage proof or representative win rate',
        model_fits=0,parameter_search=0,local_native_jobs=0,plots=0)
    d.dump('FINAL_VALIDATION',out)
    print(dict(status='PASS',native_jobs=6,native_seconds=seconds,frames=sum(frame_counts),links=checked_links,pending_jobs=[]))


if __name__=='__main__':main()
