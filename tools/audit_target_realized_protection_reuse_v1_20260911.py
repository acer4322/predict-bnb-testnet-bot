"""Post-primary mechanism test: does strong-side Active spending track confirmed
intervening payoff improvement, rather than a fixed inventory amount? A reference
floor is not a private risk budget. No HFT, model or executable randomized policy.
"""
from pathlib import Path
from collections import defaultdict,Counter
import gzip,json,hashlib,math,random,statistics,time
import duckdb
from tools.build_target_route_mechanism_discrimination_v1_20260911 import OUT,TABLE,TABLE_SHA,sha,read,write,EPS

SCORE_SHA='8ef3b9601f08307384bf4115d3aaaf7e0fab3fa7015829a2447f4ad6400ef514'


def median(xs):return statistics.median(xs) if xs else None

def pb(x):return 0 if x<.3-EPS else 1 if x<.7-EPS else 2


def arithmetic_tests():
 # StrongUP portfolio, passive service and passive increase are simultaneously possible.
 up,down,cost=100.,60.,70.;f0=min(up,down)-cost
 up+=5;cost+=5*.6;down+=10;cost+=10*.3
 gain=min(up,down)-cost-f0;assert abs(gain-4.)<1e-10
 # Spending3 preserves previousFloor; spending5 goes past it, without net flipping.
 for cash,expected in [(3.,True),(5.,False)]:
  p=.5;q=cash/p;fafter=min(up+q,down)-cost-cash
  assert (fafter>=f0-1e-10)==expected
 # Bought protection may be largely Active, not a profitable passive-pair credit.
 makercontrib=-3.;otheractivecontrib=7.;assert makercontrib+otheractivecontrib==4.
 # Same paired inventory doesn't fix current risk capital.
 assert min(10,10)-8==2 and min(10,10)-12==-2
 return 5


def summarize(rs):
 pos=[r for r in rs if r['confirmedFloorGain']>EPS];cover=[r for r in pos if r['strongTakerCash']<=r['confirmedFloorGain']+1e-8]
 return dict(events=len(rs),markets=len({r['market_id'] for r in rs}),positiveConfirmedGain=len(pos),coveredByGain=len(cover),
   noPositiveGain=sum(r['confirmedFloorGain']<=EPS for r in rs),spendsMoreThanPositiveGain=len(pos)-len(cover),
   coveredAllFirstObserved=sum(r['allFirstObservedTaker'] for r in cover),
   positiveMakerContribution=sum(r['makerFloorContribution']>EPS for r in pos),
   positiveOppositeTakerContribution=sum(r['oppositeTakerFloorContribution']>EPS for r in pos),
   coveredAndNoOppositeTaker=sum(r['oppositeTakerFloorContribution']<=EPS for r in cover),
   gainMedian=median([r['confirmedFloorGain'] for r in pos]),spendOverGainMedian=median([r['strongTakerCash']/r['confirmedFloorGain'] for r in pos]),
   originalFloorStillNegative=sum(r['currentFloor']<-EPS for r in cover),
   completeFillBudgetKnown=False)


def permutation_check(rows):
 groups=defaultdict(list)
 for r in rows:groups[(r['market_id'],r['side'],pb(r['ask']))].append(r)
 groups={k:rs for k,rs in groups.items() if len(rs)>=3};flat=[r for rs in groups.values() for r in rs]
 actual=sum(r['confirmedFloorGain']>EPS and r['strongTakerCash']<=r['confirmedFloorGain']+EPS for r in flat)
 sims=[]
 for seed in range(50):
  rng=random.Random(2026091100+seed);covered=0
  for rs in groups.values():
   costs=[r['strongTakerCash'] for r in rs];rng.shuffle(costs)
   covered+=sum(r['confirmedFloorGain']>EPS and c<=r['confirmedFloorGain']+EPS for r,c in zip(rs,costs))
  sims.append(covered)
 return dict(eligibleEvents=len(flat),markets=len({r['market_id'] for r in flat}),groups=len(groups),
   observedCovered=actual,observedFraction=actual/len(flat) if flat else None,
   permutationMean=statistics.mean(sims)/len(flat) if flat else None,
   permutationMin=min(sims)/len(flat) if flat else None,permutationMax=max(sims)/len(flat) if flat else None,
   seeds=50,meaning='cost-to-state pairing null withinmarket/side/broadprice,not executable counterfactual or formal causality test')


