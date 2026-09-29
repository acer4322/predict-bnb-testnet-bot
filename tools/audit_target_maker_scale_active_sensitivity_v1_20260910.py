"""Sensitivity of size/Active audit to seeds and same-second Maker/Taker mixing.
No future-derived features, original qty inference, policy, HFT, model or new data.
"""
import gzip,json,hashlib,math,statistics
from collections import Counter,defaultdict
from tools.audit_target_maker_scale_active_feedback_v1_20260910 import SOURCE,SOURCE_SHA,OUT,EPS,sha,write,analyze,median


def main():
 score=OUT/'SCORE.json';assert sha(score)=='4ff1c89d1b3dd1e3e431f8f4721457ebb986ae50c69e84dffb32d192ac82c7f0'
 primary=json.loads(score.read_text(encoding='utf-8-sig'));source=json.loads(SOURCE.read_text(encoding='utf-8-sig'));assert sha(SOURCE)==SOURCE_SHA
 op=OUT/'SEED_MIXING_AND_STATE_CHECK.json'
 if op.exists():raise FileExistsError(str(op))
 pure=[];mixed=[];checks=[];firstwitness={}
 for x in source['inputs']:
  assert sha(x['source']['path'])==x['source']['sha256']
  with gzip.open(x['source']['path'],'rt',encoding='utf-8') as f:
   total=0
   for line in f:
    total+=len(line.encode());assert total<32*1024**2;r=json.loads(line)
    ms,fs,ps,mx=analyze(r);pure+=ps;mixed+=mx
    ev=r['events'];ref=r['result'];up=math.fsum(e['shares'] for e in ev if e['side']=='UP');down=math.fsum(e['shares'] for e in ev if e['side']=='DOWN');cash=math.fsum(e['shares']*e['price'] for e in ev)
    err=max(abs(up-ref['up_position_shares']),abs(down-ref['down_position_shares']),abs(cash-ref['buy_notional_usdt']))
    assert err<1e-6;checks.append(dict(asset=ms['asset'],market_id=ms['market_id'],error=err))
    for e in ps:
     if e['geometry']=='SERVICE_EFFECT' and e['side']!='BOTH' and e['maker_total_weak_disagree']:
      firstwitness.setdefault(e['asset'],{k:e[k] for k in ('asset','block','market_id','t','side','qty','price','deltaFloor','pre','weak_total','weak_maker')})
 groups={}
 for asset in ('BTC','ETH'):
  for block in ['W1','W2','W3','W4','W5','W6']:
   match=lambda r:r['asset']==asset and r['block']==block
   ps=[r for r in pure if match(r)];xs=[r for r in mixed if match(r)];ad=[r for r in ps if r['geometry']=='EXPANSION_EFFECT' and not r['seed']]
   sv=[r for r in ps if r['geometry']=='SERVICE_EFFECT'];w=[r for r in sv if r['qty_to_total_gap'] is not None];wm=defaultdict(list)
   for r in w:wm[r['market_id']].append(r['qty_to_total_gap'])
   nonseedxs=[r for r in xs if not r['seed']]
   ta=sum(r['geometry']=='EXPANSION_EFFECT' for r in nonseedxs);ma=sum(r['makerFirstGeometry']=='EXPANSION_EFFECT' for r in nonseedxs)
   both=sum(r['geometry']==r['makerFirstGeometry']=='EXPANSION_EFFECT' for r in nonseedxs)
   sourcepositive=[r for r in ad if r['pre']['maker_pair_margin']>EPS]
   groups[asset+'_'+block]=dict(pureActive=len(ps),mixed=len(xs),mixedShare=len(xs)/(len(ps)+len(xs)),
     pureExpansionAll=sum(r['geometry']=='EXPANSION_EFFECT' for r in ps),pureExpansionExcludingInitialSeed=len(ad),
     nonseedExpansionFirstOrderIds=sum(r['firstObservedTakerOrders'] for r in ad),
     nonseedExpansionPositivePair=len(sourcepositive),nonseedExpansionPairNonPositive=sum(r['pre']['maker_paired_qty']>EPS and r['pre']['maker_pair_margin']<=EPS for r in ad),
     nonseedExpansionNoMakerPair=sum(r['pre']['maker_paired_qty']<=EPS for r in ad),
     nonseedExpansionWithPositivePairGain=sum(r['makerPairGainSinceLastTaker']>EPS for r in ad),
     nonseedExpansionWithPositivePairAndNegativeTotalFloor=sum(r['pre']['floor']<-EPS for r in sourcepositive),
     repairedFractionOfFullGapMarketMedian=median([median(v) for v in wm.values()]),
     singleSideServiceMakerOnlyWrongWeak=sum(r['maker_total_weak_disagree'] for r in sv if r['side']!='BOTH'),
     singleSideService=sum(r['side']!='BOTH' for r in sv),
     mixedTakerFirstExpansion=ta,mixedMakerFirstExpansion=ma,mixedAgreeExpansion=both,
     totalNonseedExpansionUnderTakerFirstConvention=len(ad)+ta,
     totalNonseedExpansionUnderMakerFirstConvention=len(ad)+ma,
     conventionCaveat='whole Taker batch before/after same-second Makers are sensitivity conventions, not reconstructed timing or sharp bounds on all intrasecond interleavings')
 assert len(pure)==primary['pureActiveBatches'] and len(mixed)==primary['mixedActiveMakerBatches']
 result=dict(sourceScoreSha256=sha(score),groups=groups,firstChronologicalMakerVsTotalWeakWitness=firstwitness,
   marketsChecked=len(checks),maxIndependentFsumError=max(r['error'] for r in checks),noHFT=True,noTraining=True,
   seedCorrection='initial zero-inventory acquisition excluded from ADD-frequency headline; still retained in initial geometry counts',
   keyLimit='Mixed Maker/Taker seconds remain unresolved. Agreement under two attribution conventions does not establish true per-order decision role.',checks=checks)
 write(op,result)
 print(json.dumps({k:v for k,v in result.items() if k!='checks'},ensure_ascii=False))


if __name__=='__main__':main()
