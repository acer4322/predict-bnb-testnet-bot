"""Post-primary descriptive chain and passive-drift sensitivity. No training.
Opposing realized Maker drift is a candidate heuristic, not a recovered target.
"""
from pathlib import Path
from collections import Counter,defaultdict
import gzip,json,math,statistics,time
from tools.audit_target_active_passive_chain_v1_20260910 import BASE,OUT,SRC,SRC_SHA,sha,write,EPS,state,effect,oid,sign


def median(xs):return statistics.median(xs) if xs else None

def mean(xs):return statistics.mean(xs) if xs else None

def mr(rows,fn):
 by=defaultdict(list)
 for r in rows:by[r['market_id']].append(float(fn(r)))
 return dict(events=len(rows),markets=len(by),pooled=mean([float(fn(r)) for r in rows]),equalMarket=mean([mean(x) for x in by.values()]))


def read_gz(p,cap=32*1024**2):
 out=[];expanded=0
 with gzip.open(p,'rt',encoding='utf-8') as f:
  for line in f:
   expanded+=len(line.encode());assert expanded<cap;out.append(json.loads(line))
 return out


def drift_market(raw):
 m=raw['identity'];events=raw['events'];by=defaultdict(list);first={}
 for e in events:by[e['event_ms']].append(e);first[oid(e)]=min(e['event_ms'],first.get(oid(e),e['event_ms']))
 q={r:[0.,0.] for r in ('MAKER','TAKER')};c={r:[0.,0.] for r in q};last=None;maker_n=0;rows=[];status=Counter()
 for t,es in sorted(by.items()):
  pre=state(q,c);ms=[e for e in es if e['role']=='MAKER'];ts=[e for e in es if e['role']=='TAKER']
  if ts and not ms:
   ef=effect(pre,ts);single=ef['side']!='BOTH'
   if pre['gross']<=EPS:status['SEED']+=1
   elif not single:status['BOTH_SIDES']+=1
   elif last is None:status['NO_PREVIOUS_TAKER']+=1
   elif maker_n==0:status['NO_INTERVENING_MAKER']+=1
   else:
    dn=pre['net']-last['net'];status['WITH_PRIOR_MAKER']+=1
    if abs(dn)<=EPS:status['ZERO_PASSIVE_NET_DRIFT']+=1
    actual=1 if ef['side']=='UP' else -1
    candidate=dict(totalZero=-sign(pre['net']),makerZero=-sign(pre['maker_net']),opposePassiveDrift=-sign(dn),repeatLastTaker=last['activeFlowSign'])
    postnet=pre['net']+actual*ef['qty']
    rows.append(dict(asset=m['asset'],block=m['block'],market_id=m['market_id'],t=t,
      allFirstObservedTaker=all(first[oid(e)]==t for e in ts),effect=ef['geometry'],actualSide=ef['side'],actualSign=actual,
      candidateSigns=candidate,currentNet=pre['net'],previousPostTakerNet=last['net'],makerNet=pre['maker_net'],
      passiveNetDrift=dn,passiveEvents=maker_n,activeQty=ef['qty'],activePrice=ef['price'],
      qtyToDrift=ef['qty']/abs(dn) if abs(dn)>EPS else None,
      movedCloserToPreviousNet=abs(postnet-last['net'])<abs(dn)-EPS,
      wholeFloorImprovement=ef['deltaFloor'],gross=pre['gross']))
  for e in es:
   i=0 if e['side']=='UP' else 1;q[e['role']][i]+=e['shares'];c[e['role']][i]+=e['shares']*e['price']
  post=state(q,c)
  if ts:
   last=dict(net=post['net'],activeFlowSign=sign(sum(e['shares']*(1 if e['side']=='UP' else -1) for e in ts)))
   maker_n=0
  else:maker_n+=1
 ref=raw['result'];errs=[abs(post['up']-ref['up_position_shares']),abs(post['down']-ref['down_position_shares']),abs(post['cost']-ref['buy_notional_usdt'])]
 # Independent direct sums, not relying on classification implementation.
 for side,name in [('UP','up'),('DOWN','down')]:errs.append(abs(math.fsum(e['shares'] for e in events if e['side']==side)-post[name]))
 errs.append(abs(math.fsum(e['shares']*e['price'] for e in events)-post['cost']))
 assert max(errs)<1e-6
 return rows,dict(asset=m['asset'],market_id=m['market_id'],status=dict(status),maxError=max(errs))


