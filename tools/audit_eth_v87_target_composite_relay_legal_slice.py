from __future__ import annotations
import json,sqlite3,statistics,math,importlib.util,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9
spec=importlib.util.spec_from_file_location('v71',Path(__file__).with_name('audit_eth_v71_generation_economic_anatomy.py'));v71=importlib.util.module_from_spec(spec);spec.loader.exec_module(v71)

def med(xs):
 xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))];return statistics.median(xs) if xs else None

def comps(mids,end_by=None,pre180=False):
 con=sqlite3.connect('data/target_wallet_official_v1.db');ph=','.join('?'*len(mids));rs=con.execute(f"select market_id,parent_id,role,side,average_price,shares,first_event_ms from target_parent_orders where asset='ETH' and market_id in ({ph}) order by market_id,first_event_ms,parent_id",mids).fetchall();con.close();ends=end_by or {int(m):10**18 for m in mids};return v71.target_components(rs,ends,pre180=True)

def audit(cs):
 by={}
 for x in cs:by.setdefault(int(x['marketId']),[]).append(x)
 out=[]
 for mid,ev in by.items():
  parents={}
  for x in ev:parents.setdefault((int(x['t']),str(x['parentId'])),[]).append(x)
  ps=sorted(parents.items())
  for i,((t,pid),parts) in enumerate(ps):
   if not {'REPAIR','EXPAND'}.issubset({x['kind'] for x in parts}):continue
   side=str(parts[0]['side']);opp='DOWN' if side=='UP' else 'UP';ov=sum(float(x['qty']) for x in parts if x['kind']=='EXPAND');nxt=None
   for (tt,pp),parts2 in ps[i+1:]:
    if int(tt)<=t:continue
    rr=[x for x in parts2 if x['kind']=='REPAIR' and str(x['side'])==opp]
    if rr:
     px=float(rr[0]['price']);legal=1.0/px if px>EPS else None;rep=sum(float(x['qty']) for x in rr);nxt={'lagSec':(int(tt)-t)/1000,'route':str(rr[0]['route']),'price':px,'repairComponentQty':rep,'legalMinQtyAtRepairPrice':legal,'overflowToLegalMin':ov/legal if legal and legal>EPS else None,'overflowCoversOneLegalSlice':bool(legal and ov+EPS>=legal),'nextParentComposite':any(x['kind']=='EXPAND' for x in parts2)};break
   if nxt:out.append({'marketId':mid,'t':t,'overflow':ov,'next':nxt})
 n=len(out);small=[r for r in out if not r['next']['overflowCoversOneLegalSlice']]
 return {'n':n,'overflowCoversOneLegalSliceShare':sum(r['next']['overflowCoversOneLegalSlice'] for r in out)/n if n else None,'medianOverflowToLegalMin':med([r['next']['overflowToLegalMin'] for r in out]),'p25OverflowToLegalMin':sorted([r['next']['overflowToLegalMin'] for r in out])[max(0,int(.25*(n-1)))] if n else None,'smallOverflowCount':len(small),'smallOverflowNextParentCompositeShare':sum(r['next']['nextParentComposite'] for r in small)/len(small) if small else None,'route':{rt:{'n':sum(r['next']['route']==rt for r in out),'covers':sum(r['next']['route']==rt and r['next']['overflowCoversOneLegalSlice'] for r in out)} for rt in ['MAKER','TAKER']},'rows':out}

def main():
 v69=json.load(open('data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V69_STAGEA16_TARGET_ACTION_CONDITION_GAP_20260903.json',encoding='utf-8'));ends={}
 for r in v69['conditionRows']:
  mid=int(r['marketId'])
  if mid not in ends:
   s=r['ourState'];ends[mid]=int(round(float(s['snapshotT'])+float(s['remainingSec'])*1000))
 a=audit(comps(v71.STAGEA,ends,True));f=audit(comps([1912961],None,False));out={'version':'TARGET_ETH_V87_COMPOSITE_RELAY_LEGAL_SLICE','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'stageA16Pre180':a,'fresh1912961':f,'boundary':['post-market Target actual fills only','next Repair price used as descriptive execution label, never runtime input','same-clock excluded','no winner/PnL']};Path('data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V87_COMPOSITE_RELAY_LEGAL_SLICE_20260903.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'stage':{k:v for k,v in a.items() if k!='rows'},'fresh':{k:v for k,v in f.items() if k!='rows'}},ensure_ascii=False))
if __name__=='__main__':main()
