from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_protection_inherited_responsibility_v1 as v1
PREREG=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_protection_inherited_responsibility_preregistered_v2.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1'

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,default=15);a=ap.parse_args()
 pr=json.loads(PREREG.read_text(encoding='utf-8'));ids=[int(x) for x in pr['preOutcomeSupportConstruction']['candidateMarketIds']]; sel=ids[a.start:a.start+a.count]
 rows=[];markets=[]
 for i,mid in enumerate(sel,1):
  try:r=v1.one_market(mid)
  except Exception as e:r={'marketId':mid,'status':'ERROR','error':f'{type(e).__name__}:{e}','rows':[]}
  rows.extend(r.pop('rows',[]));markets.append(r);print(json.dumps({'start':a.start,'i':i,'n':len(sel),'marketId':mid,'status':r.get('status'),'rowsTotal':len(rows)},ensure_ascii=False),flush=True)
 rep={'version':'R4_P0B_PROTECTION_INHERITED_RESPONSIBILITY_V2_CHUNK','preregistered':str(PREREG.relative_to(ROOT)).replace('\\','/'),'start':a.start,'count':len(sel),'marketIds':sel,'marketResults':markets,'rows':rows,'guards':pr['guards']}
 p=OUT/f'r4_p0b_protection_inherited_responsibility_v2_chunk_{a.start}_{len(sel)}.json';p.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'artifact':str(p.relative_to(ROOT)).replace('\\','/'),'markets':len(sel),'rows':len(rows)},ensure_ascii=False))
if __name__=='__main__':main()
