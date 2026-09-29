from __future__ import annotations
import json, sqlite3, statistics, math
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'
OUT=ROOT/'data'/'research'/'r3_v0'/'r3_safe_base_formation_v0.json'


def fee(sh,px,role):
    return sh*px*0.02 if str(role).upper()=='TAKER' else 0.0


def analyze_market(c,mid):
    rr=c.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall()
    if len(rr)<4: return None
    up=down=cost=fees=0.0
    states=[]
    first_safe_idx=None
    for i,(role,side,t,px,sh) in enumerate(rr):
        role=str(role); side=str(side); t=int(t); px=float(px); sh=float(sh)
        if side=='UP': up+=sh
        else: down+=sh
        cost += px*sh; fees += fee(sh,px,role)
        pu=up-cost-fees; pd=down-cost-fees
        floor=min(pu,pd); upside=max(pu,pd)
        surplus=up-down
        sabs=abs(surplus); base=min(up,down)
        st={'i':i,'t':t,'role':role,'side':side,'px':px,'sh':sh,'up':up,'down':down,'floor':floor,'upside':upside,'surplus':surplus,'surplusAbs':sabs,'base':base}
        states.append(st)
        if first_safe_idx is None and floor>=0:
            first_safe_idx=i
    if first_safe_idx is None:
        # summarize no-safe pre behavior
        pre=states
        return {'marketId':mid,'everSafe':False,'n':len(states),'preMakerRate':sum(s['role']=='MAKER' for s in pre)/len(pre),'preTakerRate':sum(s['role']=='TAKER' for s in pre)/len(pre),'preFinalSurplusAbs':pre[-1]['surplusAbs'],'preMaxSurplusAbs':max(s['surplusAbs'] for s in pre),'preBaseFinal':pre[-1]['base']}

    fs=states[first_safe_idx]
    pre=states[:first_safe_idx]
    cross_prev=states[first_safe_idx-1] if first_safe_idx>0 else None
    post=states[first_safe_idx+1:]
    t0=states[0]['t']; ts=fs['t']; total=max(1,states[-1]['t']-t0)

    # when did persistent surplus start? Define first point before safe where abs surplus >= max(18, 10% of base+surplus) and remains non-flat (same sign or >=5 abs) for next 3 events.
    pers_idx=None
    for i in range(first_safe_idx):
        s=states[i]
        thresh=max(18.0,0.10*max(1.0,s['up']+s['down']))
        if s['surplusAbs']<thresh: continue
        sign=1 if s['surplus']>0 else -1
        fut=states[i:min(first_safe_idx+1,i+4)]
        if len(fut)>=3 and sum((1 if x['surplus']>0 else -1 if x['surplus']<0 else 0)==sign and x['surplusAbs']>=5 for x in fut)>=3:
            pers_idx=i; break

    # dynamics in last 15s before safe
    near=[s for s in pre if ts-s['t']<=15000]
    maker_near=[s for s in near if s['role']=='MAKER']; taker_near=[s for s in near if s['role']=='TAKER']
    weak_side='DOWN' if fs['surplus']>0 else 'UP' if fs['surplus']<0 else None
    strong_side='UP' if fs['surplus']>0 else 'DOWN' if fs['surplus']<0 else None
    weak_near=sum(s['sh'] for s in near if weak_side and s['side']==weak_side)
    strong_near=sum(s['sh'] for s in near if strong_side and s['side']==strong_side)
    cross_role=fs['role']; cross_side=fs['side']
    cross_is_weak = bool(weak_side and cross_side==weak_side)

    return {
      'marketId':mid,'everSafe':True,'n':len(states),'firstSafeIdx':first_safe_idx,'firstSafeMsFromStart':ts-t0,'firstSafeFracTime':(ts-t0)/total,
      'floorBefore':cross_prev['floor'] if cross_prev else None,'floorAtSafe':fs['floor'],'upsideAtSafe':fs['upside'],'surplusAbsAtSafe':fs['surplusAbs'],'baseAtSafe':fs['base'],
      'crossRole':cross_role,'crossSide':cross_side,'crossIsWeakSide':cross_is_weak,
      'persistentSurplusBeforeSafe':pers_idx is not None,
      'persistentSurplusLeadMs':(ts-states[pers_idx]['t']) if pers_idx is not None else None,
      'surplusAbsAtPersistentStart':states[pers_idx]['surplusAbs'] if pers_idx is not None else None,
      'preMaxSurplusAbs':max([s['surplusAbs'] for s in pre],default=0.0),
      'preMakerRate':sum(s['role']=='MAKER' for s in pre)/max(1,len(pre)),'preTakerRate':sum(s['role']=='TAKER' for s in pre)/max(1,len(pre)),
      'near15sWeakShares':weak_near,'near15sStrongShares':strong_near,'near15sWeakMinusStrong':weak_near-strong_near,
      'near15sMakerEvents':len(maker_near),'near15sTakerEvents':len(taker_near),
      'postMakerRate':sum(s['role']=='MAKER' for s in post)/max(1,len(post)) if post else None,
      'postTakerRate':sum(s['role']=='TAKER' for s in post)/max(1,len(post)) if post else None
    }


def main():
    c=sqlite3.connect(DB)
    mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]
    rows=[]
    for mid in mids:
        z=analyze_market(c,mid)
        if z: rows.append(z)
    c.close()
    safe=[x for x in rows if x['everSafe']]; nosafe=[x for x in rows if not x['everSafe']]
    def med(vals):
        vals=[v for v in vals if v is not None and math.isfinite(float(v))]
        return statistics.median(vals) if vals else None
    role=Counter(x['crossRole'] for x in safe)
    summary={
      'version':'R3_SAFE_BASE_FORMATION_V0','markets':len(rows),'everSafeMarkets':len(safe),'everSafeRate':len(safe)/len(rows) if rows else None,
      'persistentSurplusBeforeSafeRate':sum(x['persistentSurplusBeforeSafe'] for x in safe)/len(safe) if safe else None,
      'medianPersistentSurplusLeadMs':med([x['persistentSurplusLeadMs'] for x in safe]),
      'medianFirstSafeMsFromStart':med([x['firstSafeMsFromStart'] for x in safe]),
      'medianFirstSafeFracTime':med([x['firstSafeFracTime'] for x in safe]),
      'crossRoleCounts':dict(role),'crossWeakSideRate':sum(x['crossIsWeakSide'] for x in safe)/len(safe) if safe else None,
      'medianNear15sWeakMinusStrongShares':med([x['near15sWeakMinusStrong'] for x in safe]),
      'safePreMakerRateMedian':med([x['preMakerRate'] for x in safe]),'safePreTakerRateMedian':med([x['preTakerRate'] for x in safe]),
      'noSafePreMakerRateMedian':med([x['preMakerRate'] for x in nosafe]),'noSafePreTakerRateMedian':med([x['preTakerRate'] for x in nosafe]),
      'safePreMaxSurplusMedian':med([x['preMaxSurplusAbs'] for x in safe]),'noSafePreMaxSurplusMedian':med([x['preMaxSurplusAbs'] for x in nosafe]),
      'safeBaseAtSafeMedian':med([x['baseAtSafe'] for x in safe]),'safeSurplusAtSafeMedian':med([x['surplusAbsAtSafe'] for x in safe]),
    }
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps({'summary':summary,'markets':rows},indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))

if __name__=='__main__': main()