def main():
 begin=time.monotonic();op=OUT/'PROTECTION_REUSE_AND_INDEPENDENT_VERIFICATION.json'
 if op.exists():raise FileExistsError(str(op))
 assert sha(OUT/'SCORE.json')==SCORE_SHA and sha(TABLE)==TABLE_SHA
 ntest=arithmetic_tests();events=[];exposure=[];verified=0;diffs=0;maxecon=0.;publicviolations=0
 c=duckdb.connect();c.execute('SET threads=1');c.execute("SET memory_limit='96MB'")
 for block in ['W1','W2','W3','W4','W5','W6']:
  d=read(OUT/(block+'.json'));p=Path(d['rows']['path']);assert sha(p)==d['rows']['sha256']
  rr=[];expanded=0
  with gzip.open(p,'rt',encoding='utf-8') as f:
   for line in f:
    expanded+=len(line.encode());assert expanded<48*1024**2;rr.append(json.loads(line))
  query=c.execute('SELECT * FROM read_parquet(?) WHERE block=?',[str(TABLE),block]);cols=[x[0] for x in query.description]
  original={(x[cols.index('market_id')],x[cols.index('t')],x[cols.index('side')]):dict(zip(cols,x)) for x in query.fetchall()}
  per=defaultdict(list)
  for r in rr:
   key=(r['market_id'],r['t'],r['side']);ref=original[key];verified+=1
   if any(r[k]!=ref[k] for k in cols):diffs+=1
   if r['z_public_sample_ms'] is not None:
    assert r['z_public_sample_ms']<r['t'] and r['z_public_archive_ms']<r['t']
   per[r['market_id']].append(r)
  for mid,rs in per.items():
   index={(r['t'],r['side']):r for r in rs};times=sorted({r['t'] for r in rs});start=times[0]
   for side in ['UP','DOWN']:
    opp='DOWN' if side=='UP' else 'UP';sgn=1 if side=='UP' else -1
    # Prefix quantities over whole-market seconds; all actual observed fills retained.
    invalid=[0];mf=[0.];tf=[0.]
    for t in times:
     r=index[t,side];o=index[t,opp]
     invalid.append(invalid[-1]+int(not(r['f_role']=='STRONG' and r['y_robust_whole_batch_sign'])))
     mf.append(mf[-1]+o['y_Mqty']-o['y_Mcash']-r['y_Mcash'])
     tf.append(tf[-1]+o['y_Tqty']-o['y_Tcash'])
    for t in times:
     r=index[t,side]
     if not(r['fresh3'] and r['f_role']=='STRONG' and r['z_reference_same_dominant'] and r['z_ref_pre_role']=='STRONG'):continue
     rt=r['z_ref_t'];lo=int((rt-start)//1000)+1;hi=int((t-start)//1000)
     if lo>=len(times) or hi<lo:continue
     reference=index[times[lo],side]
     # No unknown intrasecond ordering may change the dominant side after reference.
     stablepast=invalid[hi]==invalid[lo]
     if not stablepast:continue
     gain=r['f_floor']-reference['f_floor'];maker=mf[hi]-mf[lo];otherT=tf[hi]-tf[lo]
     err=abs(gain-maker-otherT);maxecon=max(maxecon,err);assert err<1e-6
     exposure.append(dict(asset=r['asset'],block=block,market_id=mid,t=t,side=side,hasT=r['y_hasT'],gainPositive=gain>EPS))
     if not(r['y_hasT'] and r['y_robust_whole_batch_sign']):continue
     events.append(dict(asset=r['asset'],block=block,market_id=mid,t=t,referenceT=rt,side=side,ask=r['f_ask'],referenceAsk=r['z_ref_ask'],
       referenceFloor=reference['f_floor'],currentFloor=r['f_floor'],confirmedFloorGain=gain,makerFloorContribution=maker,
       oppositeTakerFloorContribution=otherT,strongTakerCash=r['y_Tcash'],strongTakerQty=r['y_Tqty'],strongTakerPrice=r['y_Tvwap'],
       quantityDelta=r['z_delta_net_side'],allFirstObservedTaker=r['y_all_T_first_observed'] is True,
       protectedReferenceFloorAfterOnlyThisActive=(r['f_floor']-r['y_Tcash']>=reference['f_floor']-1e-8),
       sameSecondOtherFillsIgnoredForPrepaidCredit=True))
 c.close();assert verified==86400 and diffs==0
 groups={a:dict(all=summarize([r for r in events if r['asset']==a]),firstObservedOnly=summarize([r for r in events if r['asset']==a and r['allFirstObservedTaker']]),
     pairingNull=permutation_check([r for r in events if r['asset']==a]),
     perBlock={b:summarize([r for r in events if r['asset']==a and r['block']==b]) for b in ['W1','W2','W3','W4','W5','W6']}) for a in ['BTC','ETH']}
 earliest={}
 for a in ['BTC','ETH']:
  for kind,fn in [('covered',lambda r:r['confirmedFloorGain']>EPS and r['strongTakerCash']<=r['confirmedFloorGain']+EPS),
    ('overspent',lambda r:r['confirmedFloorGain']>EPS and r['strongTakerCash']>r['confirmedFloorGain']+EPS),
    ('noNewProtection',lambda r:r['confirmedFloorGain']<=EPS)]:
   candidates=[r for r in events if r['asset']==a and fn(r)]
   if candidates:earliest[a+'_'+kind]=min(candidates,key=lambda r:(r['t'],r['market_id']))
 d=dict(version='PROTECTION_REUSE_POSTPRIMARY_EXPLORATION_V1',primaryScoreSha256=SCORE_SHA,groups=groups,events=events,
  earliestWitnesses=earliest,uniqueEvents=len(events),originalRowsVerified=verified,originalColumnDifferences=diffs,
  payoffDecompositionMaxError=maxecon,arithmeticTests=ntest,newHFT=0,newTraining=0,seconds=time.monotonic()-begin,
  featureSemantics='previouspostActiveFloor is a revealedreference not a privatebudget; positivegain may be financed by previousoppositeTaker,not freeprofit',
  selection='both reference and current side strong; allintervening/currentseconds order-invariant no-netcross; allotherobservations remain source',
  permutationSemantics='50costreassignments preserve market/side/pricegroups; no executable/HFT meaning, descriptive pairingcheck only',
  limits=['noncausal,alreadyobservedsources','sampling on currentfilled strongTaker excludes decisions/unfilledorders','same-second concurrentfutureservice excluded from priorcredit','coveredbyexistinggain can occur incidentally ifgain is large','refFloor may legitimately be spent,soovershoot is not automatically a bug or badtrade'])
 write(op,d)
 print(json.dumps(dict(output=str(op),bytes=op.stat().st_size,sha256=sha(op),groups=groups,witnesses=earliest,
   originalRowsVerified=verified,originalDifferences=diffs,payoffError=maxecon,seconds=d['seconds']),ensure_ascii=False))


if __name__=='__main__':main()
