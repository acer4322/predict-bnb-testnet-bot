"""Freeze ONLY the isolated Playground's source copies and seven consumed roots."""
from pathlib import Path
import hashlib,json,shutil
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
SOURCE=ROOT/'data/research/v49_incremental_cycle_value_micro_20260921_r78'
DEST=ROOT/'data/research/strategy_playground_v0_20260921'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    names=['base_kernel.py','r58_kernel.py','r59_kernel.py','r60_kernel.py','kernel.py','grid_encoding.py']
    for f in names:
        if sha(SOURCE/f)!=sha(HERE/'vendor'/f):raise ValueError('Source parity failed: '+f)
    DEST.mkdir(parents=True,exist_ok=True)
    if not (DEST/'ROOTS.json').exists():shutil.copyfile(SOURCE/'ROOTS.json',DEST/'ROOTS.json')
    if sha(DEST/'ROOTS.json')!=sha(SOURCE/'ROOTS.json'):raise ValueError('Existing roots differ; preserve and stop')
    pins={'source':str(SOURCE.relative_to(ROOT)),'files':{f:sha(HERE/'vendor'/f) for f in names},'roots_sha256':sha(DEST/'ROOTS.json')}
    p=HERE/'VENDOR_PINS.json'
    if p.exists():
        if json.loads(p.read_text(encoding='utf-8'))!=pins:raise ValueError('Existing pin differs; preserve and stop')
    else:p.write_text(json.dumps(pins,indent=2),encoding='utf-8')
    result={'status':'PREPARED','roots':len(json.loads((DEST/'ROOTS.json').read_text(encoding='utf-8'))['roots']),
            'vendor_files':len(names),'live_changes':0,'native_hft_runs':0,'mainline_pointer_changed':False}
    print(json.dumps(result))
if __name__=='__main__':main()
