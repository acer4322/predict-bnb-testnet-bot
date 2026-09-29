from __future__ import annotations
import json, math, sqlite3
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/research/r4_v0/p0_provenance_v1/target_eth_btc_strategy_compare_snapshot_v1.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_BTC_ETH_DIRECTIONAL_THESIS_PASSIVE_REPAIR_V1.json'
EPS=1e-9
REPAIR_WINDOW=30_000
REEXPAND_WINDOW=30_000


def pct(vals,p):
    a=sorted(float(v) for v in vals if v is not None and math.isfinite(float(v)))
    if not a:return None
    if len(a)==1:return a[0]
    x=(len(a)-1)*p; lo=int(math.floor(x)); hi=int(math.ceil(x)); w=x-lo
    return a[lo] if lo==hi else a[lo]*(1-w)+a[hi]*w

def stats(vals):
    a=[float(v) for v in vals if v is not None and math.isfinite(float(v))]
    return {'n':len(a),'mean':sum(a)/len(a) if a else None,'median':pct(a,.5),'p25':pct(a,.25),'p75':pct(a,.75),'p90':pct(a,.9),'min':min(a) if a else None,'max':max(a) if a else None}

def rate(rows,key):
    a=[r for r in rows if r.get(key) is not None]
    return sum(bool(r[key]) for r in a)/len(a) if a else None

def side_qty(U,D,side): return U if side=='UP' else D

def payoff(U,D,C,side): return (U-C) if side=='UP' else (D-C)

def other(side): return 'DOWN' if side=='UP' else 'UP'

def fifo_pair(dirlegs, repairlegs):
    # dirlegs / repairlegs: [{'q','p'}]; only repair-effective qty enters repairlegs.
    ds=[[float(x['q']),float(x['p'])] for x in dirlegs]
    rs=[[float(x['q']),float(x['p'])] for x in repairlegs]
    i=j=0; mq=0.0; sumpair=0.0; dircost=0.0; repcost=0.0
    while i<len(ds) and j<len(rs):
        q=min(ds[i][0],rs[j][0])
        if q<=EPS: break
        mq+=q; sumpair+=q*(ds[i][1]+rs[j][1]); dircost+=q*ds[i][1]; repcost+=q*rs[j][1]
        ds[i][0]-=q; rs[j][0]-=q
        if ds[i][0]<=EPS:i+=1
        if rs[j][0]<=EPS:j+=1
    return {'matchedQty':mq,'pairSumWavg':sumpair/mq if mq>EPS else None,'matchedDirCost':dircost,'matchedRepairCost':repcost}

def summarize(rows):
    if not rows:return {'n':0}
    repaired=[r for r in rows if r['hasPassiveRepair']]
    residual=[r for r in repaired if (r.get('residualDesiredQty') or 0)>EPS]
    return {
      'n':len(rows),'markets':len(set(r['marketId'] for r in rows)),
      'passiveRepairWithin30sRate':len(repaired)/len(rows),
      'sameDirectionReexpandAfterRepairRate':rate(repaired,'sameDirectionReexpand'),
      'repairThenReexpandSandwichRateAll':sum(r['hasPassiveRepair'] and r['sameDirectionReexpand'] for r in rows)/len(rows),
      'repairImprovesFloorRate':rate(repaired,'repairImprovesFloor'),
      'repairReachesNonLossFloorRate':rate(repaired,'postRepairSafe'),
      'repairPreservesOriginalPositivePayoffRate':rate(repaired,'postRepairOriginalPositive'),
      'repairLeavesOriginalDirectionalResidualRate':rate(repaired,'postRepairOriginalStillDominant'),
      'originalSideWinnerRate':rate(rows,'originalSideWon'),
      'originalSideWinnerRateRepaired':rate(repaired,'originalSideWon'),
      'directionQty':stats([r['directionQty'] for r in rows]),
      'directionAvgPrice':stats([r['directionAvgPrice'] for r in rows]),
      'matchedRepairFraction':stats([r['matchedRepairFraction'] for r in repaired]),
      'pairSumWavg':stats([r['pairSumWavg'] for r in repaired]),
      'pairSumLe1Rate':sum((r['pairSumWavg'] is not None and r['pairSumWavg']<=1+EPS) for r in repaired)/len(repaired) if repaired else None,
      'pairSumLe099Rate':sum((r['pairSumWavg'] is not None and r['pairSumWavg']<=.99+EPS) for r in repaired)/len(repaired) if repaired else None,
      'pairEdgePerMatchedShare':stats([(1-r['pairSumWavg']) for r in repaired if r['pairSumWavg'] is not None]),
      'residualDesiredQty':stats([r['residualDesiredQty'] for r in repaired]),
      'residualDesiredRate':len(residual)/len(repaired) if repaired else None,
      'effectiveResidualCostPerShare':stats([r['effectiveResidualCostPerShare'] for r in residual]),
      'effectiveResidualCostBelowDirectionAvgRate':sum(r['effectiveResidualCostPerShare'] < r['directionAvgPrice']-EPS for r in residual)/len(residual) if residual else None,
      'effectiveResidualFreeOrCreditRate':sum(r['effectiveResidualCostPerShare']<=EPS for r in residual)/len(residual) if residual else None,
      'floorDeltaFromRepair':stats([r['postRepairFloor']-r['preRepairFloor'] for r in repaired]),
      'originalPayoffAfterRepair':stats([r['postRepairOriginalPayoff'] for r in repaired]),
      'oppositePayoffAfterRepair':stats([r['postRepairOppPayoff'] for r in repaired]),
      'gapAfterRepair':stats([r['postRepairGap'] for r in repaired]),
      'timeDirectionStartToRepairMs':stats([r['firstRepairAt']-r['startAt'] for r in repaired]),
      'timeRepairToReexpandMs':stats([r['reexpandAt']-r['lastRepairAt'] for r in repaired if r['sameDirectionReexpand']]),
    }

