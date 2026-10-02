from __future__ import annotations
import json,lzma,re
from pathlib import Path
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
OUT=P/'r4_p0b_stage3_additive_admission_untouched_cohort_v1_preregistered.json'
TAIPEI=timezone(timedelta(hours=8))
N=160

def main():
    src={int(x.name.split('_',1)[0]):x for x in SRC.glob('*_r2_hft_closed_loop_v1.json.xz')}
    source_ids=set(src)
    used=set()
    # Conservative: any market id appearing in any existing p0 JSON before this prereg is treated as consumed.
    for fp in P.glob('*.json'):
        if fp==OUT: continue
        try: txt=fp.read_text(encoding='utf-8',errors='ignore')
        except Exception: continue
        for s in re.findall(r'(?<!\d)(\d{6,8})(?!\d)',txt):
            v=int(s)
            if v in source_ids: used.add(v)
    eligible=[]
    for mid in sorted(source_ids,reverse=True):
        if mid in used: continue
        try:
            with lzma.open(src[mid],'rt',encoding='utf-8') as f: d=json.load(f)
            ms=int((d.get('feed') or {}).get('firstReceivedMs') or 0)
        except Exception: continue
        if not ms: continue
        dt=datetime.fromtimestamp(ms/1000,TAIPEI)
        if dt.date().isoformat()=='2026-08-16': continue
        eligible.append({'marketId':mid,'firstReceivedMs':ms,'dateTaipei':dt.date().isoformat()})
        if len(eligible)>=N: break
    out={
      'version':'R4_P0B_STAGE3_ADDITIVE_ADMISSION_UNTOUCHED_COHORT_V1',
      'createdDate':'2026-08-29',
      'status':'PREREGISTERED_LABELS_UNOPENED',
      'researchOnly':True,'actionAuthority':False,
      'selection':'Newest 160 chronological R2_RESIDUAL realistic-HFT market files not referenced by any pre-existing p0_provenance_v1 JSON artifact; no trigger/outcome/role/branch screening.',
      'cohort':[x['marketId'] for x in eligible],
      'metadata':eligible,
      'usedMarketIdsExcludedCount':len(used),
      'guards':['No 2026-08-16 SEALED','No outcome screening','No branch economics inspected','Market-disjoint from all existing p0 JSON references at freeze time','Second computer forbidden unless user explicitly reauthorizes'],
      'next':'Run strict-past candidate discovery only; freeze one-shot validation gate before opening branch-economics labels.'
    }
    OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)),'n':len(eligible),'first10':out['cohort'][:10],'last10':out['cohort'][-10:],'usedExcluded':len(used)},indent=2))
if __name__=='__main__':main()
