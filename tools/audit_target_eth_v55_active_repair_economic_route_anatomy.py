from __future__ import annotations
import argparse,sqlite3,json,math
from pathlib import Path
from collections import defaultdict
import numpy as np
from sklearn.metrics import roc_auc_score
EPS=1e-9

def qsum(vals):
    a=np.asarray([float(x) for x in vals if x is not None and math.isfinite(float(x))],float)
    if not len(a): return {'n':0}
    return {'n':int(len(a)),'mean':float(a.mean()),'p10':float(np.quantile(a,.1)),'p25':float(np.quantile(a,.25)),'median':float(np.median(a)),'p75':float(np.quantile(a,.75)),'p90':float(np.quantile(a,.9))}

def auc(rows,key,flip=False):
    z=[r for r in rows if r.get(key) is not None and math.isfinite(float(r[key]))]
    if not z or len(set(int(r['active']) for r in z))<2:return None
    x=np.asarray([float(r[key]) for r in z]);
    if flip:x=-x
    return float(roc_auc_score(np.asarray([int(r['active']) for r in z]),x))

def summarize(rows):
    if not rows:return {'n':0}
    a=[r for r in rows if r['active']];p=[r for r in rows if not r['active']]
    def side(rr):
        return {
          'n':len(rr),
          'priceMinusCeiling':qsum([r['priceMinusCeiling'] for r in rr]),
          'priceOverCeiling':qsum([r['priceOverCeiling'] for r in rr]),
          'repairFracPreAbs':qsum([r['repairFracPreAbs'] for r in rr]),
          'secondsSinceLatestExpand':qsum([r['secondsSinceLatestExpand'] for r in rr]),
          'preFloor':qsum([r['preFloor'] for r in rr]),
          'generationRepairProgress':qsum([r['generationRepairProgress'] for r in rr]),
          'atOrBelowCeilingRate':float(np.mean([r['priceMinusCeiling']<=EPS for r in rr if r['priceMinusCeiling'] is not None])) if any(r['priceMinusCeiling'] is not None for r in rr) else None,
          'aboveCeilingRate':float(np.mean([r['priceMinusCeiling']>EPS for r in rr if r['priceMinusCeiling'] is not None])) if any(r['priceMinusCeiling'] is not None for r in rr) else None,
        }
    return {'n':len(rows),'active':len(a),'activeRate':len(a)/len(rows),'ACTIVE_REPAIR':side(a),'PASSIVE_REPAIR':side(p),'aucActiveFromPriceMinusCeiling':auc(rows,'priceMinusCeiling'),'aucActiveFromPriceOverCeiling':auc(rows,'priceOverCeiling'),'aucActiveFromSecondsSinceLatestExpand':auc(rows,'secondsSinceLatestExpand'),'aucActiveFromGenerationRepairProgress':auc(rows,'generationRepairProgress')}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    con=sqlite3.connect(a.db)
    ev=defaultdict(list)
    for m,role,side,t,p,q in con.execute("select market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' and average_price is not null and shares>0 order by market_id,first_event_ms,parent_id"):
        ev[int(m)].append((int(t),str(role).upper(),str(side).upper(),float(p),float(q)))
    con.close();rows=[]
    for mid,xs in ev.items():
        U=D=C=0.;latestExpandAt=None;latestGenDebt=0.;latestGenPaid=0.;lastKind=None;lastRole=None
        for t,role,side,p,q in xs:
            preU,preD,preC=U,D,C;preAbs=abs(preU-preD);strong=max(preU,preD);weak=min(preU,preD);preFloor=weak-preC
            dominant='UP' if preU>preD+EPS else ('DOWN' if preD>preU+EPS else 'FLAT')
            weakSide='DOWN' if dominant=='UP' else ('UP' if dominant=='DOWN' else None)
            repairQty=min(q,preAbs) if weakSide and side==weakSide else 0.
            ceiling=((strong-preC)/preAbs) if preAbs>EPS else None
            if repairQty>EPS:
                gap=(p-ceiling) if ceiling is not None else None
                ratio=(p/ceiling) if ceiling is not None and abs(ceiling)>EPS else None
                progress=min(1.,latestGenPaid/(latestGenDebt+EPS)) if latestGenDebt>EPS else 1.
                rows.append({'marketId':mid,'t':t,'role':role,'active':int(role=='TAKER'),'side':side,'price':p,'qty':q,'repairQty':repairQty,'preAbsNet':preAbs,'preFloor':preFloor,'economicRepairCeiling':ceiling,'priceMinusCeiling':gap,'priceOverCeiling':ratio,'repairFracPreAbs':repairQty/(preAbs+EPS),'secondsSinceLatestExpand':((t-latestExpandAt)/1000.) if latestExpandAt is not None else None,'generationRepairProgress':progress,'lastKind':lastKind,'lastRole':lastRole})
            if side=='UP':U+=q
            else:D+=q
            C+=p*q
            postAbs=abs(U-D);delta=postAbs-preAbs
            if delta>EPS:
                kind='EXPAND';latestExpandAt=t;latestGenDebt=delta;latestGenPaid=0.
            elif delta<-EPS:
                kind='REPAIR';pay=min(latestGenDebt-latestGenPaid if latestGenDebt>latestGenPaid else -delta,-delta);latestGenPaid=min(latestGenDebt,latestGenPaid+max(0.,pay))
            else: kind='FLAT'
            if kind in ('EXPAND','REPAIR'):
                lastKind=kind;lastRole=role
            if latestGenDebt>EPS and latestGenPaid>=latestGenDebt-EPS:
                latestGenDebt=0.;latestGenPaid=0.
    postExpand=[r for r in rows if r['lastKind']=='EXPAND'];postRepair=[r for r in rows if r['lastKind']=='REPAIR'];negFloor=[r for r in rows if r['preFloor']<0]
    out={'version':'TARGET_ETH_V55_ACTIVE_REPAIR_ECONOMIC_ROUTE_ANATOMY','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'coverage':{'markets':len(ev),'repairEvents':len(rows)},'ALL':summarize(rows),'POST_EXPAND':summarize(postExpand),'POST_REPAIR':summarize(postRepair),'NEGATIVE_FLOOR':summarize(negFloor),'crossTabs':{'lastRole':{k:summarize([r for r in rows if r['lastRole']==k]) for k in ['MAKER','TAKER']},'lastKind':{'EXPAND':summarize(postExpand),'REPAIR':summarize(postRepair)}},'guards':['event fill price is descriptive proxy for executable price, not private decision-time proof','no fitted threshold','no winner/PnL','no BTC numeric transfer','no 8781']}
    Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
