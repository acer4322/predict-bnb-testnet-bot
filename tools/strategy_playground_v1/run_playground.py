"""Launch only the hash-pinned research actor through its existing native runner."""
from pathlib import Path
import argparse,hashlib,json,sys
P=Path(__file__).resolve().parent
ap=argparse.ArgumentParser(add_help=False)
ap.add_argument('--legacy-dir',required=True)
a,rest=ap.parse_known_args()
legacy=Path(a.legacy_dir)
man=json.loads((P/'MANIFEST.json').read_text(encoding='utf-8'))
for name,digest in man['legacy_files'].items():
    if hashlib.sha256((legacy/name).read_bytes()).hexdigest()!=digest:
        raise RuntimeError('Pinned native source changed: '+name)
sys.path.insert(0,str(legacy));sys.path.insert(0,str(P))
source=(legacy/'run_system.py').read_text(encoding='utf-8')
needle='from nn_bridge import NeuralOperator'
if source.count(needle)!=1:raise RuntimeError('Native operator import seam missing')
source=source.replace(needle,'from playground_operator import PlaygroundOperator as NeuralOperator')
sys.argv=[str(P/'run_playground.py')]+rest
# The existing runner already owns the permitted actor loader and receipt bridge.
# No user text is compiled here; only the pinned runner with one fixed import change.
exec(compile(source,str(P/'run_playground.py'),'exec'),{'__name__':'__main__','__file__':str(P/'run_playground.py')})
