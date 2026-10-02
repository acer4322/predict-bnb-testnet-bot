from __future__ import annotations
import json,lzma
from pathlib import Path
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/hft_forward_paper_v1/markets'
OUT=ROOT/'data/research/r4_v0/hourly'
FROZEN={1700601,1700655,1701123,1701140,1701356,1701359,1701523,1701531}
TZ=ZoneInfo('Asia/Taipei')

def geom(s):
 up,down,cu,cd=s; paired=min(up,down); cost=cu+cd; gross=up+down
 au=cu/up if up>1e-12 else 0.; ad=cd/down if down>1e-12 else 0.
 return {'up':up,'down':down,'paired':paired,'cost':cost,'floor':paired-cost,'edge':1-(au+ad) if up>1e-12 and down>1e-12 else 0.,'absnet':abs(up-down),'coverage':2*paired/gross if gross>1e-12 else 0.}

def apply(s,side,px,q):
 up,down,cu,cd=s
 if side=='UP': up+=q; cu+=q*px
 else: down+=q; cd+=q*px
 return (up,down,cu,cd)

def events(d):
 out=[]
 for e in d.get('makerFillEvents') or []:
  q=float(e.get('deltaShares') or 0); px=float(e.get('price') or 0); side=str(e.get('side') or '').upper(); t=int(e.get('observedAtMs') or e.get('atMs') or 0)
  if q>0 and 0<=px<=1.05 and side in {'UP','DOWN'}: out.append((t,'MAKER',side,px,q))
 for e in d.get('takerEvents') or []:
  q=float(e.get('shares') or e.get('filledShares') or e.get('deltaShares') or e.get('qty') or 0); px=float(e.get('price') or e.get('fillPrice') or e.get('avgPrice') or 0); side=str(e.get('side') or '').upper(); t=int(e.get('observedAtMs') or e.get('atMs') or e.get('fillMs') or 0)
  if q>0 and 0<=px<=1.05 and side in {'UP','DOWN'}: out.append((t,'TAKER',side,px,q))
 return sorted(out)

def main():
 rows=[]; total_files=0; usable=0; pos_markets=0; break_markets=0; break_events=0; rel=Counter(); students=Counter(); examples=[]
 for p in sorted(SRC.glob('*.json.xz')):
  total_files+=1
  try:
   with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
  except Exception: continue
  mid=int(d.get('marketId') or 0)
  if mid in FROZEN: continue
  ev=events(d)
  if not ev: continue
  usable+=1; state=(0.,0.,0.,0.); hadpos=False; mb=0
  for t,role,side,px,q in ev:
   pre=geom(state); post=geom(apply(state,side,px,q)); reserve=max(0.,pre['floor']-post['floor'])
   if pre['floor']>0: hadpos=True
   flag=pre['floor']>0 and reserve>1e-12 and post['floor']<=0 and post['edge']<0
   if flag:
    mb+=1; break_events+=1
    surplus='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT'
    relation='SURPLUS_SIDE' if side==surplus else ('WEAK_SIDE_CROSS' if surplus!='FLAT' else 'FLAT')
    rel[relation]+=1
    if len(examples)<30: examples.append({'marketId':mid,'student':d.get('student'),'eventMs':t,'role':role,'side':side,'price':px,'shares':q,'relation':relation,'preFloor':pre['floor'],'postFloor':post['floor'],'postEdge':post['edge'],'preAbsNet':pre['absnet'],'postAbsNet':post['absnet']})
   state=apply(state,side,px,q)
  if hadpos: pos_markets+=1
  if mb:
   break_markets+=1; students[str(d.get('student'))]+=1
   rows.append({'marketId':mid,'student':d.get('student'),'file':str(p.relative_to(ROOT)).replace('\\','/'),'baseBreakEvents':mb,'final':geom(state)})
 now=datetime.now(TZ)
 report={'version':'R4_HFT_BASE_BREAK_SUPPORT_AUDIT_V1','createdAt':now.isoformat(),'source':'non-live hft_forward_paper_v1 realized closed-loop fills','guards':{'excludedFrozenEchtgeldMarketIds':sorted(FROZEN),'noDreamFill':True,'noWinnerUse':True,'noThresholdTuning':True,'researchOnly':True},'summary':{'filesScanned':total_files,'usableHistories':usable,'marketsWithPositiveFloor':pos_markets,'historiesWithBaseBreak':break_markets,'baseBreakEvents':break_events,'relationCounts':dict(rel),'studentCounts':dict(students)},'supportedHistories':rows,'examples':examples}
 out=OUT/f"r4_hft_base_break_support_audit_v1_{now.strftime('%Y%m%d_%H%M%S')}.json"; out.write_text(json.dumps(report,indent=2),encoding='utf-8')
 print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'summary':report['summary']},ensure_ascii=False))
if __name__=='__main__':main()
