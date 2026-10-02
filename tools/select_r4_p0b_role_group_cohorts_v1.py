from __future__ import annotations
import json,lzma,datetime,zoneinfo
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
P0=ROOT/'data/research/r4_v0/p0_provenance_v1'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
TAPE=ROOT/'data/execution_tape_v1/markets'
FROZEN={1700601,1700655,1701123,1701140,1701356,1701359,1701523,1701531}

def collect_used():
    used=set()
    def walk(x,key=None):
        if isinstance(x,dict):
            for k,v in x.items(): walk(v,k)
        elif isinstance(x,list):
            if key in {'cohort','ids','markets','marketIds'}:
                for v in x:
                    if isinstance(v,int) and 1_000_000<=v<=9_999_999: used.add(v)
            else:
                for v in x: walk(v,key)
        elif isinstance(x,int) and key in {'marketId','market_id'} and 1_000_000<=x<=9_999_999:
            used.add(x)
    for p in P0.glob('*.json'):
        try: walk(json.loads(p.read_text(encoding='utf-8')))
        except Exception: pass
    return used

def choose(n):
    used=collect_used(); out=[];seen=set()
    files=sorted(SRC.glob('*.json.xz'),key=lambda p:p.stat().st_mtime_ns,reverse=True)
    for p in files:
        try:
            with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
        except Exception: continue
        mid=int(d.get('marketId') or 0); student=str(d.get('student') or '')
        if not mid or mid in seen or mid in FROZEN or mid in used or 'R2_RESIDUAL' not in student or not (d.get('orderMeta') or {}): continue
        dms=[int(x.get('decisionMs') or 0) for x in (d.get('decisionRows') or []) if int(x.get('decisionMs') or 0)>0]
        if dms:
            day=datetime.datetime.fromtimestamp(min(dms)/1000,datetime.timezone.utc).astimezone(zoneinfo.ZoneInfo('Asia/Taipei')).date()
            if day==datetime.date(2026,8,16): continue
        if not (TAPE/f'{mid}.json.xz').exists(): continue
        seen.add(mid);out.append(mid)
        if len(out)>=n: break
    return sorted(used),out

if __name__=='__main__':
    used,ids=choose(240)
    rep={
      'version':'R4_P0B_ROLE_GROUP_COHORTS_V1',
      'researchOnly':True,
      'selection':'First 240 chronological R2_RESIDUAL realistic-HFT markets not structurally referenced by prior p0_provenance JSON artifacts; no trigger/outcome screening; all markets whose earliest decisionMs falls on 2026-08-16 Asia/Taipei are excluded, plus historical FROZEN IDs.',
      'usedMarketIdsExcludedCount':len(used),
      'development':ids[:120],
      'independentReplication':ids[120:240],
      'guards':['No 2026-08-16 SEALED IDs','No R3/8781/Echtgeld writes','No outcome or trigger screening','Cohorts frozen before role outcomes']
    }
    out=P0/'r4_p0b_role_group_cohorts_preregistered_v1.json'
    out.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(out.relative_to(ROOT)),'dev':len(rep['development']),'rep':len(rep['independentReplication']),'devFirstLast':[rep['development'][0],rep['development'][-1]] if rep['development'] else None,'repFirstLast':[rep['independentReplication'][0],rep['independentReplication'][-1]] if rep['independentReplication'] else None,'excludedUsed':len(used)},ensure_ascii=False))
