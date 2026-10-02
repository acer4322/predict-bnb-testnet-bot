"""PLVAC-1 baseline-only seam discovery and budget freeze.

No treatment arm is executed here.  For each preregistered consumed market:
1) run untouched N;
2) run N_OBS with behavior-inert B2/native-menu observation;
3) select the earliest strict-past PENDING_ACTIVE state with legal non-equivalent
   same-side/same-role BOUNDED_ACTIVE and PASSIVE_PRIMARY bundles and native A priority;
4) measure full native continuation service/risk/activity budget telemetry from that seam.

If required telemetry or any fixed market seam is missing, treatment is not authorized.
"""
from __future__ import annotations
import argparse, copy, hashlib, json, math, os, tempfile, zipfile
from collections import defaultdict
from pathlib import Path
import sys
STAGE=Path(__file__).resolve().parent
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if (STAGE/'tools').exists(): sys.path.insert(0,str(STAGE))
elif str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_native_selection_margin_prevalence_stagea16_v1 as margin

b2=margin.b2; clock=b2.clock; base=margin.base
EPS=margin.EPS; TOL=1e-8; ACCOUNT_TOL=1e-7
FIXED=[1824758,1824852,1825962,1825994]

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def src(rel):
    p=ROOT/rel
    if p.exists(): return p
    q=Path(__file__).resolve().with_name(Path(rel).name)
    return q if q.exists() else p
def stable(x): return b2.stable(x)
def digest(x): return b2.digest(x)
def payoff(sim):
    u=float(sim.inv['UP'])-float(sim.cost); d=float(sim.inv['DOWN'])-float(sim.cost)
    return {'U':u,'D':d,'M':0.5*(u+d),'T':0.5*(u-d),'Floor':min(u,d),'Best':max(u,d),'upQty':float(sim.inv['UP']),'downQty':float(sim.inv['DOWN']),'buyNotional':float(sim.cost)}
def open_lots(sim):
    return [copy.deepcopy(x) for x in sim.serializable_lots() if x.get('completedAt') is None and float(x.get('remainingQty') or 0)>EPS]
def pair_debt(fill_rows):
    # Canonical project FIFO actual-fill pairing semantics (audit_eth_v13_passive_pair_economics_smoke_v1.py).
    unmatched={'UP':[],'DOWN':[]}; debt=0.0; reserve=0.0; paired=0.0
    for e in fill_rows:
        q=float(e.get('confirmedQty') or e.get('shares') or 0.0); p=float(e.get('executionPriceFromInheritedSubstrate') or e.get('price') or 0.0); s=str(e.get('side'))
        if q<=EPS or s not in {'UP','DOWN'}: continue
        opp='DOWN' if s=='UP' else 'UP'; rem=q; lots=unmatched[opp]
        while rem>EPS and lots:
            lot=lots[0]; m=min(rem,float(lot['qty'])); ps=float(lot['price'])+p
            paired+=m; debt+=m*max(0.0,ps-1.0); reserve+=m*max(0.0,1.0-ps)
            rem-=m; lot['qty']-=m
            if lot['qty']<=EPS: lots.pop(0)
        if rem>EPS: unmatched[s].append({'qty':rem,'price':p})
    return {'pairDebt':debt,'pairReserve':reserve,'pairedQty':paired}
def candidate_identity(v): return copy.deepcopy(v.get('identity')) if v else None
def same_physical(a,p):
    if not a or not p:return True
    return str(a.get('side'))==str(p.get('side')) and str(a.get('role'))==str(p.get('role')) and float(a.get('price')).hex()==float(p.get('price')).hex() and float(a.get('qty')).hex()==float(p.get('qty')).hex()
