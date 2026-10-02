"""Main host packaging only: hash/copy known small artifacts; no dataset processing."""
from pathlib import Path
import hashlib
import json
import py_compile
import shutil
import time

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.lan_worker_v1/minimal_student_training_data_smoke3_20260910_v1'
R=ROOT/'data/research/r4_v0/p0_provenance_v1'
SOURCE=ROOT/'.lan_worker_v1/minimal_student_preflight_smoke3_20260910_v1'
MIDS=[2022527,2022538,2022602]


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()


def main():
    t=time.monotonic();assert not OUT.exists(),'immutable staging package exists'
    ap=R/'MINIMAL_STUDENT_QUANTITY_SEAM_ACCEPTANCE_V1_20260910.json'
    assert ap.stat().st_size<20000
    a=json.loads(ap.read_text(encoding='utf-8'))
    assert a['completedMarkets']==3 and a['ownDecisionRecords']==4458
    assert [x['marketId'] for x in a['rows']]==MIDS
    assert all(x['unresolvedCount']==0 for x in a['rows'])
    p=SOURCE/'MANIFEST.json';assert p.stat().st_size<20000
    m=json.loads(p.read_text(encoding='utf-8'))
    files={'inputs/ACCEPTANCE.json':ap,'inputs/SOURCE_MANIFEST.json':p,
        'build_minimal_student_training_data_smoke3_worker_v1.py':ROOT/'tools/build_minimal_student_training_data_smoke3_worker_v1.py',
        'minimal_student_dataset_reader_v1.py':ROOT/'tools/minimal_student_dataset_reader_v1.py',
        'PREREG.md':R/'MINIMAL_STUDENT_TRAINING_DATA_SMOKE3_PREREG_V1_20260910.md'}
    for r in a['rows']:
        mid=r['marketId'];trace=ROOT/r['tracePath']
        assert trace.stat().st_size<1024**2 and sha(trace)==r['traceSha256']
        if mid==2022602:assert 'trace-retry' in trace.as_posix()
        files[f'inputs/OWN_TRAJECTORY_{mid}.jsonl.gz']=trace
        ip=SOURCE/f'input_{mid}.json.gz'
        assert ip.stat().st_size==m['files'][ip.name]['bytes'] and sha(ip)==m['files'][ip.name]['sha256']
        files[f'inputs/{ip.name}']=ip
    for row,name in zip(a['resultSources'],['NATIVE_V1_COMPACT.json','NATIVE_RETRY_COMPACT.json']):
        p=ROOT/row['path'];assert p.stat().st_size<30000 and sha(p)==row['sha256']
        files['inputs/'+name]=p
    for rel,p in files.items():
        assert p.stat().st_size<=1024**2
        if p.suffix=='.py':py_compile.compile(str(p),doraise=True)
    OUT.mkdir(parents=True)
    for rel,p in files.items():
        dst=OUT/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst)
    metadata={rel:dict(bytes=(OUT/rel).stat().st_size,sha256=sha(OUT/rel)) for rel in files}
    mm=dict(version='MINIMAL_STUDENT_TRAINING_DATA_SMOKE3_V1',files=metadata,
        markets=MIDS,split={'TRAIN':MIDS[:2],'PIPELINE_CHECK':MIDS[2:]},
        processing_location='SECOND_LAN_WORKER',model_fits=0,HFT=0,new_markets=0,
        main_host_work='hash/copy/syntax only; no joins/decoding/training',
        preparation_seconds=time.monotonic()-t)
    (OUT/'MANIFEST.json').write_text(json.dumps(mm,indent=2),encoding='utf-8')
    print(json.dumps(dict(package=OUT.relative_to(ROOT).as_posix(),files=len(metadata),
        bytes=sum(x['bytes'] for x in metadata.values()),manifest_sha256=sha(OUT/'MANIFEST.json'),
        preparation_seconds=mm['preparation_seconds'])))


if __name__=='__main__':main()
