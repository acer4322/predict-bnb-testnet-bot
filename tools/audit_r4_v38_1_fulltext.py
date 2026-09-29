from pathlib import Path
import re,json
root=Path('data/research/r4_v0')
ids=set(range(1818097,1818597)); hits={i:[] for i in ids}
pat=re.compile(r'(?<!\d)(1818\d{3})(?!\d)')
for p in root.rglob('*'):
    if not p.is_file() or p.suffix.lower() not in {'.json','.md','.csv','.log','.txt','.jsonl'}: continue
    try:
        with p.open('r',encoding='utf-8',errors='ignore') as f:
            for ln,line in enumerate(f,1):
                for m in pat.finditer(line):
                    i=int(m.group(1))
                    if i in hits and len(hits[i])<3: hits[i].append([str(p),ln])
    except Exception: pass
clean=[i for i,v in hits.items() if not v]
out={'version':'R4_V38_1_FULLTEXT_PROVENANCE_EXTENSION','range':[min(ids),max(ids)],'scannedRoot':str(root),'candidateNoTextHit':clean,'candidateCount':len(clean),'note':'Exact-token full-size text scan only; absence is not yet global pristine proof because binary/non-R4 artifacts and eligibility data availability still require audit.'}
Path('data/research/r4_v0/p0_provenance_v1/r4_v38_1_fulltext_provenance_extension.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps({'candidateCount':len(clean),'first20':clean[:20]}))
