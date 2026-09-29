from __future__ import annotations
import argparse, hashlib, importlib.util, json, math, os, shutil, tempfile, zipfile
from pathlib import Path

HERE=Path(__file__).resolve().parent

def sibling(name,filename):
    p=HERE/filename
    s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

lad=sibling('mvps_bid_leg_ladder_v2','run_eth_safety_reintroduction_ladder_1946317.py')
base=lad.base
LadderSim=lad.LadderSim
EPS=1e-9
MIDS=[1945866,1945869,1945898,1945986]
EXPECTED={
  1945866:{'pnl':131.568490481977,'fills':116,'submits':204},
  1945869:{'pnl':-39.72549019607843,'fills':62,'submits':128},
  1945898:{'pnl':308.58546345055845,'fills':177,'submits':292},
  1945986:{'pnl':-107.39596435995786,'fills':140,'submits':204},
}

class BidLegObserver:
    def __init__(self):
        self.prev={'UP':None,'DOWN':None}; self.leg={'UP':'UNKNOWN','DOWN':'UNKNOWN'}
    def update(self,qv):
        if qv is None:
            self.prev={'UP':None,'DOWN':None}; self.leg={'UP':'UNKNOWN','DOWN':'UNKNOWN'}
            return
        for side in ('UP','DOWN'):
            b=float(qv[side]['bid']); p=self.prev[side]
            if p is None:
                self.prev[side]=b; self.leg[side]='UNKNOWN'; continue
            if b>p:self.leg[side]='FAVORABLE'
            elif b<p:self.leg[side]='ADVERSE'
            self.prev[side]=b


def gate_decision(native_admissible:bool,pure_same_side:bool,underwater:bool,leg:str)->bool:
    return bool(native_admissible and pure_same_side and underwater and leg=='ADVERSE')


def fixture_contract():
    o=BidLegObserver(); q=lambda u,d:{'UP':{'bid':u,'ask':u+.01},'DOWN':{'bid':d,'ask':d+.01}}
    out=[]
    o.update(q(.50,.40)); out.append(('prime',dict(o.leg)))
    o.update(q(.49,.41)); out.append(('down_up',dict(o.leg)))
    o.update(q(.49,.41)); out.append(('same_retains',dict(o.leg)))
    o.update(q(.51,.39)); out.append(('reverse',dict(o.leg)))
    o.update(None); out.append(('invalid',dict(o.leg)))
    o.update(q(.52,.38)); out.append(('reprime',dict(o.leg)))
    checks={
      'primeUnknown':out[0][1]=={'UP':'UNKNOWN','DOWN':'UNKNOWN'},
      'upDownIsAdverse':out[1][1]['UP']=='ADVERSE',
      'downUpIsFavorable':out[1][1]['DOWN']=='FAVORABLE',
      'sameRetains':out[2][1]==out[1][1],
      'reverseWorks':out[3][1]=={'UP':'FAVORABLE','DOWN':'ADVERSE'},
      'invalidResets':out[4][1]=={'UP':'UNKNOWN','DOWN':'UNKNOWN'},
      'reprimeUnknown':out[5][1]=={'UP':'UNKNOWN','DOWN':'UNKNOWN'},
      'gateOnlyLegalPureUnderwaterAdverse':gate_decision(True,True,True,'ADVERSE'),
      'favorableNeverGated':not gate_decision(True,True,True,'FAVORABLE'),
      'unknownNeverGated':not gate_decision(True,True,True,'UNKNOWN'),
      'busyOrIllegalNeverGated':not gate_decision(False,True,True,'ADVERSE'),
      'oppositeResidualNeverGated':not gate_decision(True,False,True,'ADVERSE'),
      'notUnderwaterNeverGated':not gate_decision(True,True,False,'ADVERSE'),
    }
    return {'checks':checks,'pass':all(checks.values()),'trace':out}