def eligible(sim,a,end,t):
    ref=a['reference']; A=ref.get('candidates',{}).get('BOUNDED_ACTIVE') or {}; P=ref.get('candidates',{}).get('PASSIVE_PRIMARY') or {}
    ai=candidate_identity(A); pi=candidate_identity(P)
    checks={
      'observerClean':bool(a['errorsClean'] and a['observerMutationInert'] and a['menuScopePass']),
      'pendingActive':sim.q_pending_active is not None and sim.q_ladder is not None and sim.q_ladder.get('route')=='PENDING_ACTIVE',
      'R0Exists':len(open_lots(sim))>0,
      'ACompleteLegal':ai is not None and A.get('L')=='TRUE' and A.get('A')=='TRUE' and A.get('currentExecutable') is not False,
      'PCompleteLegal':pi is not None and P.get('L')=='TRUE' and P.get('A')=='TRUE' and P.get('currentExecutable') is not False,
      'sameSideRole':ai is not None and pi is not None and ai.get('side')==pi.get('side') and ai.get('role')==pi.get('role'),
      'nonEquivalentPhysical':ai is not None and pi is not None and not same_physical(ai,pi),
      'nativePriorityA':ref.get('priority')=='BOUNDED_ACTIVE',
      'above180Boundary':(int(end)-int(t))>int(base.v2.NO_NEW_EXPOSURE_MS),
    }
    return all(checks.values()),checks,A,P

