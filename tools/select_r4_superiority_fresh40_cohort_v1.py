from __future__ import annotations
import json,lzma,re,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=ROOT/'data/hft_forward_paper_v1/markets';TAPE=ROOT/'data/execution_tape_v1/markets'
OUT=P/'r4_superiority_fresh40_cohort_v1.json';IDS=P/'r4_superiority_fresh40_ids_v1.json'
SEALED={1700601,1700655,1701123,1701140,1701356,1701359,1701523,1701531}; TARGET=40

def used_ids_from_top_level_p0():
    u=set(); pat=re.compile(rb'(?<![0-9])([0-9]{7})(?![0-9])'); scanned=0; files=0
    for p in P.iterdir():
        if not p.is_file() or p in {OUT,IDS}: continue
        if p.suffix.lower() not in {'.json','.md','.txt','.csv','.py'}: continue
        try:b=p.read_bytes()
        except Exception:continue
        files+=1; scanned+=len(b)
        for m in pat.finditer(b):u.add(int(m.group(1)))
    return u,files,scanned

def has_settlement(mid,c):
    return bool(c.execute("select 1 from target_market_results where market_id=? and asset='BTC' limit 1",(mid,)).fetchone())

def main():
    if OUT.exists() or IDS.exists(): raise RuntimeError('cohort already frozen; refusing reselection')
    used,files_scanned,bytes_scanned=used_ids_from_top_level_p0(); con=sqlite3.connect(ROOT/'data/target_wallet_official_v1.db'); cand=[]; opened=0
    files=sorted(SRC.glob('*.json.xz'),key=lambda p:p.stat().st_mtime_ns,reverse=True)
    for p in files:
        try:
            with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f); opened+=1
        except Exception: continue
        mid=int(d.get('marketId') or 0); student=str(d.get('student') or '')
        if not mid or mid in used or mid in SEALED or 'R2_RESIDUAL' not in student or not (d.get('orderMeta') or {}): continue
        if not (TAPE/f'{mid}.json.xz').exists(): continue
        if not has_settlement(mid,con): continue
        cand.append(mid)
        if len(cand)>=TARGET: break
    con.close()
    if len(cand)!=TARGET: raise RuntimeError(f'only {len(cand)} eligible unseen markets')
    rep={'version':'R4_SUPERIORITY_FRESH40_COHORT_V1','researchOnly':True,'contract':'r4_superiority_fresh40_contract_v1.json','selectionBeforeScoring':True,'cohort':cand,'usedIdCount':len(used),'p0TopLevelFilesScanned':files_scanned,'p0TopLevelBytesScanned':bytes_scanned,'sourceFilesOpened':opened,'guards':['no outcome/trigger screening','2026-08-16 SEALED excluded','same cohort for all three comparators','no reselection']}
    OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); IDS.write_text(json.dumps(cand,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