class PublicBidLegSim(LadderSim):
    def __init__(self,tape,enabled:bool):
        super().__init__(tape,1,True,False,False)
        self.enabled=bool(enabled); self.obs=BidLegObserver(); self.obsHasher=hashlib.sha256()
        self.legalTrace=[]; self.vetoed=[]; self.allowedUnderwater=[]; self.outOfScope=0; self.unknownEligible=0
    def _observe(self,t,qv):
        self.obs.update(qv)
        rec={'t':int(t),'upBid':None if qv is None else float(qv['UP']['bid']),'downBid':None if qv is None else float(qv['DOWN']['bid']),'upLeg':self.obs.leg['UP'],'downLeg':self.obs.leg['DOWN']}
        self.obsHasher.update((json.dumps(rec,sort_keys=True,separators=(',',':'))+'\n').encode())
    def _probe_native_legal(self,t,qv,end):
        if int(end)-int(t)<=lad.NO_NEW_EXPOSURE_MS:return None
        side=self._direction(qv); bid=float(qv[side]['bid']); ask=float(qv[side]['ask'])
        if bid<=EPS or bid>=1.0-EPS or ask<=bid+EPS:return None
        qty=1.0/bid if bid>EPS else math.inf
        if not math.isfinite(qty) or qty<=EPS or qty>12.0+EPS:return None
        sid=1
        if sid in self.slot_key:return None
        if self.pair_guard and not self._pair_ok(side,bid):return None
        return {'side':side,'bid':bid,'ask':ask,'qty':qty,'slotId':sid}
    def _open_free_slots(self,t,qv,end):
        pr=self._probe_native_legal(t,qv,end)
        if pr is None:return super()._open_free_slots(t,qv,end)
        side=pr['side']; opp='DOWN' if side=='UP' else 'UP'
        sameq=sum(float(a) for a,_ in self.un[side]); oppq=sum(float(a) for a,_ in self.un[opp])
        pure=bool(sameq>EPS and oppq<=EPS)
        avg=self.unmatched_avg(side) if sameq>EPS else None
        underwater=bool(avg is not None and pr['bid']<=float(avg)+1e-10)
        leg=str(self.obs.leg[side]); eligible=bool(pure and underwater)
        rec={'t':int(t),'side':side,'price':pr['bid'],'qty':pr['qty'],'sameUnmatchedQty':sameq,'oppUnmatchedQty':oppq,'avgUnmatchedCost':avg,'underwater':underwater,'leg':leg,'pureSameSide':pure,'nativeAdmissible':True,'submitsBefore':int(self.submits),'fillsBefore':int(self.fills),'invUP':float(self.inv['UP']),'invDOWN':float(self.inv['DOWN']),'cost':float(self.cost)}
        if not pure and sameq>EPS:self.outOfScope+=1
        if eligible:
            self.legalTrace.append(dict(rec))
            if leg=='UNKNOWN':self.unknownEligible+=1
            if self.enabled and gate_decision(True,True,True,leg):
                rec['decision']='WITHHOLD'; self.vetoed.append(dict(rec)); self.veto['PUBLIC_BID_LEG_UNDERWATER_ADD']+=1; return
            before=self.n
            super()._open_free_slots(t,qv,end)
            if self.n>before:
                key=f'{side}_{before}'; rec['decision']='ALLOW_NATIVE'; rec['key']=key; self.allowedUnderwater.append(dict(rec))
            else:
                rec['decision']='NATIVE_NO_SUBMIT_AFTER_PROBE'; self.allowedUnderwater.append(dict(rec))
            return
        return super()._open_free_slots(t,qv,end)
    def run_bid_leg(self,winner):
        updates=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])))
        first=int(self.meta['firstReceivedMs']); base.ex.advance_to(self.bt,first)
        end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
        for u in updates:
            t=int(u[1]); base.ex.advance_to(self.bt,t); self.process(t); self.cancel_expired(t); self._refresh_slots(t)
            base.apply(self.book,u); qv=base.quotes(self.book); self._observe(t,qv)
            if qv:self._open_free_slots(t,qv,end)
        end2=int(self.meta['lastReceivedMs']); base.ex.advance_to(self.bt,end2); self.process(end2); self._refresh_slots(end2)
        win=str(winner).upper(); pnl=float(self.inv.get(win,0.0)-self.cost); floor=float(min(self.inv.values())-self.cost); best=float(max(self.inv.values())-self.cost)
        for e in self.allowedUnderwater:
            k=e.get('key'); o=self.orders.get(k) if k else None
            e['confirmedCum']=float(o.get('cum') or 0.0) if o else 0.0; e['confirmedFill']=bool(e['confirmedCum']>EPS)
        return {'submits':int(self.submits),'fillEvents':int(self.fills),'filledQty':float(self.inv['UP']+self.inv['DOWN']),'upQty':float(self.inv['UP']),'downQty':float(self.inv['DOWN']),'buyNotional':float(self.cost),'pnlDiagnosticOnly':pnl,'floor':floor,'best':best,'slotReopens':int(self.slot_reopens),'vetoCounts':dict(self.veto),'observerHash':self.obsHasher.hexdigest(),'legalTrace':self.legalTrace[:1200],'vetoed':self.vetoed[:600],'allowedUnderwater':self.allowedUnderwater[:600],'outOfScope':int(self.outOfScope),'unknownEligible':int(self.unknownEligible)}