class Tracker:
    def __init__(self,sim,t0,end,Aid,Pid):
        self.t0=int(t0); self.end=int(end); self.H=max(EPS,(self.end-self.t0)/1000.0)
        self.R0=copy.deepcopy(open_lots(sim)); self.r0init={int(x['id']):float(x.get('remainingQty') or 0.0) for x in self.R0}; self.Q0=sum(self.r0init.values())
        self.q=max(float(Aid['qty']),float(Pid['qty'])); self.c=max(float(Aid['price'])*float(Aid['qty']),float(Pid['price'])*float(Pid['qty']))
        self.prePairDebt=float(pair_debt(sim.fill_accounting)['pairDebt']); self.last_t=self.t0; self.prev=None
        self.r0Burden={rid:0.0 for rid in self.r0init}; self.newBurden={'UP':0.0,'DOWN':0.0}; self.newPeak={'UP':0.0,'DOWN':0.0}
        self.cashRiskPeak=0.0; self.grossPeak=0.0; self.absNetPeak=0.0; self.grossIntegral=0.0; self.absNetIntegral=0.0
        self.fillStart=len(sim.fill_accounting); self.payStart=len(sim.resp_payment_rows); self.qEventStart=len(getattr(sim,'q_events',[])); self.respStartIds={int(x['id']) for x in sim.serializable_lots()}
        self.sample_state(sim)
    def state(self,sim):
        lots={int(x['id']):x for x in sim.serializable_lots()}; r0={rid:max(0.0,float((lots.get(rid) or {}).get('remainingQty') or 0.0)) for rid in self.r0init}
        new={'UP':0.0,'DOWN':0.0}
        for rid,x in lots.items():
            if rid in self.respStartIds: continue
            if int(x.get('bornAt') or 0)<self.t0: continue
            rem=max(0.0,float(x.get('remainingQty') or 0.0)); service=str(x.get('repairSide') or ('DOWN' if str(x.get('side'))=='UP' else 'UP'))
            if service in new:new[service]+=rem
        p=payoff(sim); gross=float(sim.inv['UP'])+float(sim.inv['DOWN']); net=abs(float(sim.inv['UP'])-float(sim.inv['DOWN']))
        cash=max(0.0,-float(p['U']),-float(p['D']))
        return {'r0':r0,'new':new,'gross':gross,'absnet':net,'cashrisk':cash}
    def sample_state(self,sim):
        self.prev=self.state(sim)
        for s in ('UP','DOWN'): self.newPeak[s]=max(self.newPeak[s],self.prev['new'][s])
        self.cashRiskPeak=max(self.cashRiskPeak,self.prev['cashrisk']); self.grossPeak=max(self.grossPeak,self.prev['gross']); self.absNetPeak=max(self.absNetPeak,self.prev['absnet'])
    def advance(self,t,sim):
        t=int(t); dt=max(0.0,(t-self.last_t)/1000.0)
        if self.prev is not None and dt>0:
            for rid,v in self.prev['r0'].items(): self.r0Burden[rid]+=float(v)*dt
            for s in ('UP','DOWN'): self.newBurden[s]+=float(self.prev['new'][s])*dt
            self.grossIntegral+=float(self.prev['gross'])*dt; self.absNetIntegral+=float(self.prev['absnet'])*dt
        self.last_t=t; self.sample_state(sim)
    def finish(self,sim):
        self.advance(self.end,sim); lots={int(x['id']):x for x in sim.serializable_lots()}
        r0term={rid:max(0.0,float((lots.get(rid) or {}).get('remainingQty') or 0.0)) for rid in self.r0init}
        newterm={'UP':0.0,'DOWN':0.0}; newrows=[]
        for rid,x in lots.items():
            if rid in self.respStartIds or int(x.get('bornAt') or 0)<self.t0: continue
            rem=max(0.0,float(x.get('remainingQty') or 0.0)); service=str(x.get('repairSide') or ('DOWN' if str(x.get('side'))=='UP' else 'UP'))
            if service in newterm:newterm[service]+=rem
            newrows.append(copy.deepcopy(x))
        pd=pair_debt(sim.fill_accounting); burn=max(0.0,float(pd['pairDebt'])-self.prePairDebt)
        fills=sim.fill_accounting[self.fillStart:]; sides={str(x.get('side')) for x in fills if float(x.get('confirmedQty') or 0)>EPS}
        # Conservative circulation evidence: distinct completed post-t0 responsibilities followed by a later/same-time new responsibility birth.
        completed=[x for x in lots.values() if x.get('completedAt') is not None and int(x.get('completedAt') or 0)>=self.t0]
        births=[x for x in lots.values() if int(x.get('bornAt') or 0)>=self.t0 and int(x['id']) not in self.respStartIds]
        links=[]
        used_new=set()
        for old in sorted(completed,key=lambda x:(int(x.get('completedAt') or 0),int(x['id']))):
            ct=int(old.get('completedAt') or 0)
            for nw in sorted(births,key=lambda x:(int(x.get('bornAt') or 0),int(x['id']))):
                if int(nw['id']) in used_new:continue
                if int(nw.get('bornAt') or 0)>=ct:
                    links.append({'retiredResponsibilityId':int(old['id']),'completedAt':ct,'continuationResponsibilityId':int(nw['id']),'bornAt':int(nw.get('bornAt') or 0)})
                    used_new.add(int(nw['id']));break
        qev=copy.deepcopy(getattr(sim,'q_events',[])[self.qEventStart:])
        norm=lambda x,d: None if d<=EPS else float(x)/float(d)
        lotNorm={str(rid):norm(r0term[rid],q0) for rid,q0 in self.r0init.items()}
        lotBurden={str(rid):norm(self.r0Burden[rid],q0*self.H) for rid,q0 in self.r0init.items()}
        return {
          'scale':{'Hsec':self.H,'q':self.q,'c':self.c,'Q0':self.Q0},
          'R0':{'initial':self.R0,'terminalRemainingById':r0term,'terminalResidualNorm':norm(sum(r0term.values()),self.Q0),'lotResidualNorm':lotNorm,'burdenNorm':norm(sum(self.r0Burden.values()),self.Q0*self.H),'lotBurdenNorm':lotBurden},
          'newService':{'rows':newrows,'peakByServiceSide':self.newPeak,'terminalByServiceSide':newterm,'burdenShareSecByServiceSide':self.newBurden,
                        'peakNormByServiceSide':{s:norm(self.newPeak[s],self.q) for s in ('UP','DOWN')},'terminalNormByServiceSide':{s:norm(newterm[s],self.q) for s in ('UP','DOWN')},'burdenNormByServiceSide':{s:norm(self.newBurden[s],self.q*self.H) for s in ('UP','DOWN')}},
          'risk':{'cashAtRiskPeakNorm':norm(self.cashRiskPeak,self.c),'grossPeakNorm':norm(self.grossPeak,self.q),'absNetPeakNorm':norm(self.absNetPeak,self.q),'grossIntegralNorm':norm(self.grossIntegral,self.q*self.H),'absNetIntegralNorm':norm(self.absNetIntegral,self.q*self.H),'nonReplenishingPairDebtBurn':burn,'nonReplenishingBurnNorm':norm(burn,self.c),'pairAccounting':pd,'realNetCostStatus':'REAL_NET_COST_UNRESOLVED'},
          'activity':{'suffixConfirmedFills':len(fills),'fillSides':sorted(sides),'T':len(fills)>0,'B':sides=={'UP','DOWN'},'R':len(links)>=2,'circulationLinks':links,'completedPostT0':[{'id':int(x['id']),'completedAt':int(x.get('completedAt') or 0)} for x in completed],'newResponsibilityBirths':[{'id':int(x['id']),'bornAt':int(x.get('bornAt') or 0),'side':x.get('side'),'repairSide':x.get('repairSide')} for x in births], 'circulationDefinitionStatus':'CONSERVATIVE_RESPONSIBILITY_RETIREMENT_TO_LATER_BIRTH_LINK'},
          'qEventTypes':sorted(set(str(x.get('type') or x.get('event') or x.get('action') or x.get('reason') or 'UNKNOWN') for x in qev)),
          'qEventSample':stable(qev[:40])
        }

