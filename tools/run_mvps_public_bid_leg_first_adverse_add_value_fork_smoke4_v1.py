from __future__ import annotations
import argparse, hashlib, importlib.util, json, math, os, shutil, tempfile, zipfile
from pathlib import Path

HERE=Path(__file__).resolve().parent

def sibling(name, filename):
    p=HERE/filename
    s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

pmod=sibling('mvps_public_bid_leg_p_v2','run_mvps_pair1_public_bid_leg_underwater_add_smoke4_v2.py')
EPS=1e-9
MIDS=[1945866,1945869,1945898,1945986]

class AllowFirstAdverseSim(pmod.PublicBidLegSim):
    def __init__(self,tape):
        super().__init__(tape,True)
        self.overrideUsed=0
        self.overrideEvent=None
    def _open_free_slots(self,t,qv,end):
        # Detect exactly the state that frozen P would veto, after full native legality.
        if self.overrideUsed==0:
            pr=self._probe_native_legal(t,qv,end)
            if pr is not None:
                side=pr['side']; opp='DOWN' if side=='UP' else 'UP'
                sameq=sum(float(a) for a,_ in self.un[side]); oppq=sum(float(a) for a,_ in self.un[opp])
                pure=bool(sameq>EPS and oppq<=EPS)
                avg=self.unmatched_avg(side) if sameq>EPS else None
                underwater=bool(avg is not None and pr['bid']<=float(avg)+1e-10)
                leg=str(self.obs.leg[side])
                if pmod.gate_decision(True,pure,underwater,leg):
                    rec={'t':int(t),'side':side,'price':float(pr['bid']),'qty':float(pr['qty']),
                         'sameUnmatchedQty':float(sameq),'oppUnmatchedQty':float(oppq),
                         'avgUnmatchedCost':None if avg is None else float(avg),'underwater':underwater,
                         'leg':leg,'pureSameSide':pure,'nativeAdmissible':True,
                         'submitsBefore':int(self.submits),'fillsBefore':int(self.fills),
                         'invUP':float(self.inv['UP']),'invDOWN':float(self.inv['DOWN']),'cost':float(self.cost),
                         'decision':'ALLOW_ONE_OVERRIDE'}
                    before=self.n
                    # Bypass only P admission once; all native legality/submit semantics remain frozen.
                    pmod.LadderSim._open_free_slots(self,t,qv,end)
                    rec['nativeSubmitted']=bool(self.n>before)
                    rec['key']=f'{side}_{before}' if self.n>before else None
                    self.overrideUsed=1
                    self.overrideEvent=rec
                    return
        return super()._open_free_slots(t,qv,end)
    def run_allow(self,winner):
        r=self.run_bid_leg(winner)
        r['overrideUsed']=int(self.overrideUsed)
        r['overrideEvent']=self.overrideEvent
        r['upPayoff']=float(self.inv['UP']-self.cost)
        r['downPayoff']=float(self.inv['DOWN']-self.cost)
        if self.overrideEvent and self.overrideEvent.get('key'):
            o=self.orders.get(self.overrideEvent['key'])
            r['overrideDirectCum']=float(o.get('cum') or 0.0) if o else 0.0
        else:r['overrideDirectCum']=0.0
        return r

def run_allow(tape,winner):
    sim=AllowFirstAdverseSim(tape)
    try:
        r=sim.run_allow(winner); snap=pmod.behavior_snapshot(sim,r)
    finally:sim.close()
    return r,snap

def k_endpoints(k):
    winner=str(k['winnerPostHocOnly']).upper(); other='DOWN' if winner=='UP' else 'UP'
    pnl=float(k['pnl']); best=float(k['best']); floor=float(k['floor'])
    if abs(pnl-best)<=1e-8:
        return {winner:best,other:floor}
    if abs(pnl-floor)<=1e-8:
        return {winner:floor,other:best}
    raise RuntimeError(f'cannot reconstruct K endpoints winner={winner} pnl={pnl} best={best} floor={floor}')

def same_seed(a,b):
    if a is None or b is None:return False
    return (int(a['t'])==int(b['t']) and str(a['side'])==str(b['side']) and
            abs(float(a['price'])-float(b['price']))<=1e-12 and abs(float(a['qty'])-float(b['qty']))<=1e-12 and
            int(a['submitsBefore'])==int(b['submitsBefore']) and int(a['fillsBefore'])==int(b['fillsBefore']))

