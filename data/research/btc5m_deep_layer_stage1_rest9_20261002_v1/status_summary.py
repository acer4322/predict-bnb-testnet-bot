import json
from discover import t,JOB
code="""import pathlib,json,collections
p=pathlib.Path('C:/BTC5M-worker/.lan_worker_v1/results')/JOB
progress=json.loads((p/'PROGRESS.json').read_text()) if (p/'PROGRESS.json').exists() else {}
partial=json.loads((p/'PARTIAL.json').read_text()) if (p/'PARTIAL.json').exists() else {'paths':[]}
result=json.loads((p/'RESULT.json').read_text()) if (p/'RESULT.json').exists() else {}
rows=partial['paths']
print(json.dumps(dict(progress=progress,completed=len(rows),by_arm=dict(collections.Counter(r['arm'] for r in rows)),errors=[r for r in rows if r['status']!='PASS'],terminal=result.get('status'))))
""".replace('JOB',repr(JOB))
print(json.dumps(t.remote(code)))