def behavior_snapshot(sim,r):
    orders=[]
    for k,o in sorted(sim.orders.items()):
        orders.append({'key':k,'n':int(o['n']),'side':o['side'],'price':float(o['price']),'qty':float(o['qty']),'cum':float(o['cum']),'placed':int(o['placed']),'status':str(o.get('status'))})
    obj={'result':{k:r[k] for k in ['submits','fillEvents','filledQty','upQty','downQty','buyNotional','pnlDiagnosticOnly','floor','best','slotReopens']},'orders':orders,'slotHistory':sim.slot_history,'vetoCounts':dict(sim.veto)}
    raw=json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False)
    return {'sha256':hashlib.sha256(raw.encode()).hexdigest(),'object':obj}


def run_public(tape,winner,enabled):
    sim=PublicBidLegSim(tape,enabled)
    try:r=sim.run_bid_leg(winner); snap=behavior_snapshot(sim,r)
    finally:sim.close()
    return r,snap

def run_native(tape,winner):
    sim=LadderSim(tape,1,True,False,False)
    try:r=sim.run_ladder(winner); snap=behavior_snapshot(sim,r)
    finally:sim.close()
    # normalize names to public runner schema subset
    rr={'submits':int(r['submits']),'fillEvents':int(r['fillEvents']),'filledQty':float(r['filledQty']),'upQty':float(r['upQty']),'downQty':float(r['downQty']),'buyNotional':float(r['buyNotional']),'pnlDiagnosticOnly':float(r['pnlDiagnosticOnly']),'floor':float(r['floor']),'best':float(r['best']),'slotReopens':int(r['slotReopens'])}
    return rr,snap

def maxdd(pnls):
    c=0.; peak=0.; dd=0.
    for p in pnls:
        c+=p; peak=max(peak,c); dd=max(dd,peak-c)
    return dd

