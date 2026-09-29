from __future__ import annotations
import json,lzma,datetime,zoneinfo
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; P=ROOT/'data/research/r4_v0/p0_provenance_v1'; SRC=ROOT/'data/hft_forward_paper_v1/markets'; TAPE=ROOT/'data/execution_tape_v1/markets'
FROZEN={1700601,1700655,1701123,1701140,1701356,1701359,1701523,1701531}
def used_ids():
 out=set()
 def walk(x,k=None):
  if isinstance(x,dict):
   for a,b in x.items(): walk(b,a)
  elif isinstance(x,list):
   for z in x: walk(z,k)
  elif isinstance(x,int) and 1_000_000<=x<=9_999_999 and (k in {'marketId','market_id'} or k in {'marketIds','markets','cohort','ids','development','independentReplication','extensionMarketIds'}): out.add(x)
 for p in P.glob('*.json'):
  try: walk(json.loads(p.read_text(encoding='utf-8')))
  except Exception: pass
 return out
def main():
 used=used_ids(); ids=[]
 files=sorted(SRC.glob('*.json.xz'),key=lambda p:p.stat().st_mtime_ns,reverse=True)
 for p in files:
  try:d=json.load(lzma.open(p,'rt',encoding='utf-8'))
  except Exception:continue
  mid=int(d.get('marketId') or 0)
  if not mid or mid in used or mid in FROZEN or 'R2_RESIDUAL' not in str(d.get('student') or '') or not (TAPE/f'{mid}.json.xz').exists():continue
  ds=[int(x.get('decisionMs') or 0) for x in d.get('decisionRows',[]) if int(x.get('decisionMs') or 0)>0]
  if ds:
   day=datetime.datetime.fromtimestamp(min(ds)/1000,datetime.timezone.utc).astimezone(zoneinfo.ZoneInfo('Asia/Taipei')).date()
   if day==datetime.date(2026,8,16):continue
  ids.append(mid)
  if len(ids)>=240:break
 rep={'version':'R4_P0B_OBJECTIVE_GROUPING_REPLICATION_COHORT_V1','researchOnly':True,'selection':'First 240 currently-unused R2_RESIDUAL realistic-HFT markets by source chronology; all P0 referenced IDs and Taipei 2026-08-16 excluded before any opportunity/outcome scan.','marketIds':ids,'guards':['no trigger screening','no role/outcome screening','2026-08-16 sealed','no R3/8781/Echtgeld']}
 q=P/'r4_p0b_objective_grouping_replication_cohort_preregistered_v1.json';q.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(q.relative_to(ROOT)),'markets':len(ids),'firstLast':[ids[0],ids[-1]] if ids else None,'excluded':len(used)},ensure_ascii=False))
if __name__=='__main__':main()
