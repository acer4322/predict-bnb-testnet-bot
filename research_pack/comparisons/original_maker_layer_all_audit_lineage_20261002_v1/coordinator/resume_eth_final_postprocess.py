"""Resume read-only hash verification; collection/native execution are complete."""
import json
import os
import time
import supervise_eth_once as s

P, JOB, PACKAGE = s.P, s.JOB, s.PACKAGE


def main():
    old = json.loads((P / 'ETH_SUPERVISION_STATE.json').read_bytes())
    assert old['state'] == 'STOPPED_FOR_INSPECTION' and '02_hashes exited 1' in old['error']
    collection = json.loads((P / 'ETH_COLLECTION_RECOVERY_RESULT.json').read_bytes())
    assert collection['status'] == 'COLLECTED_HASH_VERIFIED' and collection['files'] == 56756
    intent = P / 'ETH_FINAL_POSTPROCESS_INTENT.json'
    assert not intent.exists()
    intent.write_text(json.dumps(dict(pid=os.getpid(), attempts=1, started=time.time(),
        prior_state=old, scope='Hashing, audit and publication only', collection_repeated=False,
        native_submissions=0, native_replays=0), indent=2))
    s.LOG = P / 'eth_final_postprocess'
    s.LOG.mkdir(exist_ok=False)
    s.step('02_hashes', ['verify_collections_resilient.py', *s.BTC, JOB])
    s.step('03_analyze', ['analyze_full.py', PACKAGE])
    report = json.loads((P / (PACKAGE + '_ANALYSIS.json')).read_bytes())
    assert report['observed'] == report['expected_paths'] == 2583 and not report['missing']
    s.step('04_packets', ['export_packets.py', PACKAGE])
    packets = json.loads((P / 'ETH_PACKETS.json').read_bytes())
    published = []
    for i, packet in enumerate(packets):
        s.step(f'05_publish_{i:02d}', ['publish_one.py', packet['id']])
        published.append(json.loads((P / (packet['id'] + '_PUBLISHED.json')).read_bytes()))
    waiting = dict(job_id=JOB, phase='WAIT_ETH_EXECUTION_AUDIT', state='POSTPROCESS_RUNNING')
    while not (P / 'ETH_FINAL_EXECUTION_AUDIT.json').exists():
        s.save(waiting)
        s.emit(waiting)
        time.sleep(45)
    s.step('06_lineage', ['export_lineage.py', 'ALL'])
    lineage = json.loads((P / 'ALL_LINEAGE_PACKET.json').read_bytes())
    s.step('07_publish_lineage', ['publish_one.py', lineage['id']])
    published.append(json.loads((P / (lineage['id'] + '_PUBLISHED.json')).read_bytes()))
    result = dict(job_id=JOB, phase='FINISHED', state='COLLECTED_AUDITED_PUBLISHED',
        collection_recovery=True, status_counts=report['status_counts'], summary=report['summary'],
        published=published, fits=0, live_changes=0, shadow_test_started=False)
    (P / 'ETH_SUPERVISION_RESULT.json').write_text(json.dumps(result, indent=2))
    s.save(result)
    s.emit(dict(job_id=JOB, state=result['state'], status_counts=result['status_counts'], commits=[r['commit'] for r in published]))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        state = dict(job_id=JOB, state='STOPPED_FOR_INSPECTION', error=repr(exc), native_submissions=0, no_automatic_retry=True)
        s.save(state)
        s.emit(state)
        raise
