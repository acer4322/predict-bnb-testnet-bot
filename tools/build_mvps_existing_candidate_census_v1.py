from __future__ import annotations
import json, re
from pathlib import Path
ROOT=Path('data/research/r4_v0/p0_provenance_v1')
rows=[]
for p in ROOT.glob('*'):
    if not p.is_file() or p.suffix.lower() not in {'.json','.md'}: continue
    name=p.name.upper()
    if 'PREREG' in name or 'CONTRACT' in name or 'ENTRYPOINT' in name or 'TARGET_' in name: continue
    if not any(d in name for d in ('20260903','20260904','20260905','20260906','20260907','20260908')): continue
    try:
        txt=p.read_text(encoding='utf-8',errors='ignore')
    except Exception: continue
    if len(txt)>3_000_000: continue
    # evidence tags
    realistic=('realistic HFT' in txt or 'realistic-HFT' in txt or 'HftBacktest' in txt or 'dream fill' in txt.lower())
    # collect plausible metric snippets from JSON recursively
    if p.suffix.lower()=='.json':
        try: obj=json.loads(txt)
        except Exception: continue
        def walk(x,path=''):
            if isinstance(x,dict):
                keys={str(k).lower():k for k in x}
                pnl=None
                for lk in ('aggregatepnl','totalpnl','aggregatepnlusdt','pnl'):
                    if lk in keys and isinstance(x[keys[lk]],(int,float)): pnl=float(x[keys[lk]]); break
                wr=None
                for lk in ('winrate','winrateallmarkets','positiveRate'.lower()):
                    if lk in keys and isinstance(x[keys[lk]],(int,float)): wr=float(x[keys[lk]]); break
                n=None
                for lk in ('markets','marketcount','nmarkets','totalmarkets'):
                    if lk in keys and isinstance(x[keys[lk]],(int,float)): n=int(x[keys[lk]]); break
                tc=None
                if 'tradecoverage' in keys and isinstance(x[keys['tradecoverage']],(int,float)): tc=float(x[keys['tradecoverage']])
                safety=None
                for lk in ('allsafetyzero','safetypass','allcorrectnesspass'):
                    if lk in keys: safety=x[keys[lk]]; break
                if pnl is not None and pnl>0 and (n is None or n>=3):
                    rows.append({'file':str(p),'path':path or '$','pnl':pnl,'winRate':wr,'markets':n,'tradeCoverage':tc,'safety':safety,'realisticEvidence':realistic,'version':obj.get('version') if isinstance(obj,dict) else None})
                for k,v in x.items(): walk(v,f'{path}.{k}' if path else str(k))
            elif isinstance(x,list):
                for i,v in enumerate(x): walk(v,f'{path}[{i}]')
        walk(obj)
    else:
        for m in re.finditer(r'(?:total|aggregate)\s*PnL\s*[:=]\s*\+?(-?\d+(?:\.\d+)?)',txt,re.I):
            pnl=float(m.group(1))
            if pnl>0: rows.append({'file':str(p),'path':'md_regex','pnl':pnl,'winRate':None,'markets':None,'tradeCoverage':None,'safety':None,'realisticEvidence':realistic,'version':None})
# dedupe file/path/pnl and rank, while limiting duplicates per file
seen=set();out=[];per={}
for r in sorted(rows,key=lambda x:(x['realisticEvidence'], x['markets'] or 0, x['pnl']),reverse=True):
    key=(r['file'],r['path'],round(r['pnl'],12))
    if key in seen: continue
    if per.get(r['file'],0)>=4: continue
    seen.add(key);per[r['file']]=per.get(r['file'],0)+1;out.append(r)
res={'version':'MVPS_EXISTING_CANDIDATE_CENSUS_V1_20260908','researchOnly':True,'rows':out[:80],'count':len(out[:80]),'boundary':['existing artifacts only','positive numeric paths are candidate leads, not automatic comparable strategies','target/preregistered/contract files excluded by filename','must manually verify current-substrate comparability and realistic-HFT provenance before reuse']}
opath=ROOT/'MVPS_EXISTING_CANDIDATE_CENSUS_V1_20260908.json';opath.write_text(json.dumps(res,indent=2),encoding='utf-8')
print(json.dumps({'ok':True,'count':len(out),'top':out[:25]},ensure_ascii=False,indent=2))
