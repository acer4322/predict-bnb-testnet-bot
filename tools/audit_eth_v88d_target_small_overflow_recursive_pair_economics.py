from __future__ import annotations
import json,sqlite3,statistics,math,importlib.util,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9
spec=importlib.util.spec_from_file_location('v71',Path(__file__).with_name('audit_eth_v71_generation_economic_anatomy.py'));v71=importlib.util.module_from_spec(spec);spec.loader.exec_module(v71)

def med(xs):
 xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))];return statistics.median(xs) if xs else None

def comps(mids,ends=None):
 con=sqlite3.connect('data/target_wallet_official_v1.db');ph=','.join('?'*len(mids));rs=con.execute(f"select market_id,parent_id,role,side,average_price,shares,first_event_ms from target_parent_orders where asset='ETH' and market_id in ({ph}) order by market_id,first_event_ms,parent_id",mids).fetchall();con.close();return v71.target_components(rs,ends or {int(m):10**18 for m in mids},pre180=True)

def audit(cs):
 by={}
 for x in cs:by.setdefault(int(x['marketId']),[]).append(x)
 rows=[]
 for mid,ev in by.items():
  parents={}
  for x in ev:parents.setdefault((int(x['t']),str(x['parentId'])),[]).append(x)
  ps=sorted(parents.items())
  for i,((t,pid),parts) in enumerate(ps):
   kinds={x['kind'] for x in parts}
   if not {'REPAIR','EXPAND'}.issubset(kinds):continue
   side=str(parts[0]['side']);opp='DOWN' if side=='UP' else 'UP';ov=sum(float(x['qty']) for x in parts if x['kind']=='EXPAND');px0=float(parts[0]['price']);nxt=None
   for (tt,pp),parts2 in ps[i+1:]:
    if int(tt)<=t:continue
    rr=[x for x in parts2 if x['kind']=='REPAIR' and str(x['side'])==opp]
    if not rr:continue
    px1=float(rr[0]['price']);legal=1/px1 if px1>EPS else None;rep=sum(float(x['qty']) for x in rr);nxt={'t':int(tt),'lagSec':(int(tt)-t)/1000,'route':str(rr[0]['route']),'price':px1,'repairComponentQty':rep,'legalMinQtyAtRepairPrice':legal,'overflowCoversOneLegalSlice':bool(legal and ov+EPS>=legal),'nextParentComposite':any(x['kind']=='EXPAND' for x in parts2),'pairPriceSum':px0+px1};break
   if nxt:rows.append({'marketId':mid,'t':t,'currentCompositePrice':px0,'overflow':ov,'next':nxt})
 small=[r for r in rows if not r['next']['overflowCoversOneLegalSlice']];
 def summ(xs):
  return {'n':len(xs),'pairSumLt1Share':sum(r['next']['pairPriceSum']<1-EPS for r in xs)/len(xs) if xs else None,'pairSumLe1Share':sum(r['next']['pairPriceSum']<=1+EPS for r in xs)/len(xs) if xs else None,'medianPairSum':med([r['next']['pairPriceSum'] for r in xs]),'nextCompositeShare':sum(r['next']['nextParentComposite'] for r in xs)/len(xs) if xs else None,'medianLagSec':med([r['next']['lagSec'] for r in xs])}
 return {'all':summ(rows),'smallOverflow':summ(small),'smallRows':small,'rows':rows}

def main():
 v69=json.load(open('data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V69_STAGEA16_TARGET_ACTION_CONDITION_GAP_20260903.json',encoding='utf-8'));ends={}
 for r in v69['conditionRows']:
  mid=int(r['marketId'])
  if mid not in ends:
   s=r['ourState'];ends[mid]=int(round(float(s['snapshotT'])+float(s['remainingSec'])*1000))
 a=audit(comps(v71.STAGEA,ends));f=audit(comps([1912961],None));out={'version':'TARGET_ETH_V88D_SMALL_OVERFLOW_RECURSIVE_PAIR_ECONOMICS','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'stageA16Pre180':a,'fresh1912961':f,'boundary':['Target post-market fills only','pair price sum is descriptive, never runtime trigger','same-clock ordering inherited from v71 exclusions','no winner/PnL','no controller change']};Path('data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V88D_SMALL_OVERFLOW_RECURSIVE_PAIR_ECONOMICS_20260903.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'stageAll':a['all'],'stageSmall':a['smallOverflow'],'freshAll':f['all'],'freshSmall':f['smallOverflow']},ensure_ascii=False))
if __name__=='__main__':main()