def classify(du,dd):
    pu=du>EPS; nu=du<-EPS; pd=dd>EPS; nd=dd<-EPS
    if (du>=-EPS and dd>=-EPS) and (pu or pd):return 'ALLOW_ONE_ENDPOINT_DOMINATES'
    if (du<=EPS and dd<=EPS) and (nu or nd):return 'KEEP_VETO_ENDPOINT_DOMINATES'
    if (pu and nd) or (nu and pd):return 'GENUINE_BRANCH_TRADEOFF'
    return 'TERMINAL_ENDPOINT_EQUIVALENT'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--freeze',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    freeze=json.load(open(a.freeze,encoding='utf-8'))
    if freeze.get('markets')!=MIDS:raise RuntimeError('freeze market mismatch')
    # Verify frozen source hashes before any HFT branch.
    hashes={
      'pRunner':hashlib.sha256((HERE/'run_mvps_pair1_public_bid_leg_underwater_add_smoke4_v2.py').read_bytes()).hexdigest().upper(),
      'ladder':hashlib.sha256((HERE/'run_eth_safety_reintroduction_ladder_1946317.py').read_bytes()).hexdigest().upper(),
    }
    if hashes['pRunner']!=freeze['sourceHashes']['pRunner'] or hashes['ladder']!=freeze['sourceHashes']['ladder']:
        raise RuntimeError('frozen source hash drift')
    tmp=Path(tempfile.mkdtemp(prefix='mvps_first_adverse_value_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        # BE1: first-anchor KEEP_VETO no-op parity under current wrapper/import stack.
        m0=MIDS[0]; k0,sK0=pmod.run_public(tmp/'tapes'/f'{m0}.json.xz',co[m0]['winner'],True)
        fk0=freeze['rows'][str(m0)]
        kParity={
          'behaviorHash':sK0['sha256']==fk0['behaviorHash'],
          'observerHash':k0['observerHash']==fk0['observerHash'],
          'pnl':abs(float(k0['pnlDiagnosticOnly'])-float(fk0['pnl']))<=1e-9,
          'fills':int(k0['fillEvents'])==int(fk0['fills']),
          'submits':int(k0['submits'])==int(fk0['submits']),
          'notional':abs(float(k0['buyNotional'])-float(fk0['buyNotional']))<=1e-9,
        }
        if not all(kParity.values()):raise RuntimeError('KEEP_VETO first-anchor parity fail')
        rows=[]; allowRuns={}; allowSnaps={}
        # BE2-BE5: four ALLOW_ONE branches in fixed order.
        for mid in MIDS:
            tape=tmp/'tapes'/f'{mid}.json.xz'; winner=co[mid]['winner']; fr=freeze['rows'][str(mid)]
            r,s=run_allow(tape,winner); allowRuns[mid]=r; allowSnaps[mid]=s
            if int(r.get('overrideUsed') or 0)!=1 or not r.get('overrideEvent') or not r['overrideEvent'].get('nativeSubmitted'):
                raise RuntimeError(f'override not exercised market {mid}')
            if not same_seed(r['overrideEvent'],fr['firstVeto']):
                raise RuntimeError(f'seed mismatch market {mid}: {r["overrideEvent"]} != {fr["firstVeto"]}')
            K=k_endpoints(fr); A={'UP':float(r['upPayoff']),'DOWN':float(r['downPayoff'])}
            du=A['UP']-K['UP']; dd=A['DOWN']-K['DOWN']; cls=classify(du,dd)
            row={'marketId':mid,'winnerPostHocOnly':str(fr['winnerPostHocOnly']).upper(),
                 'K':{'UP':K['UP'],'DOWN':K['DOWN'],'pnl':float(fr['pnl']),'fills':int(fr['fills']),'submits':int(fr['submits']),'buyNotional':float(fr['buyNotional'])},
                 'A':{'UP':A['UP'],'DOWN':A['DOWN'],'pnl':float(r['pnlDiagnosticOnly']),'fills':int(r['fillEvents']),'submits':int(r['submits']),'buyNotional':float(r['buyNotional'])},
                 'delta':{'UP':du,'DOWN':dd},'classification':cls,'overrideEvent':r['overrideEvent'],'overrideDirectCum':float(r['overrideDirectCum']),
                 'observerHashA':r['observerHash'],'observerHashK':fr['observerHash'],'observerPolicyIndependent':r['observerHash']==fr['observerHash'],'behaviorHashA':s['sha256']}
            rows.append(row);print(json.dumps({'marketId':mid,'deltaUP':du,'deltaDOWN':dd,'classification':cls,'Arealized':row['A']['pnl'],'Krealized':row['K']['pnl'],'fillsA':row['A']['fills'],'fillsK':row['K']['fills'],'overrideFill':row['overrideDirectCum']},ensure_ascii=False),flush=True)
        # BE6: first-anchor ALLOW_ONE deterministic repeat.
        ar,sar=run_allow(tmp/'tapes'/f'{m0}.json.xz',co[m0]['winner'])
        repeat={'behaviorHash':sar['sha256']==allowSnaps[m0]['sha256'],'observerHash':ar['observerHash']==allowRuns[m0]['observerHash'],
                'upPayoff':abs(float(ar['upPayoff'])-float(allowRuns[m0]['upPayoff']))<=1e-12,'downPayoff':abs(float(ar['downPayoff'])-float(allowRuns[m0]['downPayoff']))<=1e-12,
                'seed':same_seed(ar.get('overrideEvent'),allowRuns[m0].get('overrideEvent'))}
        classes={c:sum(r['classification']==c for r in rows) for c in ['ALLOW_ONE_ENDPOINT_DOMINATES','KEEP_VETO_ENDPOINT_DOMINATES','GENUINE_BRANCH_TRADEOFF','TERMINAL_ENDPOINT_EQUIVALENT']}
        totalAFills=sum(r['A']['fills'] for r in rows);totalKFills=sum(r['K']['fills'] for r in rows);totalANot=sum(r['A']['buyNotional'] for r in rows);totalKNot=sum(r['K']['buyNotional'] for r in rows)
        activity={'fillRatioAoverK':totalAFills/totalKFills if totalKFills else None,'notionalRatioAoverK':totalANot/totalKNot if totalKNot else None,
                  'totalAFills':totalAFills,'totalKFills':totalKFills,'totalANotional':totalANot,'totalKNotional':totalKNot,
                  'collapseWarning':(totalKFills>0 and totalAFills/totalKFills<.75) or (totalKNot>0 and totalANot/totalKNot<.75)}
        correctness={'sourceHashParity':True,'keepVetoFirstAnchorParity':all(kParity.values()),'allowRepeat':all(repeat.values()),'overrideExactlyOnce4of4':all(int(allowRuns[m]['overrideUsed'])==1 for m in MIDS),
                     'seedMatch4of4':all(same_seed(allowRuns[m]['overrideEvent'],freeze['rows'][str(m)]['firstVeto']) for m in MIDS),
                     'observerIndependent4of4':all(r['observerPolicyIndependent'] for r in rows)}
        if not all(correctness.values()):raise RuntimeError('correctness summary failure')
        if classes['ALLOW_ONE_ENDPOINT_DOMINATES'] and classes['KEEP_VETO_ENDPOINT_DOMINATES']:
            verdict='ADVERSE_LEG_ACTION_CLASS_VALUE_HETEROGENEOUS'
        elif classes['ALLOW_ONE_ENDPOINT_DOMINATES']:
            verdict='PUBLIC_LEG_VETO_NOT_VALUE_SUFFICIENT_AT_THIS_STATE'
        elif classes['KEEP_VETO_ENDPOINT_DOMINATES']:
            verdict='LOCAL_VETO_VALUE_WITNESS_SUPPORTED'
        elif classes['GENUINE_BRANCH_TRADEOFF'] and classes['TERMINAL_ENDPOINT_EQUIVALENT']==0:
            verdict='BRANCH_TRADEOFF_REQUIRES_VALUATION_OR_EXOGENOUS_RISK_CONTRACT'
        elif classes['TERMINAL_ENDPOINT_EQUIVALENT']==4:
            verdict='NO_TERMINAL_GEOMETRY_RESPONSE_AT_SELECTED_SEEDS'
        else:
            verdict='MIXED_PARTIAL_GEOMETRY_RESPONSE'
        out={'version':'MVPS_PUBLIC_BID_LEG_FIRST_ADVERSE_ADD_VALUE_FORK_SMOKE4_V1_20260909','researchOnly':True,'runtimeAuthority':False,'markets':MIDS,'rows':rows,'classificationCounts':classes,
             'correctness':correctness,'firstAnchorKeepVetoParity':kParity,'firstAnchorAllowRepeat':repeat,'activity':activity,'verdict':verdict,'branchEquivalentsUsed':6,
             'boundary':['fixed four consumed anchors only','first actual native-admissible adverse P veto only','ALLOW_ONE bypasses exactly one P gate then returns to frozen P','KEEP_VETO main comparators reused from prior formal X result','labelled UP/DOWN terminal endpoints, winner post-hoc only','no utility fit/no threshold tuning/no fresh/reserve/no8781/no dream fill']}
        op=(Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
        print(json.dumps({'ok':True,'verdict':verdict,'classificationCounts':classes,'activity':activity,'correctness':correctness,'BE':6},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
