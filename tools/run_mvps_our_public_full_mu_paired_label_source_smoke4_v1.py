from __future__ import annotations
import argparse, hashlib, importlib.util, json, os, shutil, tempfile, zipfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
EPS=1e-9
MIDS=[1946036,1946298,1946317,1946448]


def sibling(name,filename):
    p=HERE/filename
    s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

pmod=sibling('mvps_fullmu_p_v2','run_mvps_pair1_public_bid_leg_underwater_add_smoke4_v2.py')


def same_seed(a,b):
    if a is None or b is None:return False
    return (int(a['t'])==int(b['t']) and str(a['side'])==str(b['side']) and
            abs(float(a['price'])-float(b['price']))<=1e-12 and
            abs(float(a['qty'])-float(b['qty']))<=1e-12 and
            int(a['submitsBefore'])==int(b['submitsBefore']) and
            int(a['fillsBefore'])==int(b['fillsBefore']))

class AllowFirstAdverseSourceSim(pmod.PublicBidLegSim):
    def __init__(self,tape):
        super().__init__(tape,True)
        self.overrideUsed=0;self.overrideEvent=None;self.strictPastState=None
    def _state(self,t,qv,pr,side,sameq,oppq,avg,leg):
        return {
          't':int(t),'side':side,'price':float(pr['bid']),'ask':float(pr['ask']),'qty':float(pr['qty']),
          'upBid':float(qv['UP']['bid']),'upAsk':float(qv['UP']['ask']),
          'downBid':float(qv['DOWN']['bid']),'downAsk':float(qv['DOWN']['ask']),
          'spread':float(qv.get('spread',0.0)),'bookImbalance':float(qv.get('imb',0.0)),
          'bestBidDepth':float(qv.get('bd',0.0)),'bestAskDepth':float(qv.get('ad',0.0)),
          'top3Bid':float(qv.get('tb',0.0)),'top3Ask':float(qv.get('ta',0.0)),
          'leg':str(leg),'sameUnmatchedQty':float(sameq),'oppUnmatchedQty':float(oppq),
          'avgUnmatchedCost':None if avg is None else float(avg),
          'invUP':float(self.inv['UP']),'invDOWN':float(self.inv['DOWN']),'cashCost':float(self.cost),
          'submitsBefore':int(self.submits),'fillsBefore':int(self.fills),
          'observerHashPrefix':self.obsHasher.copy().hexdigest(),
          'strictPastOnly':True
        }
    def _open_free_slots(self,t,qv,end):
        if self.overrideUsed==0:
            pr=self._probe_native_legal(t,qv,end)
            if pr is not None:
                side=pr['side'];opp='DOWN' if side=='UP' else 'UP'
                sameq=sum(float(a) for a,_ in self.un[side]);oppq=sum(float(a) for a,_ in self.un[opp])
                pure=bool(sameq>EPS and oppq<=EPS);avg=self.unmatched_avg(side) if sameq>EPS else None
                underwater=bool(avg is not None and pr['bid']<=float(avg)+1e-10);leg=str(self.obs.leg[side])
                if pmod.gate_decision(True,pure,underwater,leg):
                    self.strictPastState=self._state(t,qv,pr,side,sameq,oppq,avg,leg)
                    rec=dict(self.strictPastState);rec.update({'underwater':True,'pureSameSide':True,'nativeAdmissible':True,'decision':'ALLOW_ONE_OVERRIDE'})
                    before=self.n;pmod.LadderSim._open_free_slots(self,t,qv,end)
                    rec['nativeSubmitted']=bool(self.n>before);rec['key']=f'{side}_{before}' if self.n>before else None
                    self.overrideUsed=1;self.overrideEvent=rec;return
        return super()._open_free_slots(t,qv,end)
    def run_source(self,winner):
        r=self.run_bid_leg(winner);r['overrideUsed']=int(self.overrideUsed);r['overrideEvent']=self.overrideEvent;r['strictPastState']=self.strictPastState
        r['upPayoff']=float(self.inv['UP']-self.cost);r['downPayoff']=float(self.inv['DOWN']-self.cost)
        if self.overrideEvent and self.overrideEvent.get('key'):
            o=self.orders.get(self.overrideEvent['key']);r['overrideDirectCum']=float(o.get('cum') or 0.0) if o else 0.0
        else:r['overrideDirectCum']=0.0
        return r

def run_a(tape,winner):
    sim=AllowFirstAdverseSourceSim(tape)
    try:r=sim.run_source(winner);snap=pmod.behavior_snapshot(sim,r)
    finally:sim.close()
    return r,snap