def run_native(tape,spec,observe):
    sim=clock.InstrumentedFork(tape,spec,'II','N'); seed_phase=None; seam=None; tr=None
    try:
        updates=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0]))); first=int(sim.meta['firstReceivedMs']); base.v2.base.ex.advance_to(sim.bt,first); end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
        for ordinal,u in enumerate(updates):
            t=int(u[1])
            if tr is not None: tr.advance(t,sim)
            sim.set_phase(ordinal,t); base.v2.base.ex.advance_to(sim.bt,t); sim.process(t); sim.cancel_expired(t); sim._refresh_slots(t); base.v2.base.apply(sim.book,u); qv=base.v2.base.quotes(sim.book)
            if qv: sim._risk_contract_if_needed(t); sim._reanchor_stale(t)
            if observe and seam is None and qv and seed_phase is not None and ordinal>seed_phase and (end-t)>int(base.v2.NO_NEW_EXPOSURE_MS):
                before=b2.behavior_state_digest(sim); a=margin.audit_current_state(sim,ordinal,t,qv,end); after=b2.behavior_state_digest(sim); ok,checks,A,P=eligible(sim,a,end,t)
                if ok:
                    ai=candidate_identity(A); pi=candidate_identity(P)
                    seam={'phaseOrdinal':int(ordinal),'eventTimestampMs':int(t),'stateHash':a['stateHash'],'behaviorPrefixDigest':before,'observerReadOnly':before==after,'checks':checks,'AIdentity':stable(ai),'PIdentity':stable(pi),'nativePriority':a['reference'].get('priority'),'R0':stable(open_lots(sim))}
            before_seed=bool(getattr(sim,'postSeed',False))
            if qv: sim._open_one_option(t,qv,end)
            if not before_seed and bool(getattr(sim,'postSeed',False)): seed_phase=int(ordinal)
            if observe and seam is not None and tr is None and ordinal==seam['phaseOrdinal']:
                tr=Tracker(sim,seam['eventTimestampMs'],end,seam['AIdentity'],seam['PIdentity'])
            if tr is not None: tr.sample_state(sim)
            sim._sample_occupancy()
        fin=clock.finalize(sim,spec,None); tele=None if tr is None else tr.finish(sim)
        return {'behaviorLedgerDigest':fin['behaviorLedgerDigest'],'terminal':payoff(sim),'accountingChecks':fin['accountingChecks'],'terminalMaxSlots':int(fin['terminal']['maxSlots']),'seedPhysical':stable(fin.get('seedPhysical')),'seedAdmissionEvents':stable(fin.get('seedAdmissionEvents')),'seedPhaseOrdinal':seed_phase,'seam':seam,'telemetry':tele}
    finally: sim.close()
