from __future__ import annotations
import argparse,json,sqlite3,statistics,math,importlib.util,sys
from pathlib import Path
EPS=1e-9
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('v71base',Path(__file__).with_name('audit_eth_v71_generation_economic_anatomy.py'));v71=importlib.util.module_from_spec(spec);spec.loader.exec_module(v71)

def pct(xs,p):
    xs=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
    if not xs:return None
    if len(xs)==1:return xs[0]
    z=(len(xs)-1)*p;i=int(z);f=z-i
    return xs[i]*(1-f)+xs[min(i+1,len(xs)-1)]*f

def clock_rows(components):
    by={}
    for x in components:
        key=(int(x['marketId']),int(x['t']),str(x['kind']),str(x['side']))
        z=by.setdefault(key,{'marketId':key[0],'t':key[1],'kind':key[2],'side':key[3],'qty':0.0,'notional':0.0,'floorDelta':0.0,'bestDelta':0.0,'routes':set()})
        z['qty']+=float(x['qty']);z['notional']+=float(x['notional']);z['floorDelta']+=float(x['floorDelta']);z['bestDelta']+=float(x['bestDelta']);z['routes'].add(str(x.get('route')))
    out=[]
    for z in by.values():
        z['price']=z['notional']/z['qty'] if z['qty']>EPS else None;z['routes']=sorted(z['routes']);out.append(z)
    return out

