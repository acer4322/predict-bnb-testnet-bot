from __future__ import annotations
import json,lzma,re,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=ROOT/'data/hft_forward_paper_v1/markets';TAPE=ROOT/'data/execution_tape_v1/markets'
OUT=P/'r4_threeway10_benchmark_cohort_preregistered_v1.json';IDS=P/'r4_threeway10_benchmark_ids_v1.json'
SEALED={1700601,1700655,1701123,1701140,1701356,1701359,1701523,1701531}

def used_ids():
    u=set()
    pat=re.compile(rb'(?<!\d)(\d{7})(?!\d)')
    for p in P.rglob('*'):
        if not p.is_file() or p.suffix.lower() not in {'.json','.md','.txt','.csv','.py'}: continue
        try:
            b=p.read_bytes()
        except Exception: continue
        for m in pat.finditer(b):u.add(int(m.group(1)))
    return u

def has_settlement(mid:int,c:sqlite3.Connection)->bool:
    r=c.execute("select 1 from target_market_results where market_id=? and asset='BTC' limit 1",(mid,)).fetchone()
    return bool(r)

def main():
    used=used_ids(); con=sqlite3.connect(ROOT/'data/target_wallet_official_v1.db'); cand=[]
    files=sorted(SRC.glob('*.json.xz'),key=lambda p:p.stat().st_mtime_ns,reverse=True)
    opened=0
    for p in files:
        try:
            with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f);opened+=1
        except Exception:continue
        mid=int(d.get('marketId') or 0);student=str(d.get('student') or '')
        if not mid or mid in used or mid in SEALED or 'R2_RESIDUAL' not in student or not (d.get('orderMeta') or {}):continue
        if not (TAPE/f'{mid}.json.xz').exists():continue
        if not has_settlement(mid,con):continue
        cand.append(mid)
        if len(cand)>=10:break
    con.close()
    if len(cand)!=10:raise RuntimeError(f'only {len(cand)} eligible')
    rep={'version':'R4_THREEWAY10_BENCHMARK_COHORT_PREREGISTERED_V1','researchOnly':True,'selectionAfterContractFreeze':True,'contract':'r4_threeway10_action_enabled_benchmark_contract_v1.json','selection':'Next 10 newest eligible realistic-HFT R2_RESIDUAL markets absent as digit-bounded 7-digit IDs from all pre-existing P0 text artifacts; HFT tape and BTC settlement record existence required; winner direction/value not read for selection.','cohort':cand,'usedIdCount':len(used),'sourceFilesOpened':opened,'guards':['no outcome/trigger screening','2026-08-16 SEALED excluded','same cohort for all three comparators']}
    OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');IDS.write_text(json.dumps(cand),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
