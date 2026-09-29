from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]; p=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r21_echtgeld_environment_curriculum_v1_dataset.json'
d=json.loads(p.read_text(encoding='utf-8')); d['minimumNotionalUsdt']=1.0; d['minimumNotionalRule']='1.0 USDT authoritative minimum; below-minimum requests are rejected with no inventory change and no silent upsize.'; d['correction']='Any prior 1.5-USDT assumption is superseded for this Echtgeld curriculum.'; p.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'rows':len(d.get('rows',[])),'minimumNotionalUsdt':d['minimumNotionalUsdt']},ensure_ascii=False))