def seed_match(seed,spec):
    if not seed:return False
    return str(seed.get('side'))==str(spec['seedSideFromCache']) and str(seed.get('role'))=='ECONOMIC_CORE' and math.isclose(float(seed.get('price')),float(spec['seedPriceFromCache']),abs_tol=TOL) and math.isclose(float(seed.get('qty')),float(spec['seedQtyFromCache']),abs_tol=TOL)
def make_row(mid,spec,N,O):
    checks={'N_NOBS_behaviorParity':N['behaviorLedgerDigest']==O['behaviorLedgerDigest'],'N_NOBS_terminalParity':stable(N['terminal'])==stable(O['terminal']),'N_NOBS_seedParity':stable(N['seedPhysical'])==stable(O['seedPhysical']) and stable(N['seedAdmissionEvents'])==stable(O['seedAdmissionEvents']),'seedMatchesOutcomeBlindCache':seed_match(O['seedPhysical'],spec),'accountingClean':all(bool(v) for v in N['accountingChecks'].values()) and all(bool(v) for v in O['accountingChecks'].values()),'max4':N['terminalMaxSlots']<=4 and O['terminalMaxSlots']<=4}
    seam=O['seam']; checks['eligibleSeamFound']=seam is not None
    if seam is not None: checks['seamObserverReadOnly']=bool(seam['observerReadOnly']) and all(bool(v) for v in seam['checks'].values())
    return {'marketId':mid,'checks':checks,'correctnessPass':all(v for k,v in checks.items() if k not in {'eligibleSeamFound'}),'eligibleSeamFound':seam is not None,'seam':seam,'baselineTelemetry':O['telemetry'],'NBehaviorDigest':N['behaviorLedgerDigest'],'NOBSBehaviorDigest':O['behaviorLedgerDigest'],'terminal':O['terminal']}
def numeric_values(row):
    t=row['baselineTelemetry']; out={}
    out['R0TerminalResidual']=t['R0']['terminalResidualNorm']; out['R0Burden']=t['R0']['burdenNorm']; out['cashAtRiskPeak']=t['risk']['cashAtRiskPeakNorm']; out['grossIntegral']=t['risk']['grossIntegralNorm']; out['absNetIntegral']=t['risk']['absNetIntegralNorm']; out['burn']=t['risk']['nonReplenishingBurnNorm']; out['grossPeak']=t['risk']['grossPeakNorm']; out['absNetPeak']=t['risk']['absNetPeakNorm']
    for s in ('UP','DOWN'):
        out[f'newPeak_{s}']=t['newService']['peakNormByServiceSide'][s]; out[f'newTerminal_{s}']=t['newService']['terminalNormByServiceSide'][s]; out[f'newBurden_{s}']=t['newService']['burdenNormByServiceSide'][s]
    return out