def audit(components):
    clocks=clock_rows(components);segments=[];ambiguous=0
    for mid in sorted(set(int(x['marketId']) for x in clocks)):
        ev=sorted([x for x in clocks if int(x['marketId'])==mid],key=lambda x:(int(x['t']),0 if x['kind']=='REPAIR' else 1,x['side']))
        times=sorted(set(int(x['t']) for x in ev));cur=None;rid=0
        for t in times:
            at=[x for x in ev if int(x['t'])==t];ex=[x for x in at if x['kind']=='EXPAND'];ex_sides=set(x['side'] for x in ex)
            if len(ex_sides)>1:
                ambiguous+=1;cur=None;continue
            if not ex:continue
            side=ex[0]['side'];pay='DOWN' if side=='UP' else 'UP'
            ex_qty=sum(float(x['qty']) for x in ex);ex_notional=sum(float(x['notional']) for x in ex);ex_price=ex_notional/ex_qty if ex_qty>EPS else None
            if cur is None or side!=cur['side']:
                rid+=1;cur={'id':rid,'side':side,'paySide':pay,'lastExpandAt':t};continue
            prior=int(cur['lastExpandAt'])
            repairs=[x for x in ev if x['kind']=='REPAIR' and x['side']==pay and prior<int(x['t'])<t]
            rq=sum(float(x['qty']) for x in repairs);rn=sum(float(x['notional']) for x in repairs);rp=rn/rq if rq>EPS else None
            pair=(rp+ex_price) if rp is not None and ex_price is not None else None
            matched=min(rq,ex_qty);matched_gain=(matched*(1.0-pair)) if pair is not None else None
            seg={'marketId':mid,'responsibilityId':rid,'side':side,'priorExpandAt':prior,'t':t,'lagSec':(t-prior)/1000.0,'repairQty':rq,'repairNotional':rn,'repairPrice':rp,'expandQty':ex_qty,'expandNotional':ex_notional,'expandPrice':ex_price,'marginalPairSum':pair,'favorablePairLt1':bool(pair is not None and pair<1.0-EPS),'nonDamagingPairLe1':bool(pair is not None and pair<=1.0+EPS),'matchedQty':matched,'repairToExpandQty':rq/ex_qty if ex_qty>EPS else None,'unmatchedExpandQty':max(0.0,ex_qty-rq),'matchedFloorGain':matched_gain,'repairFillCount':len(repairs),'expandRoutes':sorted(set(r for x in ex for r in x['routes']))}
            segments.append(seg);cur['lastExpandAt']=t
    scored=[s for s in segments if s['marginalPairSum'] is not None];pairs=[s['marginalPairSum'] for s in scored];gains=[s['matchedFloorGain'] for s in scored if s['matchedFloorGain'] is not None];cov=[s['repairToExpandQty'] for s in scored if s['repairToExpandQty'] is not None]
    summary={'markets':len(set(int(x['marketId']) for x in clocks)),'reExpandSegments':len(segments),'scoredPairSegments':len(scored),'noInterveningRepairShare':sum(s['repairQty']<=EPS for s in segments)/len(segments) if segments else None,'ambiguousOppositeExpandClocksExcluded':ambiguous,'medianPairSum':pct(pairs,.5),'p25PairSum':pct(pairs,.25),'p75PairSum':pct(pairs,.75),'favorablePairLt1Share':sum(s['favorablePairLt1'] for s in scored)/len(scored) if scored else None,'nonDamagingPairLe1Share':sum(s['nonDamagingPairLe1'] for s in scored)/len(scored) if scored else None,'medianMatchedFloorGain':pct(gains,.5),'medianRepairToExpandQty':pct(cov,.5),'p25RepairToExpandQty':pct(cov,.25),'medianLagSec':pct([s['lagSec'] for s in segments],.5)}
    return summary,segments

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--target-db',default='data/target_wallet_official_v1.db');ap.add_argument('--v69',default='data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V69_STAGEA16_TARGET_ACTION_CONDITION_GAP_20260903.json');ap.add_argument('--v70f',default='data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V70F_EARLY_CLOCK_PARALLEL_RELAY_SMOKE_20260903.json');ap.add_argument('--output',required=True);a=ap.parse_args()
    v69=json.load(open(a.v69,encoding='utf-8'));end_by={}
    for row in v69['conditionRows']:
        mid=int(row['marketId'])
        if mid not in end_by:
            s=row['ourState'];end_by[mid]=int(round(float(s['snapshotT'])+float(s['remainingSec'])*1000.))
    con=sqlite3.connect(a.target_db);ph=','.join('?'*len(v71.STAGEA));rows=con.execute(f"select market_id,parent_id,role,side,average_price,shares,first_event_ms from target_parent_orders where asset='ETH' and market_id in ({ph}) order by market_id,first_event_ms,parent_id",v71.STAGEA).fetchall();con.close()
    tc=v71.target_components(rows,end_by,pre180=True);oc=v71.our_components(json.load(open(a.v70f,encoding='utf-8')))
    ts,tseg=audit(tc);os,oseg=audit(oc)
    cond1=ts['favorablePairLt1Share'] is not None and os['favorablePairLt1Share'] is not None and ts['favorablePairLt1Share']>=os['favorablePairLt1Share']+0.20
    cond2=ts['medianPairSum'] is not None and os['medianPairSum'] is not None and ts['medianPairSum']<=os['medianPairSum']-0.05
    keep=bool(cond1 or cond2)
    decision='KEEP_MARGINAL_PAIR_ECONOMICS_AS_RESPONSIBILITY_REPLENISHMENT_AXIS' if keep else 'REJECT_SIMPLE_PAIR_ECONOMICS_DIFFERENTIATOR'
    out={'version':'ETH_REPAIR_V71G_MARGINAL_PAIR_REEXPAND_ECONOMICS','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'target':ts,'ourV70F':os,'comparison':{'targetMinusOurFavorablePairShare':(ts['favorablePairLt1Share']-os['favorablePairLt1Share']) if ts['favorablePairLt1Share'] is not None and os['favorablePairLt1Share'] is not None else None,'targetMinusOurMedianPairSum':(ts['medianPairSum']-os['medianPairSum']) if ts['medianPairSum'] is not None and os['medianPairSum'] is not None else None},'gates':{'favorablePairShareGapGe020':cond1,'medianPairSumAtLeast005Lower':cond2,'keep':keep},'decision':decision,'targetSegments':tseg,'ourSegments':oseg,'boundary':['strictly-between Repair only','same-clock Repair excluded','pair sum 1.0 structural, not tuned','no winner/PnL','no controller change','no H100','no 8781']}
    Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'target':ts,'our':os,'comparison':out['comparison'],'ourSegments':oseg},ensure_ascii=False))
if __name__=='__main__':main()
