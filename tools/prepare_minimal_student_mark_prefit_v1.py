"""Host: pin/copy small code and metadata only. All data loading/fits run on worker."""
from pathlib import Path
import hashlib
import json
import py_compile
import shutil

ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research/r4_v0/p0_provenance_v1'
OUT=ROOT/'.lan_worker_v1/minimal_student_mark_prefit_20260910_v1'
D=ROOT/'data/research/lan_worker_returns/minimal-student-training-data-smoke3-20260910-v1'
S=ROOT/'data/research/lan_worker_returns/minimal-student-training-data-support-20260910-v1'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    assert not OUT.exists(),'immutable package already exists'
    files={
        'minimal_student_mark_logistic_v1.py':ROOT/'tools/minimal_student_mark_logistic_v1.py',
        'run_minimal_student_mark_prefit_worker_v1.py':ROOT/'tools/run_minimal_student_mark_prefit_worker_v1.py',
        'PREREG.md':R/'MINIMAL_STUDENT_OBSERVED_MARK_PREFIT_PREREG_V1_20260910.md',
        'ACCEPTANCE.json':R/'MINIMAL_STUDENT_TRAINING_DATA_ACCEPTANCE_V1_20260910.json',
        'BUILD_RESULT.json':D/'COMPACT.json',
        'TASK_SUPPORT.json':S/'COMPACT.json'}
    for rel,p in files.items():
        assert p.stat().st_size<40000,rel
        if p.suffix=='.py':py_compile.compile(str(p),doraise=True)
    assert sha(D/'DATASET_MANIFEST.json')=='3ef1638c642481ce0c390797fdb8ae271b4b1e466d5d7d00c61dfa095fc9ffb0'
    assert sha(D/'COMPACT.json')=='474a36c9c684fc9fd2df038657beeb769655c27b552e8b2513b95a7f452ad18a'
    assert sha(S/'COMPACT.json')=='8bf8e373ce822290036fdb61c7eb113754f8cdb58e47b46e97ad5dbcf169535d'
    OUT.mkdir(parents=True)
    for rel,p in files.items():shutil.copy2(p,OUT/rel)
    meta={rel:dict(bytes=(OUT/rel).stat().st_size,sha256=sha(OUT/rel)) for rel in files}
    (OUT/'MANIFEST.json').write_text(json.dumps(dict(version='MINIMAL_STUDENT_MARK_PREFIT_V1',files=meta,
        max_threads=4,worker_only=True,train_markets=[2022527,2022538],pipeline_check=[2022602],
        max_research_fits=8,max_seconds=180,HFT=0,live_changes=0),indent=2),encoding='utf-8')
    print(json.dumps(dict(package=OUT.relative_to(ROOT).as_posix(),bytes=sum(x['bytes'] for x in meta.values()),
                         manifest_sha256=sha(OUT/'MANIFEST.json'))))


if __name__=='__main__':main()