def aggregate(rows):
    eligible=[r for r in rows if r['eligibleSeamFound'] and r['baselineTelemetry'] is not None]
    vals={r['marketId']:numeric_values(r) for r in eligible}; tails={}; pools={}; pool_names={'R0TerminalResidual','R0Burden','cashAtRiskPeak','grossIntegral','absNetIntegral','burn'}
    names=sorted({k for v in vals.values() for k in v})
    for k in names:
        xs=[v[k] for v in vals.values() if v.get(k) is not None and math.isfinite(float(v[k]))]
        tails[k]=None if not xs else max(xs)
        if k in pool_names:pools[k]=None if len(xs)!=len(eligible) else sum(xs)
    coverage={'T':sum(bool(r['baselineTelemetry']['activity']['T']) for r in eligible),'B':sum(bool(r['baselineTelemetry']['activity']['B']) for r in eligible),'R':sum(bool(r['baselineTelemetry']['activity']['R']) for r in eligible)}
    return {'Tail':tails,'Pool':pools,'coverageN':coverage,'perMarketNormalized':vals,'eligibleMarkets':[r['marketId'] for r in eligible]}
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--cohort',required=True); ap.add_argument('--prereg',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    co=json.loads(Path(a.cohort).read_text(encoding='utf-8')); states=co['states']; mids=[int(x['marketId']) for x in states]
    if mids!=FIXED: raise RuntimeError(f'COHORT_MISMATCH:{mids}')
    outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.')); rows=[]
    preflight={'version':'B3_PLVAC_BASELINE_HASH_FREEZE_V1','createdBeforeFirstReplay':True,'sha256':{'runner':sha(Path(__file__)),'bundle':sha(a.bundle),'cohort':sha(a.cohort),'prereg':sha(a.prereg),'sourceContract':sha(src('data/research/r4_v0/p0_provenance_v1/GPT6_EXTREME_B3_PROSPECTIVE_VALUE_CONTRACT_REPORT_ONLY_RESULT_V1_20260908.md')),'selectionManifest':sha(src('data/research/r4_v0/p0_provenance_v1/INITIAL_PROBE_CORE_HANDBACK_REPLICATION6_FIXED_SELECTION_20260906.json')),'pairAccountingSource':sha(src('tools/audit_eth_v13_passive_pair_economics_smoke_v1.py'))}}
    (outdir/'preflight_hash_freeze.json').write_text(json.dumps(preflight,ensure_ascii=False,indent=2),encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='plvac_baseline_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            names=set(z.namelist())
            for s in states:
                member=f"tapes/{int(s['marketId'])}.json.xz"
                if member not in names:raise RuntimeError(f'MISSING_TAPE:{member}')
                z.extract(member,root)
        for i,spec in enumerate(states,1):
            mid=int(spec['marketId']); tape=root/'tapes'/f'{mid}.json.xz'; N=run_native(tape,spec,False); O=run_native(tape,spec,True); row=make_row(mid,spec,N,O); rows.append(row)
            (outdir/'rows.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rows)+'\n',encoding='utf-8')
            print(json.dumps({'progress':i,'of':4,'marketId':mid,'correct':row['correctnessPass'],'seam':None if row['seam'] is None else [row['seam']['phaseOrdinal'],row['seam']['eventTimestampMs']], 'TBR':None if row['baselineTelemetry'] is None else [row['baselineTelemetry']['activity']['T'],row['baselineTelemetry']['activity']['B'],row['baselineTelemetry']['activity']['R']]},ensure_ascii=False),flush=True)
    allcorrect=all(r['correctnessPass'] for r in rows); allseams=all(r['eligibleSeamFound'] for r in rows); manifest=aggregate(rows) if allseams else None
    required_identified=allseams and manifest is not None and all(v is not None for v in manifest['Tail'].values()) and all(v is not None for v in manifest['Pool'].values())
    if not allcorrect: verdict='CORRECTNESS_STOP'
    elif not allseams: verdict='COHORT_MARGIN_NOT_EXERCISED'
    elif not required_identified: verdict='BUDGET_OR_ACTIVITY_NOT_IDENTIFIED'
    else: verdict='BASELINE_BUDGET_MANIFEST_FROZEN_TREATMENT1_AUTHORIZED'
    out={'version':'B3_PROSPECTIVE_LAYERED_CONTRACT_CHALLENGE_SMOKE4_V1_BASELINE_FREEZE_20260908','researchOnly':True,'runtimeAuthority':False,'rows':rows,'allCorrectnessPass':allcorrect,'allEligibleSeamsFound':allseams,'baselineBudgetManifest':manifest,'verdict':verdict,'firstTreatmentAuthorized':verdict=='BASELINE_BUDGET_MANIFEST_FROZEN_TREATMENT1_AUTHORIZED','firstTreatmentMarket':1824758,'preflightHashFreeze':preflight,'boundaries':['N/N_OBS only; no P treatment executed','earliest eligible strict-past seam only','baseline-only Tail/Pool freeze','canonical FIFO actual-fill pairDebt burn','real net fees/rebates unresolved','no fresh/no 8781/no belief model']}
    op=(outdir/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'verdict':verdict,'firstTreatmentAuthorized':out['firstTreatmentAuthorized'],'coverage':None if manifest is None else manifest['coverageN']},ensure_ascii=False),flush=True)
if __name__=='__main__': main()