def run_k(tape,winner):
    sim=pmod.PublicBidLegSim(tape,True)
    try:
        r=sim.run_bid_leg(winner);r['upPayoff']=float(sim.inv['UP']-sim.cost);r['downPayoff']=float(sim.inv['DOWN']-sim.cost);snap=pmod.behavior_snapshot(sim,r)
    finally:sim.close()
    return r,snap

def rebate_upper(filled_shares):return 0.005*max(0.0,float(filled_shares))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--freeze',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    freeze=json.load(open(a.freeze,encoding='utf-8'))
    if freeze.get('pilotTrainMarkets')!=MIDS or not freeze.get('stage0Pass'):raise RuntimeError('stage0 freeze mismatch')
    hashes={
      'pRunner':hashlib.sha256((HERE/'run_mvps_pair1_public_bid_leg_underwater_add_smoke4_v2.py').read_bytes()).hexdigest().upper(),
      'ladder':hashlib.sha256((HERE/'run_eth_safety_reintroduction_ladder_1946317.py').read_bytes()).hexdigest().upper(),
      'bundle':hashlib.sha256(Path(a.bundle).read_bytes()).hexdigest().upper(),
    }
    for k in ('pRunner','ladder','bundle'):
        if hashes[k]!=freeze['sourceHashes'][k]:raise RuntimeError(f'frozen hash drift {k}')
    tmp=Path(tempfile.mkdtemp(prefix='mvps_fullmu_source4_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[];kruns={};ksnaps={};aruns={};asnaps={};firstEligible=None
        for mid in MIDS:
            tape=tmp/'tapes'/f'{mid}.json.xz';winner=co[mid]['winner']
            k,ks=run_k(tape,winner);a1,as1=run_a(tape,winner);kruns[mid]=k;ksnaps[mid]=ks;aruns[mid]=a1;asnaps[mid]=as1
            kv=(k.get('vetoed') or []);eligible=bool(kv)
            if eligible and firstEligible is None:firstEligible=mid
            if eligible:
                if int(a1.get('overrideUsed') or 0)!=1 or not a1.get('overrideEvent') or not a1['overrideEvent'].get('nativeSubmitted'):raise RuntimeError(f'A target not exercised {mid}')
                if not same_seed(kv[0],a1['overrideEvent']):raise RuntimeError(f'A/K seed mismatch {mid}')
                if k['observerHash']!=a1['observerHash']:raise RuntimeError(f'observer branch dependence {mid}')
                gross=float(a1['pnlDiagnosticOnly']-k['pnlDiagnosticOnly'])
                du=float(a1['upPayoff']-k['upPayoff']);dd=float(a1['downPayoff']-k['downPayoff'])
                klo=rebate_upper(k['filledQty']);ahi=rebate_upper(a1['filledQty']);netlo=gross-klo;nethi=gross+ahi
                row={'marketId':mid,'eligibleTarget':True,'winnerEvaluationOnly':str(winner).upper(),'strictPastState':a1['strictPastState'],
                     'seed':a1['overrideEvent'],'seedDirectFill':float(a1['overrideDirectCum']),
                     'K':{'realizedPnlGross':float(k['pnlDiagnosticOnly']),'upPayoffGross':float(k['upPayoff']),'downPayoffGross':float(k['downPayoff']),'fills':int(k['fillEvents']),'filledShares':float(k['filledQty']),'buyNotional':float(k['buyNotional']),'behaviorHash':ks['sha256']},
                     'A':{'realizedPnlGross':float(a1['pnlDiagnosticOnly']),'upPayoffGross':float(a1['upPayoff']),'downPayoffGross':float(a1['downPayoff']),'fills':int(a1['fillEvents']),'filledShares':float(a1['filledQty']),'buyNotional':float(a1['buyNotional']),'behaviorHash':as1['sha256']},
                     'label':{'realizedGrossDelta':gross,'deltaUPGross':du,'deltaDOWNGross':dd,'KRebateTerminalUpper':klo,'ARebateTerminalUpper':ahi,'fullNetObservedLabelInterval':[netlo,nethi],'intervalContainsZero':netlo<=0<=nethi,'intervalType':'COST_REBATE_UNCERTAINTY_FOR_OBSERVED_PAIRED_LABEL_NOT_MU_CI'},
                     'observerPolicyIndependent':True}
            else:
                if int(a1.get('overrideUsed') or 0)!=0:raise RuntimeError(f'A unexpected target {mid}')
                row={'marketId':mid,'eligibleTarget':False,'winnerEvaluationOnly':str(winner).upper(),'strictPastState':None,'seed':None,'seedDirectFill':0.0,
                     'K':{'realizedPnlGross':float(k['pnlDiagnosticOnly']),'fills':int(k['fillEvents']),'filledShares':float(k['filledQty']),'buyNotional':float(k['buyNotional']),'behaviorHash':ks['sha256']},
                     'A':{'realizedPnlGross':float(a1['pnlDiagnosticOnly']),'fills':int(a1['fillEvents']),'filledShares':float(a1['filledQty']),'buyNotional':float(a1['buyNotional']),'behaviorHash':as1['sha256']},
                     'label':None,'observerPolicyIndependent':k['observerHash']==a1['observerHash']}
            rows.append(row);print(json.dumps({'marketId':mid,'eligible':eligible,'seedFill':row['seedDirectFill'],'grossLabel':None if row['label'] is None else row['label']['realizedGrossDelta'],'netInterval':None if row['label'] is None else row['label']['fullNetObservedLabelInterval']},ensure_ascii=False),flush=True)
        repeat={'performed':False}
        if firstEligible is not None:
            tape=tmp/'tapes'/f'{firstEligible}.json.xz';winner=co[firstEligible]['winner']
            kr,ksr=run_k(tape,winner);ar,asr=run_a(tape,winner)
            repeat={'performed':True,'marketId':firstEligible,
                    'KBehavior':ksr['sha256']==ksnaps[firstEligible]['sha256'],'KObserver':kr['observerHash']==kruns[firstEligible]['observerHash'],
                    'ABehavior':asr['sha256']==asnaps[firstEligible]['sha256'],'AObserver':ar['observerHash']==aruns[firstEligible]['observerHash'],
                    'ASeed':same_seed(ar.get('overrideEvent'),aruns[firstEligible].get('overrideEvent')),
                    'AEndpoints':abs(float(ar['upPayoff'])-float(aruns[firstEligible]['upPayoff']))<=1e-12 and abs(float(ar['downPayoff'])-float(aruns[firstEligible]['downPayoff']))<=1e-12}
            if not all(v for k,v in repeat.items() if k not in ('performed','marketId')):raise RuntimeError('repeat parity fail')
        eligibleRows=[r for r in rows if r['eligibleTarget']];nonEq=[r for r in eligibleRows if r['label'] and abs(r['label']['realizedGrossDelta'])>EPS]
        correctness={'sourceHashParity':True,'observerIndependent4of4':all(r['observerPolicyIndependent'] for r in rows),'repeatPass':firstEligible is None or all(v for k,v in repeat.items() if k not in ('performed','marketId')),
                     'exactOverrideOnEligible':all(int(aruns[r['marketId']].get('overrideUsed') or 0)==1 for r in eligibleRows),'noOverrideOnIneligible':all(int(aruns[r['marketId']].get('overrideUsed') or 0)==0 for r in rows if not r['eligibleTarget'])}
        if not all(correctness.values()):raise RuntimeError('correctness fail')
        if len(eligibleRows)<2:verdict='INSUFFICIENT_ELIGIBLE_SOURCE_EXERCISE'
        elif len(nonEq)==0:verdict='NO_VALUE_RESPONSE_IN_SOURCE_PILOT_B5_NOT_REJECTED'
        else:verdict='CAUSAL_LABEL_SOURCE_AVAILABLE_PREDICTIVE_VALUE_UNPROVEN'
        out={'version':'MVPS_OUR_PUBLIC_FULL_MU_PAIRED_LABEL_SOURCE_SMOKE4_V1_20260909','researchOnly':True,'runtimeAuthority':False,'markets':MIDS,'rows':rows,'firstEligibleRepeat':repeat,'correctness':correctness,
             'summary':{'eligibleMarkets':len(eligibleRows),'nonEquivalentGrossLabelMarkets':len(nonEq),'costIntervalAvailableMarkets':sum(r['label'] is not None for r in eligibleRows),'intervalContainsZeroMarkets':sum(bool(r['label']['intervalContainsZero']) for r in eligibleRows if r['label'])},
             'costContract':freeze['costContract'],'verdict':verdict,'branchEquivalentsUsed':8+(2 if firstEligible is not None else 0),
             'boundary':['four fixed non-anchor TRAIN markets only','K/A first legal underwater adverse P veto only','strict-past state sealed at seed before evaluation lane','official Maker fee/rebate finite cost interval; no exact rebate assumption','no model training/no CAL/no TEST/no fresh/reserve/no8781/no dream fill']}
        op=(Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
        print(json.dumps({'ok':True,'verdict':verdict,'summary':out['summary'],'correctness':correctness,'BE':out['branchEquivalentsUsed']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