def transition_summary(rows):
    if not rows:return {'n':0}
    return {
      'n':len(rows),'markets':len(set(r['marketId'] for r in rows)),
      'hasPriorDirectional30sRate':rate(rows,'priorDirectional30s'),
      'hasFutureSameDirection30sRate':rate(rows,'futureSameDirection30s'),
      'sandwichedDirectionRepairDirectionRate':sum(r['priorDirectional30s'] and r['futureSameDirection30s'] for r in rows)/len(rows),
      'repairFirstOnlyRate':sum((not r['priorDirectional30s']) and r['futureSameDirection30s'] for r in rows)/len(rows),
      'directionFirstOnlyRate':sum(r['priorDirectional30s'] and (not r['futureSameDirection30s']) for r in rows)/len(rows),
      'neitherRate':sum((not r['priorDirectional30s']) and (not r['futureSameDirection30s']) for r in rows)/len(rows),
    }

def main():
    con=sqlite3.connect(DB)
    winners={(a,int(m)):str(w).upper() for m,a,w in con.execute('select market_id,asset,winner from target_market_results where winner is not null')}
    evs=defaultdict(list)
    for a,m,role,side,t,p,q,pid in con.execute('''select asset,market_id,role,side,first_event_ms,average_price,shares,parent_id from target_parent_orders where average_price is not null and shares>0 order by asset,market_id,first_event_ms,parent_id'''):
        evs[(str(a),int(m))].append({'role':str(role).upper(),'side':str(side).upper(),'t':int(t),'p':float(p),'q':float(q),'pid':str(pid)})
    con.close()
    mids=defaultdict(list)
    for a,m in evs:mids[a].append(m)
    split={}
    for a,ms in mids.items():
        xs=sorted(set(ms)); n=len(xs)
        for i,m in enumerate(xs): split[(a,m)]='TRAIN60' if (i+1)/n<=.6 else ('VALID20' if (i+1)/n<=.8 else 'TEST20')

    classified={}
    for key,xs in evs.items():
        U=D=C=0.0; arr=[]
        for e in xs:
            preU,preD,preC=U,D,C; preGap=abs(U-D); preFloor=min(U-C,D-C); preBest=max(U-C,D-C)
            if preU>preD+EPS: dom='UP'; weak='DOWN'
            elif preD>preU+EPS: dom='DOWN'; weak='UP'
            else: dom=weak=None
            if e['side']=='UP': U+=e['q']
            else: D+=e['q']
            C+=e['p']*e['q']
            postGap=abs(U-D); postFloor=min(U-C,D-C); postBest=max(U-C,D-C)
            isDir=((preGap<=EPS and postGap>EPS) or (dom is not None and e['side']==dom and postGap>preGap+EPS))
            repairEff=0.0
            if weak is not None and e['side']==weak:
                repairEff=min(e['q'],preGap)
            isPassiveRepair=(e['role']=='MAKER' and repairEff>EPS)
            z=dict(e); z.update({'preU':preU,'preD':preD,'preC':preC,'preGap':preGap,'preFloor':preFloor,'preBest':preBest,
                                'dominant':dom,'weak':weak,'postU':U,'postD':D,'postC':C,'postGap':postGap,'postFloor':postFloor,'postBest':postBest,
                                'isDirectionalExpansion':isDir,'repairEffectiveQty':repairEff,'isPassiveRepair':isPassiveRepair})
            arr.append(z)
        classified[key]=arr

    episodes=[]; repairEvents=[]
    for (a,m),arr in classified.items():
        # Repair-event local sandwich audit.
        dir_idxs=[i for i,e in enumerate(arr) if e['isDirectionalExpansion']]
        for i,e in enumerate(arr):
            if not e['isPassiveRepair'] or e['dominant'] is None: continue
            orig=e['dominant']; t=e['t']
            prior=any(arr[j]['side']==orig and t-arr[j]['t']<=REPAIR_WINDOW for j in dir_idxs if j<i)
            future=any(arr[j]['side']==orig and arr[j]['t']-t<=REEXPAND_WINDOW for j in dir_idxs if j>i)
            repairEvents.append({'asset':a,'marketId':m,'split':split[(a,m)],'t':t,'side':e['side'],'originalDominant':orig,
                                 'priorDirectional30s':prior,'futureSameDirection30s':future})

        # Non-overlapping directional build -> passive repair -> optional same-direction re-expand episodes.
        i=0
        while i<len(arr):
            e=arr[i]
            if not e['isDirectionalExpansion']:
                i+=1; continue
            dirside=e['side']; role=e['role']; start=i; dirlegs=[]; lastdir=i
            j=i
            while j<len(arr):
                z=arr[j]
                if z['t']-arr[start]['t']>REPAIR_WINDOW: break
                if z['isDirectionalExpansion'] and z['side']==dirside:
                    dirlegs.append({'q':z['q'],'p':z['p'],'idx':j}); lastdir=j; j+=1; continue
                if z['isPassiveRepair'] and z['side']==other(dirside) and side_qty(z['preU'],z['preD'],dirside)>side_qty(z['preU'],z['preD'],other(dirside))+EPS:
                    break
                if z['isDirectionalExpansion'] and z['side']!=dirside: break
                j+=1
            if j>=len(arr) or arr[j]['t']-arr[start]['t']>REPAIR_WINDOW or not (arr[j]['isPassiveRepair'] and arr[j]['side']==other(dirside)):
                # unrepaired directional block; consume through last same-side directional event to avoid duplicate block starts.
                dqty=sum(x['q'] for x in dirlegs); dcost=sum(x['q']*x['p'] for x in dirlegs)
                if dqty>EPS:
                    w=winners.get((a,m))
                    episodes.append({'asset':a,'marketId':m,'split':split[(a,m)],'directionRole':role,'directionSide':dirside,
                                     'startAt':arr[start]['t'],'directionQty':dqty,'directionAvgPrice':dcost/dqty,
                                     'hasPassiveRepair':False,'sameDirectionReexpand':False,'originalSideWon':(w==dirside) if w in ('UP','DOWN') else None})
                i=max(lastdir+1,i+1); continue
            # repair block starts at j. accumulate repair-effective qty until first original-side directional reexpand or 30s after first repair.
            firstRepair=j; repairlegs=[]; lastRepair=j; k=j; reexpand=None
            while k<len(arr):
                z=arr[k]
                if z['t']-arr[firstRepair]['t']>REEXPAND_WINDOW: break
                if k>j and z['isDirectionalExpansion'] and z['side']==dirside:
                    reexpand=k; break
                if z['isPassiveRepair'] and z['side']==other(dirside) and side_qty(z['preU'],z['preD'],dirside)>side_qty(z['preU'],z['preD'],other(dirside))+EPS:
                    qeff=min(z['q'],side_qty(z['preU'],z['preD'],dirside)-side_qty(z['preU'],z['preD'],other(dirside)))
                    if qeff>EPS:
                        repairlegs.append({'q':qeff,'p':z['p'],'idx':k}); lastRepair=k
                if z['isDirectionalExpansion'] and z['side']!=dirside: break
                k+=1
            dqty=sum(x['q'] for x in dirlegs); dcost=sum(x['q']*x['p'] for x in dirlegs)
            pair=fifo_pair(dirlegs,repairlegs); mq=pair['matchedQty']; residual=max(0.0,dqty-mq)
            matchedRepCost=pair['matchedRepairCost']
            packageNetCost=dcost+matchedRepCost-mq
            effResidual=packageNetCost/residual if residual>EPS else None
            preRepair=arr[lastdir]; postRepair=arr[lastRepair]
            w=winners.get((a,m))
            episodes.append({'asset':a,'marketId':m,'split':split[(a,m)],'directionRole':role,'directionSide':dirside,
                'startAt':arr[start]['t'],'lastDirectionAt':arr[lastdir]['t'],'directionQty':dqty,'directionAvgPrice':dcost/dqty if dqty>EPS else None,
                'hasPassiveRepair':True,'firstRepairAt':arr[firstRepair]['t'],'lastRepairAt':arr[lastRepair]['t'],'repairParents':len(repairlegs),
                'matchedRepairQty':mq,'matchedRepairFraction':mq/dqty if dqty>EPS else None,'pairSumWavg':pair['pairSumWavg'],
                'residualDesiredQty':residual,'marginalPackageNetCostAfterGuaranteedPairs':packageNetCost,'effectiveResidualCostPerShare':effResidual,
                'preRepairFloor':preRepair['postFloor'],'postRepairFloor':postRepair['postFloor'],'repairImprovesFloor':postRepair['postFloor']>preRepair['postFloor']+EPS,
                'postRepairSafe':postRepair['postFloor']>=-EPS,'postRepairOriginalPayoff':payoff(postRepair['postU'],postRepair['postD'],postRepair['postC'],dirside),
                'postRepairOppPayoff':payoff(postRepair['postU'],postRepair['postD'],postRepair['postC'],other(dirside)),
                'postRepairOriginalPositive':payoff(postRepair['postU'],postRepair['postD'],postRepair['postC'],dirside)>EPS,
                'postRepairOriginalStillDominant':side_qty(postRepair['postU'],postRepair['postD'],dirside)>side_qty(postRepair['postU'],postRepair['postD'],other(dirside))+EPS,
                'postRepairGap':postRepair['postGap'],'sameDirectionReexpand':reexpand is not None,'reexpandAt':arr[reexpand]['t'] if reexpand is not None else None,
                'reexpandPrice':arr[reexpand]['p'] if reexpand is not None else None,'reexpandQty':arr[reexpand]['q'] if reexpand is not None else None,
                'originalSideWon':(w==dirside) if w in ('UP','DOWN') else None})
            # consume through repair block, but leave reexpand to start a new directional episode.
            i=(reexpand if reexpand is not None else max(lastRepair+1,lastdir+1))

    out={'version':'TARGET_BTC_ETH_DIRECTIONAL_THESIS_PASSIVE_REPAIR_V1','researchOnly':True,'actionAuthority':False,
         'preRegistration':'data/research/r4_v0/p0_provenance_v1/TARGET_BTC_ETH_DIRECTIONAL_THESIS_PASSIVE_REPAIR_V1_PREREGISTERED.json',
         'coverage':{'episodes':len(episodes),'episodeMarkets':len(set((r['asset'],r['marketId']) for r in episodes)),'passiveRepairEvents':len(repairEvents)},
         'groups':{},'repairSandwich':{},
         'notes':['Directional expansion is an observational strict-past geometry proxy, not a claimed hidden Target direction signal.',
                  'Matched pair economics greedily pair episode direction fills with later opposite Maker repair-effective quantity; this is a marginal package diagnostic, not literal lot ownership.',
                  'Winner alignment is ex-post scoring only and cannot be a runtime feature.']}
    for a in ('BTC','ETH'):
        for role in ('MAKER','TAKER'):
            g=[r for r in episodes if r['asset']==a and r['directionRole']==role]
            out['groups'][f'{a}_{role}']={'ALL':summarize(g),'TEST20':summarize([r for r in g if r['split']=='TEST20'])}
        rr=[r for r in repairEvents if r['asset']==a]
        out['repairSandwich'][a]={'ALL':transition_summary(rr),'TEST20':transition_summary([r for r in rr if r['split']=='TEST20'])}
    OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
    brief={}
    for k,v in out['groups'].items():
        s=v['TEST20'];brief[k]={x:s.get(x) for x in ['n','markets','passiveRepairWithin30sRate','sameDirectionReexpandAfterRepairRate','repairThenReexpandSandwichRateAll','pairSumLe1Rate','pairSumLe099Rate','residualDesiredRate','effectiveResidualCostBelowDirectionAvgRate','effectiveResidualFreeOrCreditRate','repairImprovesFloorRate','repairReachesNonLossFloorRate','repairPreservesOriginalPositivePayoffRate','repairLeavesOriginalDirectionalResidualRate','originalSideWinnerRate','originalSideWinnerRateRepaired']}
        brief[k]['matchedRepairFractionMed']=s.get('matchedRepairFraction',{}).get('median');brief[k]['pairSumMed']=s.get('pairSumWavg',{}).get('median');brief[k]['effectiveResidualCostMed']=s.get('effectiveResidualCostPerShare',{}).get('median')
    print(json.dumps({'out':str(OUT.relative_to(ROOT)),'coverage':out['coverage'],'brief':brief,'repairSandwichTEST20':{a:v['TEST20'] for a,v in out['repairSandwich'].items()}},indent=2))

if __name__=='__main__': main()