def summarize(rows,arm):
    xs=[r for r in rows if r['arm']==arm]; pn=[r['pnl'] for r in xs]; fills=sum(r['fills'] for r in xs); bn=sum(r['buyNotional'] for r in xs); best=max(pn); worst=min(pn)
    return {'markets':len(xs),'aggregatePnl':sum(pn),'positiveMarkets':sum(p>EPS for p in pn),'winRate':sum(p>EPS for p in pn)/len(pn),'worstPnl':worst,'bestPnl':best,'leaveOneBestOut':sum(pn)-best,'tradeCoverage':sum(r['fills']>0 for r in xs)/len(xs),'totalFills':fills,'totalBuyNotional':bn,'maxDrawdown':maxdd(pn)}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=False); ap.add_argument('--output',required=True); ap.add_argument('--fixture-only',action='store_true'); a=ap.parse_args()
    fx=fixture_contract()
    if a.fixture_only:
        out={'version':'MVPS_PAIR1_PUBLIC_BID_LEG_STAGE0_FIXTURE_V2','fixture':fx}
        op=(Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out),flush=True);return
    if not fx['pass']:raise RuntimeError('fixture fail')
    tmp=Path(tempfile.mkdtemp(prefix='mvps_bidleg4_v2_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}; rows=[]; diagnostics={}
        # Extra BE 1: pure native first-anchor parity branch.
        mid0=MIDS[0]; nat,snat=run_native(tmp/'tapes'/f'{mid0}.json.xz',co[mid0]['winner'])
        diagnostics['firstAnchorNativeParityBranch']={'marketId':mid0,'result':nat,'behaviorHash':snat['sha256']}
        publicRuns={}; publicSnaps={}
        for mid in MIDS:
            tape=tmp/'tapes'/f'{mid}.json.xz'; winner=co[mid]['winner']
            for arm,en in [('N_PAIR1_OBSERVER_DISABLED',False),('X_PUBLIC_BID_LEG_GATE',True)]:
                r,s=run_public(tape,winner,en); publicRuns[(mid,arm)]=r; publicSnaps[(mid,arm)]=s
                row={'marketId':mid,'winnerPostHocOnly':str(winner).upper(),'arm':arm,'pnl':float(r['pnlDiagnosticOnly']),'fills':int(r['fillEvents']),'submits':int(r['submits']),'buyNotional':float(r['buyNotional']),'floor':float(r['floor']),'best':float(r['best']),'observerHash':r['observerHash'],'legalUnderwaterProposals':len(r['legalTrace']),'vetoedAdverse':len(r['vetoed']),'allowedUnderwater':len(r['allowedUnderwater']),'allowedFavorable':sum(e.get('leg')=='FAVORABLE' for e in r['allowedUnderwater']),'allowedFavorableFilled':sum(e.get('leg')=='FAVORABLE' and e.get('confirmedFill') for e in r['allowedUnderwater']),'allowedUnknown':sum(e.get('leg')=='UNKNOWN' for e in r['allowedUnderwater']),'behaviorHash':s['sha256']}
                rows.append(row); print(json.dumps({'marketId':mid,'arm':arm,'pnl':row['pnl'],'fills':row['fills'],'notional':row['buyNotional'],'legal':row['legalUnderwaterProposals'],'vetoed':row['vetoedAdverse'],'favAllowed':row['allowedFavorable'],'favFilled':row['allowedFavorableFilled']},ensure_ascii=False),flush=True)
        # Extra BE 2: X deterministic repeat on first anchor.
        xr,sxr=run_public(tmp/'tapes'/f'{mid0}.json.xz',co[mid0]['winner'],True)
        diagnostics['firstAnchorXRepeat']={'marketId':mid0,'behaviorHash':sxr['sha256'],'observerHash':xr['observerHash'],'exactBehaviorRepeat':sxr['sha256']==publicSnaps[(mid0,'X_PUBLIC_BID_LEG_GATE')]['sha256'],'exactObserverRepeat':xr['observerHash']==publicRuns[(mid0,'X_PUBLIC_BID_LEG_GATE')]['observerHash']}
        # L0/parity diagnostics.
        n0=publicRuns[(mid0,'N_PAIR1_OBSERVER_DISABLED')]; sn0=publicSnaps[(mid0,'N_PAIR1_OBSERVER_DISABLED')]
        diagnostics['firstAnchorObserverDisabledVsNative']={'exactBehaviorParity':sn0['sha256']==snat['sha256'],'nativeHash':snat['sha256'],'observerDisabledHash':sn0['sha256']}
        diagnostics['observerPolicyIndependent']={str(mid):publicRuns[(mid,'N_PAIR1_OBSERVER_DISABLED')]['observerHash']==publicRuns[(mid,'X_PUBLIC_BID_LEG_GATE')]['observerHash'] for mid in MIDS}
        diagnostics['archivalBaselineParity']={str(mid):{
            'pnl':abs(publicRuns[(mid,'N_PAIR1_OBSERVER_DISABLED')]['pnlDiagnosticOnly']-EXPECTED[mid]['pnl'])<=1e-9,
            'fills':publicRuns[(mid,'N_PAIR1_OBSERVER_DISABLED')]['fillEvents']==EXPECTED[mid]['fills'],
            'submits':publicRuns[(mid,'N_PAIR1_OBSERVER_DISABLED')]['submits']==EXPECTED[mid]['submits']
        } for mid in MIDS}
        # First divergence: N should submit same identity at first X veto.
        fd={}
        for mid in MIDS:
            x=publicRuns[(mid,'X_PUBLIC_BID_LEG_GATE')]; n=publicRuns[(mid,'N_PAIR1_OBSERVER_DISABLED')]
            if x['vetoed']:
                v=x['vetoed'][0]; matches=[e for e in n['allowedUnderwater'] if e.get('t')==v.get('t') and e.get('side')==v.get('side') and abs(float(e.get('price'))-float(v.get('price')))<=1e-12 and abs(float(e.get('qty'))-float(v.get('qty')))<=1e-12]
                fd[str(mid)]={'veto':v,'nativeMatchCount':len(matches),'nativeWouldSubmit':any(e.get('key') for e in matches)}
            else:fd[str(mid)]={'veto':None,'nativeMatchCount':0,'nativeWouldSubmit':False}
        diagnostics['firstDivergence']=fd
        sN=summarize(rows,'N_PAIR1_OBSERVER_DISABLED'); sX=summarize(rows,'X_PUBLIC_BID_LEG_GATE')
        fillRet=sX['totalFills']/sN['totalFills']; notRet=sX['totalBuyNotional']/sN['totalBuyNotional']
        perMarketRet=[]
        for mid in MIDS:
            nr=next(r for r in rows if r['marketId']==mid and r['arm']=='N_PAIR1_OBSERVER_DISABLED'); xr0=next(r for r in rows if r['marketId']==mid and r['arm']=='X_PUBLIC_BID_LEG_GATE')
            perMarketRet.append({'marketId':mid,'fillRetention':xr0['fills']/nr['fills'] if nr['fills'] else None,'notionalRetention':xr0['buyNotional']/nr['buyNotional'] if nr['buyNotional'] else None,'retainHalfBoth':nr['fills']>0 and xr0['fills']>=.5*nr['fills'] and xr0['buyNotional']>=.5*nr['buyNotional']})
        G=[m for m in MIDS if EXPECTED[m]['pnl']>0]; gN=sum(next(r['pnl'] for r in rows if r['marketId']==m and r['arm']=='N_PAIR1_OBSERVER_DISABLED') for m in G); gX=sum(next(r['pnl'] for r in rows if r['marketId']==m and r['arm']=='X_PUBLIC_BID_LEG_GATE') for m in G)
        WN=max(0.,-sN['worstPnl']); WX=max(0.,-sX['worstPnl']); loboImprove=sX['leaveOneBestOut']>sN['leaveOneBestOut']+1e-9 if sN['leaveOneBestOut']<0 else sX['leaveOneBestOut']>=sN['leaveOneBestOut']-1e-9
        favWitnessByMarket={str(mid):sum(e.get('leg')=='FAVORABLE' and e.get('confirmedFill') for e in publicRuns[(mid,'X_PUBLIC_BID_LEG_GATE')]['allowedUnderwater']) for mid in MIDS}
        gates={
          'fixturePass':fx['pass'],
          'archivalBaselineParity4of4':all(all(z.values()) for z in diagnostics['archivalBaselineParity'].values()),
          'firstAnchorObserverBehaviorParity':diagnostics['firstAnchorObserverDisabledVsNative']['exactBehaviorParity'],
          'xRepeatParity':diagnostics['firstAnchorXRepeat']['exactBehaviorRepeat'] and diagnostics['firstAnchorXRepeat']['exactObserverRepeat'],
          'publicObserverPolicyIndependent4of4':all(diagnostics['observerPolicyIndependent'].values()),
          'nativeAdmissibleAdverseVetoExercised':sum(len(publicRuns[(m,'X_PUBLIC_BID_LEG_GATE')]['vetoed']) for m in MIDS)>=1,
          'firstDivergenceMatchesNativeSubmit':all((d['veto'] is None) or (d['nativeMatchCount']>=1 and d['nativeWouldSubmit']) for d in fd.values()),
          'favorableUnderwaterReadmissionFillExercised':sum(favWitnessByMarket.values())>=1,
          'positiveAnchorFavorableReadmissionWitness':favWitnessByMarket[str(1945866)]>=1 or favWitnessByMarket[str(1945898)]>=1,
          'tradeCoverage75pct':sX['tradeCoverage']>=.75 and sX['tradeCoverage']>=.75*sN['tradeCoverage'],
          'fillRetention75pct':fillRet>=.75,
          'notionalRetention75pct':notRet>=.75,
          'perMarketHalfRetention75pct':sum(x['retainHalfBoth'] for x in perMarketRet)>=3,
          'aggregateImprovedOrPositive':sX['aggregatePnl']>sN['aggregatePnl']+1e-9 or sX['aggregatePnl']>0,
          'worstLossQuarterImprovement':WN==0 or WX<=.75*WN+1e-9,
          'drawdownNonWorse':sX['maxDrawdown']<=sN['maxDrawdown']+1e-9,
          'tailImprovementBeatsActivityCompression':WN==0 or (WX/WN)<min(fillRet,notRet)-1e-12,
          'winnerGroupRetention75pct':gX>=.75*gN-1e-9,
          'anchor1945866Retains50pctAndPositive':next(r['pnl'] for r in rows if r['marketId']==1945866 and r['arm']=='X_PUBLIC_BID_LEG_GATE')>0 and next(r['pnl'] for r in rows if r['marketId']==1945866 and r['arm']=='X_PUBLIC_BID_LEG_GATE')>=.5*EXPECTED[1945866]['pnl'],
          'anchor1945898Retains50pctAndPositive':next(r['pnl'] for r in rows if r['marketId']==1945898 and r['arm']=='X_PUBLIC_BID_LEG_GATE')>0 and next(r['pnl'] for r in rows if r['marketId']==1945898 and r['arm']=='X_PUBLIC_BID_LEG_GATE')>=.5*EXPECTED[1945898]['pnl'],
          'leaveOneBestOutImprovedTowardZero':loboImprove,
        }
        go=all(gates.values())
        out={'version':'MVPS_PAIR1_PUBLIC_BID_LEG_UNDERWATER_ADD_FALSIFICATION_V2_SMOKE4','date':'2026-09-09','researchOnly':True,'runtimeAuthority':False,'markets':MIDS,'fixture':fx,'rows':rows,'summary':{'N':sN,'X':sX,'delta':{'aggregatePnl':sX['aggregatePnl']-sN['aggregatePnl'],'worstPnl':sX['worstPnl']-sN['worstPnl'],'bestPnl':sX['bestPnl']-sN['bestPnl'],'leaveOneBestOut':sX['leaveOneBestOut']-sN['leaveOneBestOut'],'fillRetention':fillRet,'notionalRetention':notRet,'winnerGroupRetention':gX/gN if gN else None},'perMarketRetention':perMarketRet},'favWitnessByMarket':favWitnessByMarket,'diagnostics':diagnostics,'gates':gates,'goFixed8':go,'branchEquivalentsUsed':10,'boundary':['public bid leg updates every received valid quote independent of own fills/actions','gate only after full native cutoff/book/qty/slot/Pair legality','treatment only pure same-side underwater ADD with no opposite unmatched residual','withhold only if public leg ADVERSE','FAVORABLE/UNKNOWN retain native full ADD','no dwell/count/epsilon sweep/exposure cap/resize/reprice/insurance/Target runtime input','realistic HFT same tape/queue/latency/TTL/<=180s','no fresh/reserve/no8781/no dream fill']}
        op=(Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
        print(json.dumps({'ok':True,'summary':out['summary'],'favWitnessByMarket':favWitnessByMarket,'gates':gates,'goFixed8':go,'BE':10},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
