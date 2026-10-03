"""Resume diagnosed collection failure, without any native submission/replay."""
import json
import os
import time
from supervise_eth_once import P, JOB, PACKAGE, BTC, save, emit
import supervise_eth_once as supervisor


def main():
    old = json.loads((P / 'ETH_SUPERVISION_STATE.json').read_bytes())
    assert old['state'] == 'STOPPED_FOR_INSPECTION' and '01_archive_collect exited 1' in old['error']
    intent = P / 'ETH_POSTPROCESS_RECOVERY_V3_INTENT.json'
    assert not intent.exists()
    intent.write_text(json.dumps(dict(pid=os.getpid(), attempts=1, started=time.time(),
        prior_state=old, scope='collection and postprocessing only', native_submissions=0), indent=2))
    supervisor.LOG = P / 'eth_postprocess_recovery_v3'
    supervisor.LOG.mkdir(exist_ok=False)
    supervisor.step('01_archive_collect', ['continue_eth_archive_transfer.py'])
    supervisor.step('02_hashes', ['verify_collections.py', *BTC, JOB])
    supervisor.step('03_analyze', ['analyze_full.py', PACKAGE])
    report = json.loads((P / (PACKAGE + '_ANALYSIS.json')).read_bytes())
    assert report['observed'] == report['expected_paths'] == 2583 and not report['missing']
    supervisor.step('04_packets', ['export_packets.py', PACKAGE])
    packets = json.loads((P / 'ETH_PACKETS.json').read_bytes())
    published = []
    for i, packet in enumerate(packets):
        supervisor.step(f'05_publish_{i:02d}', ['publish_one.py', packet['id']])
        published.append(json.loads((P / (packet['id'] + '_PUBLISHED.json')).read_bytes()))
    # Read-only independent audit must be present before final lineage publication.
    waiting = dict(job_id=JOB, phase='WAIT_ETH_EXECUTION_AUDIT', state='POSTPROCESS_RUNNING')
    while not (P / 'ETH_FINAL_EXECUTION_AUDIT.json').exists():
        save(waiting)
        emit(waiting)
        time.sleep(45)
    supervisor.step('06_lineage', ['export_lineage.py', 'ALL'])
    lineage = json.loads((P / 'ALL_LINEAGE_PACKET.json').read_bytes())
    supervisor.step('07_publish_lineage', ['publish_one.py', lineage['id']])
    published.append(json.loads((P / (lineage['id'] + '_PUBLISHED.json')).read_bytes()))
    result = dict(job_id=JOB, phase='FINISHED', state='COLLECTED_AUDITED_PUBLISHED',
        collection_recovery=True, status_counts=report['status_counts'], summary=report['summary'],
        published=published, fits=0, live_changes=0, shadow_test_started=False)
    (P / 'ETH_SUPERVISION_RESULT.json').write_text(json.dumps(result, indent=2))
    save(result)
    emit(dict(job_id=JOB, state=result['state'], status_counts=result['status_counts'], commits=[r['commit'] for r in published]))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        state = dict(job_id=JOB, state='STOPPED_FOR_INSPECTION', error=repr(exc), native_submissions=0, no_automatic_retry=True)
        save(state)
        emit(state)
        raise