def drift_summary(rows):
 scores={}
 for name in ['totalZero','makerZero','opposePassiveDrift','repeatLastTaker']:
  rs=[r for r in rows if r['candidateSigns'][name]!=0]
  scores[name]=mr(rs,lambda r:r['candidateSigns'][name]==r['actualSign'])
 conflicts={}
 for other in ['totalZero','makerZero','repeatLastTaker']:
  rs=[r for r in rows if r['candidateSigns']['opposePassiveDrift'] and r['candidateSigns'][other] and r['candidateSigns']['opposePassiveDrift']!=r['candidateSigns'][other]]
  conflicts[other]=dict(mr(rs,lambda r:r['candidateSigns']['opposePassiveDrift']==r['actualSign']),
    followsDrift=sum(r['candidateSigns']['opposePassiveDrift']==r['actualSign'] for r in rs),
    followsOther=sum(r['candidateSigns'][other]==r['actualSign'] for r in rs))
 closes=[r for r in rows if r['candidateSigns']['opposePassiveDrift'] and r['candidateSigns']['opposePassiveDrift']==r['actualSign']]
 return dict(eligible=len(rows),markets=len({r['market_id'] for r in rows}),candidateScores=scores,disagreements=conflicts,
   amongOpposingDrift=dict(events=len(closes),movedCloser=sum(r['movedCloserToPreviousNet'] for r in closes),
      qtyToDriftMedian=median([r['qtyToDrift'] for r in closes if r['qtyToDrift'] is not None])),
   scope='executed-side consistency of hypotheses on observable intervals, not forecast accuracy on all decisions/heldout markets')


