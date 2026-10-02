from pathlib import Path
import argparse,json,re
ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--chunk',type=int,required=True);ap.add_argument('--chunks',type=int,required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
ids=[int(x) for x in Path(a.ids).read_text(encoding='utf-8').split() if x.strip()]; ids_set=set(ids); hits={i:[] for i in ids}
roots=[Path('data/research'),Path('tools')]
exts={'.json','.md','.csv','.log','.txt','.jsonl','.tsv','.yaml','.yml','.py','.ps1'}
files=[]
for root in roots:
    files += [p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in exts]
files=sorted(files,key=lambda p:str(p).lower()); selected=[p for j,p in enumerate(files) if j%a.chunks==a.chunk]
pat=re.compile(r'(?<!\\d)('+'|'.join(map(str,ids))+r')(?!\\d)')
errors=[]
for p in selected:
    try:
        with p.open('r',encoding='utf-8',errors='ignore') as f:
            for ln,line in enumerate(f,1):
                for m in pat.finditer(line):
                    i=int(m.group(1));
                    if len(hits[i])<5: hits[i].append({'path':str(p),'line':ln})
    except Exception as e: errors.append({'path':str(p),'error':repr(e)})
out={'version':'R4_V38_7_TEXT_CHUNK_V1','chunk':a.chunk,'chunks':a.chunks,'fileCountAll':len(files),'fileCountScanned':len(selected),'errors':errors,'hits':{str(i):v for i,v in hits.items() if v}}
Path(a.out).parent.mkdir(parents=True,exist_ok=True);Path(a.out).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'chunk':a.chunk,'files':len(selected),'hitIds':sum(bool(v) for v in hits.values()),'errors':len(errors)}))
