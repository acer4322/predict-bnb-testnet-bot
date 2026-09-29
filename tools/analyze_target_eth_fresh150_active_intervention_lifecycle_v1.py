from __future__ import annotations
import argparse,sqlite3,json,math,statistics
from pathlib import Path
from collections import defaultdict,Counter
EPS=1e-9

def qtile(xs,q):
    ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
    if not ys:return None
    z=(len(ys)-1)*q;lo=int(math.floor(z));hi=int(math.ceil(z));w=z-lo
    return ys[lo]*(1-w)+ys[hi]*w

def stats(xs):
    ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return {'n':len(ys),'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'p25':qtile(ys,.25),'p75':qtile(ys,.75),'p90':qtile(ys,.9)}

def make_parents(c,mid):
    ev=[dict(zip(['rid','role','side','order_hash','event_ms','price','shares'],r)) for r in c.execute("select rowid,role,side,coalesce(nullif(order_hash,''),source_leg_id),event_ms,price,shares from maker_book_inference_wallet_events where market_id=? and role in ('MAKER','TAKER') and quote_type='BID' and side in ('UP','DOWN') order by event_ms,rowid",(mid,))]
    by={}
    for e in ev:
        key=(e['role'],str(e['order_hash']),e['side'])
        z=by.setdefault(key,{'marketId':mid,'role':e['role'],'side':e['side'],'orderHash':str(e['order_hash']),'firstEventMs':int(e['event_ms']),'lastEventMs':int(e['event_ms']),'shares':0.0,'notional':0.0,'legs':0})
        z['firstEventMs']=min(z['firstEventMs'],int(e['event_ms']));z['lastEventMs']=max(z['lastEventMs'],int(e['event_ms']));z['shares']+=float(e['shares']);z['notional']+=float(e['shares'])*float(e['price']);z['legs']+=1
    pp=[]
    # event-level strict-before inventory snapshots, robust to same-timestamp parent ties.
    for z in by.values():
        t=z['firstEventMs']; pre=[e for e in ev if int(e['event_ms'])<t]
        up=sum(float(e['shares']) for e in pre if e['side']=='UP');down=sum(float(e['shares']) for e in pre if e['side']=='DOWN')
        mup=sum(float(e['shares']) for e in pre if e['role']=='MAKER' and e['side']=='UP');mdown=sum(float(e['shares']) for e in pre if e['role']=='MAKER' and e['side']=='DOWN')
        preabs=abs(up-down);postabs=abs((up+z['shares']*(z['side']=='UP'))-(down+z['shares']*(z['side']=='DOWN')))
        mpreabs=abs(mup-mdown);mpostabs=abs((mup+z['shares']*(z['role']=='MAKER' and z['side']=='UP'))-(mdown+z['shares']*(z['role']=='MAKER' and z['side']=='DOWN')))
        z.update({'avgPrice':z['notional']/z['shares'] if z['shares']>EPS else None,'preUp':up,'preDown':down,'preAbsNet':preabs,'postAbsNet':postabs,'deltaAbsNet':postabs-preabs,'preMakerUp':mup,'preMakerDown':mdown,'preMakerAbsNet':mpreabs,'postMakerAbsNet':mpostabs,'deltaMakerAbsNet':mpostabs-mpreabs})
        pp.append(z)
    return sorted(pp,key=lambda x:(x['firstEventMs'],0 if x['role']=='MAKER' else 1,x['orderHash']))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();c=sqlite3.connect(f'file:{Path(a.db).resolve().as_posix()}?mode=ro',uri=True)
    mids=[int(r[0]) for r in c.execute("select distinct market_id from maker_book_inference_wallet_events where role='TAKER' order by market_id")];rows=[]
    for mi,mid in enumerate(mids,1):
        pp=make_parents(c,mid);makers=[p for p in pp if p['role']=='MAKER'];takers=[p for p in pp if p['role']=='TAKER']
        for ti,t in enumerate(takers):
            preabs=t['preAbsNet'];postabs=t['postAbsNet']
            if t['preUp']+t['preDown']<=EPS:effect='BUILD_FROM_FLAT'
            elif postabs<preabs-EPS:effect='REPAIR_EFFECT'
            elif postabs>preabs+EPS:effect='ADD_EFFECT'
            else:effect='FLAT_EFFECT'
            dominant='UP' if t['preUp']>t['preDown']+EPS else 'DOWN' if t['preDown']>t['preUp']+EPS else 'FLAT'
            oppdom='DOWN' if dominant=='UP' else 'UP' if dominant=='DOWN' else None
            prior_exp=[m for m in makers if m['firstEventMs']<t['firstEventMs'] and m['deltaMakerAbsNet']>EPS]
            ex=max(prior_exp,key=lambda x:x['firstEventMs']) if prior_exp else None
            intervening=[m for m in makers if ex is not None and ex['firstEventMs']<m['firstEventMs']<t['firstEventMs']]
            repairs=[m for m in intervening if m['deltaMakerAbsNet']<-EPS]
            wins={}
            for w in (1000,3000,5000,10000):
                mm=[m for m in makers if 0<t['firstEventMs']-m['lastEventMs']<=w]
                wins[str(w)]={'makerParents':len(mm),'makerShares':sum(m['shares'] for m in mm),'repairMakerParents':sum(m['deltaMakerAbsNet']<-EPS for m in mm),'repairMakerShares':sum(m['shares'] for m in mm if m['deltaMakerAbsNet']<-EPS)}
            nextm=[m for m in makers if m['firstEventMs']>t['lastEventMs']]; nm=min(nextm,key=lambda x:x['firstEventMs']) if nextm else None
            nextt=[x for x in takers if x['firstEventMs']>t['lastEventMs']];nt=min(nextt,key=lambda x:x['firstEventMs']) if nextt else None
            postrel=None
            if nm:postrel='SAME' if nm['side']==t['side'] else 'OPP'
            ratio=t['shares']/preabs if preabs>EPS and effect=='REPAIR_EFFECT' else None
            rows.append({'marketId':mid,'takerIndex':ti,'firstEventMs':t['firstEventMs'],'lastEventMs':t['lastEventMs'],'side':t['side'],'shares':t['shares'],'avgPrice':t['avgPrice'],'legs':t['legs'],'effect':effect,'preAbsNet':preabs,'postAbsNet':postabs,'dominantBefore':dominant,'repairSideOppositeDominant':None if effect!='REPAIR_EFFECT' or oppdom is None else t['side']==oppdom,'repairSharesToResidualAbsNet':ratio,'overRepairResidual':None if ratio is None else ratio>1.0+EPS,'latestMakerExpansionAt':ex['firstEventMs'] if ex else None,'msSinceLatestMakerExpansion':None if ex is None else t['firstEventMs']-ex['firstEventMs'],'makerRepairsSinceLatestExpansion':len(repairs),'makerRepairSharesSinceLatestExpansion':sum(m['shares'] for m in repairs),'hadMakerRepairSinceLatestExpansion':bool(repairs) if ex else None,'preWindows':wins,'nextMakerAt':nm['firstEventMs'] if nm else None,'nextMakerLagMs':None if nm is None else nm['firstEventMs']-t['lastEventMs'],'nextMakerSideRelation':postrel,'nextTakerAt':nt['firstEventMs'] if nt else None,'makerReentryBeforeNextTaker':None if nm is None else (nt is None or nm['firstEventMs']<nt['firstEventMs'])})
        if mi%50==0:print(json.dumps({'markets':mi,'of':len(mids),'takerParents':len(rows)}),flush=True)
    c.close();rep=[r for r in rows if r['effect']=='REPAIR_EFFECT'];add=[r for r in rows if r['effect']=='ADD_EFFECT'];build=[r for r in rows if r['effect']=='BUILD_FROM_FLAT']
    def rate(rr,pred):return sum(bool(pred(r)) for r in rr)/len(rr) if rr else None
    pre={}
    for w in ('1000','3000','5000','10000'):
        pre[w]={'anyMakerParentRate':rate(rows,lambda r:r['preWindows'][w]['makerParents']>0),'anyRepairMakerParentRate':rate(rows,lambda r:r['preWindows'][w]['repairMakerParents']>0),'repairEffectTakerAnyRepairMakerParentRate':rate(rep,lambda r:r['preWindows'][w]['repairMakerParents']>0)}
    post={}
    for w in (1000,3000,5000,10000):
        z=[r for r in rows if r['nextMakerLagMs'] is not None and r['nextMakerLagMs']<=w]
        post[str(w)]={'makerReentryRate':len(z)/len(rows) if rows else None,'sameRateAmongReentry':rate(z,lambda r:r['nextMakerSideRelation']=='SAME'),'oppRateAmongReentry':rate(z,lambda r:r['nextMakerSideRelation']=='OPP')}
    cnt=Counter(r['effect'] for r in rows)
    out={'version':'TARGET_ETH_FRESH150_ACTIVE_INTERVENTION_LIFECYCLE_V1','researchOnly':True,'coverage':{'markets':len(mids),'takerParents':len(rows),'marketMin':min(mids) if mids else None,'marketMax':max(mids) if mids else None},'effect':{'counts':dict(cnt),'rates':{k:v/len(rows) for k,v in cnt.items()} if rows else {}},'repairEffect':{'parents':len(rep),'oppositeDominantRate':rate(rep,lambda r:r['repairSideOppositeDominant'] is True),'sharesToResidualAbsNet':stats([r['repairSharesToResidualAbsNet'] for r in rep]),'overRepairResidualRate':rate(rep,lambda r:r['overRepairResidual'] is True),'hadMakerRepairSinceLatestExpansionRate':rate([r for r in rep if r['latestMakerExpansionAt'] is not None],lambda r:r['hadMakerRepairSinceLatestExpansion'] is True),'msSinceLatestMakerExpansion':stats([r['msSinceLatestMakerExpansion'] for r in rep])},'addEffect':{'parents':len(add),'hadMakerRepairSinceLatestExpansionRate':rate([r for r in add if r['latestMakerExpansionAt'] is not None],lambda r:r['hadMakerRepairSinceLatestExpansion'] is True)},'preTakerWindows':pre,'postTakerMakerReentry':{'nextMakerLagMs':stats([r['nextMakerLagMs'] for r in rows]),'makerReentryBeforeNextTakerRate':rate([r for r in rows if r['nextMakerAt'] is not None],lambda r:r['makerReentryBeforeNextTaker'] is True),'windows':post},'rows':rows,'boundary':['Fresh ETH actual Target Maker/Taker fills only.','Effect uses realized Taker parent only as offline teacher outcome; no winner/PnL.','Maker expansion/repair context uses actual Maker parent chronology, no fixed 10/18 unit assumptions.','BTC coordination evidence is not used numerically in this computation.']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in out.items() if k!='rows'},ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