def main():
 start=time.monotonic();op=OUT/'SENSITIVITY_AND_VERIFICATION.json'
 if op.exists():raise FileExistsError(str(op))
 score=OUT/'SCORE.json';assert sha(score)=='d9d874fc309d04eda1898eb6ebc1734100db2143550841c85e058eec8fe3cf6e';d=json.loads(score.read_text(encoding='utf-8-sig'))
 path=Path(d['artifacts'][0]['path']);assert sha(path)==d['artifacts'][0]['sha256'];anchors=read_gz(path)
 assert len(anchors)==1839 and len({(a['market_id'],a['t']) for a in anchors})==1839
 primary={};witness={}
 for asset in ['BTC','ETH']:
  rs=[a for a in anchors if a['asset']==asset and not a['seed']];z={}
  improved=[a for a in rs if a['active']['geometry']=='SERVICE_EFFECT' and a['passiveProgress']=='IMPROVED']
  z['serviceAfterPassiveImprovement']=dict(events=len(improved),markets=len({a['market_id'] for a in improved}),
    blocks=sorted({a['block'] for a in improved}),
    keepsResidual=sum(abs(a['post']['net'])>EPS for a in improved),
    fullPortfolioStillNegative=sum(a['pre']['floor']<-EPS for a in improved))
  for kind in ['SERVICE_EFFECT','EXPANSION_EFFECT']:
   aa=[a for a in rs if a['active']['geometry']==kind and a['firstPassive']]
   table=Counter((a['firstPassive']['oldStateSameFillGeometry'],a['firstPassive']['geometry']) for a in aa)
   co=[a for a in aa if a['lastPassive'] and a['lastPassive']['geometry']==kind and a['firstPassive']['geometry']==kind
      and a['active']['side']!='BOTH' and a['lastPassive']['side']==a['active']['side']==a['firstPassive']['side']]
   z[kind]=dict(eligible=len(aa),sameEffectRate=mr(aa,lambda a:a['firstPassive']['geometry']==kind),
      effectMatrix=[dict(oldInventorySameFill=k[0],actualPostInventory=k[1],n=v) for k,v in table.items()],
      sameSideThreeStepChains=len(co),chainMarkets=len({a['market_id'] for a in co}),
      chainByBlock=dict(Counter(a['block'] for a in co)),firstAfterActiveMedianSeconds=median([a['firstPassive']['elapsedSeconds'] for a in aa]))
   if co:witness[asset+'_'+kind]=min(co,key=lambda a:a['t'])
   switched=[a for a in aa if a['firstPassive']['geometry']!=kind and a['firstPassive']['geometry'] in ['SERVICE_EFFECT','EXPANSION_EFFECT']]
   z[kind]['apparentAlternation']=dict(events=len(switched),wouldHaveHadSameTypeWithoutActive=sum(a['firstPassive']['oldStateSameFillGeometry']==kind for a in switched),
     actualSideUnchangedFromLastMaker=sum(bool(a['lastPassive']) and a['lastPassive']['side']!='BOTH' and a['lastPassive']['side']==a['firstPassive']['side'] for a in switched))
  primary[asset]=z
 for a in anchors:
  assert sum(a['nextEvent']==c for c in ['MAKER','TAKER','MIXED','NONE'])==1
  assert bool(a['firstPassive'])==(a['nextEvent']=='MAKER')
  if a['firstPassive']:
   p=a['firstPassive'];assert p['t']>a['t'] and p['knownOldOrderQty']<=p['qty']+EPS
   assert abs(p['deltaFloor']-a['afterWindows']['1']['deltaFloor'])<1e-7
  for k,s in a['afterWindows'].items():
   assert s['events']==int(k) and s['firstTime']>a['t'] and a['cleanPostPassiveEvents']>=int(k)
  for k,s in a['beforeWindows'].items():assert s['lastTime']<a['t'] and s['events']==int(k)
 # Source-only follow-up of directional alternatives.
 assert sha(SRC)==SRC_SHA;source=json.loads(SRC.read_text(encoding='utf-8-sig'));drifts=[];checks=[]
 for inp in source['inputs']:
  assert sha(inp['source']['path'])==inp['source']['sha256']
  for raw in read_gz(inp['source']['path']):
   r,c=drift_market(raw);drifts+=r;checks.append(c)
 assert len(checks)==144
 byasset={a:dict(all=drift_summary([r for r in drifts if r['asset']==a]),
               onlyFirstObservedTaker=drift_summary([r for r in drifts if r['asset']==a and r['allFirstObservedTaker']])) for a in ['BTC','ETH']}
 perblock={a+'_'+b:drift_summary([r for r in drifts if r['asset']==a and r['block']==b]) for a in ['BTC','ETH'] for b in ['W1','W2','W3','W4','W5','W6']}
 dp=OUT/'PASSIVE_DRIFT_HYPOTHESIS_ROWS.jsonl.gz'
 with gzip.open(dp,'wt',encoding='utf-8',compresslevel=3) as f:
  for r in drifts:f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
 wp=OUT/'EARLIEST_CHAIN_WITNESSES.json';write(wp,witness)
 result=dict(sourceScoreSha256=sha(score),primaryChainSensitivity=primary,driftHypotheses=byasset,
   driftByBlock=perblock,checks=checks,maxIndependentError=max(c['maxError'] for c in checks),
   primaryAnchorChecks='1839 unique anchors; before/after time and competing-event boundaries checked',
   posthocDriftHypothesis=True,witnessSelection='earliest in each asset and economic class, not maximal PnL; exploratory witnesses',
   sources=dict(driftRows=dict(path=str(dp),sha256=sha(dp),bytes=dp.stat().st_size),witnesses=dict(path=str(wp),sha256=sha(wp))),
   caveats=['Directional candidate scores only on selected filled intervals with passive activity. Actual decisions/HOLD unknown.',
    'Restore-last-postActive-net heuristic has no proof that oldnet is desirednet.',
    'Same-side multi-route chains are not automatic evidence of shared objective; directional persistence and execution availability remain explanations.',
    'Regime/side/price and same-second attribution must be checked before claiming unchanged active feedback laws.',
    'No public quote/pending/microstructure matching or causal/pricing/profitability model performed.'],newHFT=0,newTraining=0,seconds=time.monotonic()-start)
 write(op,result)
 print(json.dumps(dict(output=str(op),bytes=op.stat().st_size,sha256=sha(op),primary=primary,drift=byasset,
   driftPerBlock=[dict(block=k,n=v['eligible'],disagreements=v['disagreements']) for k,v in perblock.items()],
   error=result['maxIndependentError'],seconds=result['seconds']),ensure_ascii=False))


if __name__=='__main__':main()
