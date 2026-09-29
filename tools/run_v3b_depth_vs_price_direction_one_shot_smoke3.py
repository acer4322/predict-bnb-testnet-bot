from __future__ import annotations
import argparse,copy,hashlib,json,math,os,tempfile,zipfile
from pathlib import Path
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b

EPS=v3b.EPS;base=v3b.base
MARKETS=[1823553,1823603,1823611]

def norm(x):
    if isinstance(x,dict):return {str(k):norm(v) for k,v in sorted(x.items(),key=lambda kv:str(kv[0]))}
    if isinstance(x,(list,tuple)):return [norm(v) for v in x]
    if isinstance(x,(int,float,str,bool)) or x is None:return x
    try:return float(x)
    except Exception:return str(x)

def digest(x):
    return hashlib.sha256(json.dumps(norm(x),sort_keys=True,separators=(',',':')).encode()).hexdigest()

class DirectionOneShot(v3b.FifoAggregateResponsibilityLadderV3B):
    def __init__(self,tape,mode='CONTROL',target=None):
        super().__init__(tape)
        self.mode=mode;self.target=copy.deepcopy(target);self.target_found=None;self.override_active=False;self.override_used=False
        self.shadow_up=0.0;self.shadow_down=0.0;self._seq_seen=0;self.last_fill_class=None
        self.intervention=None

    def process(self,t):
        super().process(t)
        seq=self.fill_side_sequence
        while self._seq_seen<len(seq):
            f=seq[self._seq_seen];side=str(f['side']);q=float(f['incQty']);net=self.shadow_up-self.shadow_down
            dom='UP' if net>EPS else 'DOWN' if net<-EPS else None
            cl='BASE' if dom is None else ('EXPAND' if side==dom else 'REPAIR')
            self.last_fill_class=cl
            if side=='UP':self.shadow_up+=q
            else:self.shadow_down+=q
            self._seq_seen+=1

    def _snapshot_prefix(self,t):
        live={}
        for key,o in self.orders.items():
            if key in set(self.slot_key.values()) or base.v2.base.live(str(o.get('status') or '')):
                live[key]={k:o.get(k) for k in ('n','side','price','qty','cum','placed','status','cancelRequested')}
        snap={
            't':int(t),'n':int(self.n),'inv':dict(self.inv),'cost':float(self.cost),'slotKey':dict(self.slot_key),'keyRole':dict(self.key_role),
            'liveOrders':live,'qLadder':copy.deepcopy(self.q_ladder),'qPendingActive':copy.deepcopy(self.q_pending_active),
            'responsibilities':self.serializable_lots(),'paymentRows':copy.deepcopy(self.resp_payment_rows),
            'placeHist':list(self.placeHist),'fillSequence':copy.deepcopy(self.fill_side_sequence)
        }
        return norm(snap),digest(snap)

    def _dominant(self):
        u=float(self.inv['UP']);d=float(self.inv['DOWN'])
        return 'UP' if u>d+EPS else 'DOWN' if d>u+EPS else None

    def _mid(self,qv,side):
        return (float(qv[side]['bid'])+float(qv[side]['ask']))/2.0

    def _direction(self,qv):
        if self.override_active:
            upm=self._mid(qv,'UP');dnm=self._mid(qv,'DOWN')
            return 'UP' if upm>=dnm else 'DOWN'
        return super()._direction(qv)

    def _qualifies(self,qv):
        if self.last_fill_class!='REPAIR':return None
        if self.q_pending_active is not None:return None
        dom=self._dominant()
        if dom is None:return None
        dec=base.MinimalPairRoleSim._role_decision(self,qv)
        side,role=dec[0],dec[1]
        if side!=dom or role!='SATELLITE_EXPAND':return None
        dm=self._mid(qv,dom)
        if not dm<0.5-EPS:return None
        depth_dir=super()._direction(qv)
        if depth_dir!=dom:return None
        return {'dominant':dom,'dominantMid':dm,'depthDirection':depth_dir,'baselineDecision':norm(dec)}

    def _open_one_option(self,t,qv,end):
        # CONTROL discovers the first actual conflict submit; TREATMENT reuses its exact prefix/time.
        if self.mode=='CONTROL' and self.target_found is None:
            q=self._qualifies(qv)
            if q is not None:
                snap,dig=self._snapshot_prefix(t);before=len(self.slot_history)
                out=super()._open_one_option(t,qv,end)
                ev=[x for x in self.slot_history[before:] if x.get('event')=='ROLE_SLOT_SUBMIT']
                hit=next((x for x in ev if str(x.get('role'))=='SATELLITE_EXPAND' and str(x.get('side'))==q['dominant']),None)
                if hit is not None:
                    self.target_found={'t':int(t),'prefixDigest':dig,'prefix':snap,**q,'controlSubmit':norm(hit)}
                return out
        if self.mode=='TREATMENT' and not self.override_used and self.target is not None and int(t)==int(self.target['t']):
            q=self._qualifies(qv)
            if q is not None:
                snap,dig=self._snapshot_prefix(t)
                if dig==self.target['prefixDigest']:
                    before=len(self.slot_history);orig=super()._direction(qv);self.override_active=True
                    try:out=super()._open_one_option(t,qv,end)
                    finally:self.override_active=False
                    self.override_used=True
                    ev=[x for x in self.slot_history[before:] if x.get('event')=='ROLE_SLOT_SUBMIT']
                    newdir='UP' if self._mid(qv,'UP')>=self._mid(qv,'DOWN') else 'DOWN'
                    self.intervention={'t':int(t),'prefixDigest':dig,'prefixParity':True,'originalDepthDirection':orig,'midpointDirection':newdir,
                                       'dominantMidBefore':q['dominantMid'],'newSlotEvents':norm(ev)}
                    return out
        return super()._open_one_option(t,qv,end)

    def run_probe(self):
        r=super().run_qty('__UNSCORED__')
        r['directionProbeMode']=self.mode;r['directionTarget']=self.target_found if self.mode=='CONTROL' else self.target
        r['directionIntervention']=self.intervention;r['directionOverrideUsed']=bool(self.override_used)
        return r

def metrics(r,winner,favored):
    up=float(r['upQty'])-float(r['buyNotional']);dn=float(r['downQty'])-float(r['buyNotional'])
    return {
      'winnerPnlPostHoc':up if winner=='UP' else dn,'fixedFavoredSide':favored,'fixedFavoredPayoff':up if favored=='UP' else dn,
      'oppositePayoff':dn if favored=='UP' else up,'terminalFloor':float(r['floor']),'terminalBest':float(r['best']),
      'fills':int(r['fillEvents']),'submits':int(r['submits']),'alternations':int(r.get('fillSideAlternations') or 0),
      'twoSided':bool(r.get('twoSidedMaterialized')),'ledgerViolations':(r.get('quantityLedgerSummary') or {}).get('invariantViolations') or {},
      'maxSlots':int(r.get('maxSimultaneousSlots') or 0)
    }

def run_market(tape,winner):
    A=DirectionOneShot(tape,'CONTROL')
    try:ra=A.run_probe()
    finally:A.close()
    target=ra.get('directionTarget')
    if not target:return {'valid':False,'reason':'CONTROL_NO_CONFLICT'}
    favored=str(target['dominant'])
    B=DirectionOneShot(tape,'TREATMENT',target)
    try:rb=B.run_probe()
    finally:B.close()
    ma,mb=metrics(ra,winner,favored),metrics(rb,winner,favored)
    fillret=mb['fills']/ma['fills'] if ma['fills'] else None
    checks={
      'controlConflictExists':True,
      'prefixParity':bool((rb.get('directionIntervention') or {}).get('prefixParity')),
      'overrideUsedOnce':bool(rb.get('directionOverrideUsed')),
      'controlLedgerClean':not bool(ma['ledgerViolations']),'treatmentLedgerClean':not bool(mb['ledgerViolations']),
      'max4Control':ma['maxSlots']<=4,'max4Treatment':mb['maxSlots']<=4,
      'twoSidedRetained':(not ma['twoSided']) or mb['twoSided'],'fillRetention':fillret,'fillRetentionGe90':fillret is None or fillret>=0.90
    }
    deltas={k:mb[k]-ma[k] for k in ('winnerPnlPostHoc','fixedFavoredPayoff','oppositePayoff','terminalFloor','terminalBest','fills','submits','alternations')}
    valid=all(checks[k] for k in ('controlConflictExists','prefixParity','overrideUsedOnce','controlLedgerClean','treatmentLedgerClean','max4Control','max4Treatment'))
    return {'valid':valid,'target':target,'control':ma,'treatment':mb,'deltasTreatmentMinusControl':deltas,'checks':checks,'intervention':rb.get('directionIntervention')}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    rows=[]
    with tempfile.TemporaryDirectory(prefix='v3b_dir_') as td:
      root=Path(td)
      with zipfile.ZipFile(a.bundle) as z:
        co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
        for mid in MARKETS:z.extract(f'tapes/{mid}.json.xz',root)
      for i,mid in enumerate(MARKETS,1):
        row=run_market(root/'tapes'/f'{mid}.json.xz',str(co[mid]['winner']).upper());row['marketId']=mid;rows.append(row)
        print(json.dumps({'progress':i,'marketId':mid,'valid':row.get('valid'),'delta':row.get('deltasTreatmentMinusControl'),'checks':row.get('checks'),'target':row.get('target')},ensure_ascii=False),flush=True)
    valid=[r for r in rows if r.get('valid')]
    agg={
      'markets':len(rows),'validMarkets':len(valid),'allCorrectnessPass':len(valid)==len(rows),
      'allFillRetentionGe90':all(r['checks']['fillRetentionGe90'] for r in valid) if valid else False,
      'sumDeltaFixedFavoredPayoff':sum(r['deltasTreatmentMinusControl']['fixedFavoredPayoff'] for r in valid),
      'sumDeltaOppositePayoff':sum(r['deltasTreatmentMinusControl']['oppositePayoff'] for r in valid),
      'sumDeltaWinnerPnlPostHoc':sum(r['deltasTreatmentMinusControl']['winnerPnlPostHoc'] for r in valid),
      'sumDeltaFloor':sum(r['deltasTreatmentMinusControl']['terminalFloor'] for r in valid),
      'sumDeltaFills':sum(r['deltasTreatmentMinusControl']['fills'] for r in valid),
      'sumDeltaAlternations':sum(r['deltasTreatmentMinusControl']['alternations'] for r in valid),
      'favoredImprovedMarkets':sum(r['deltasTreatmentMinusControl']['fixedFavoredPayoff']>1e-9 for r in valid),
      'favoredHarmedMarkets':sum(r['deltasTreatmentMinusControl']['fixedFavoredPayoff']<-1e-9 for r in valid),
      'floorImprovedMarkets':sum(r['deltasTreatmentMinusControl']['terminalFloor']>1e-9 for r in valid),
      'floorHarmedMarkets':sum(r['deltasTreatmentMinusControl']['terminalFloor']<-1e-9 for r in valid)
    }
    promising=bool(agg['allCorrectnessPass'] and agg['allFillRetentionGe90'] and agg['sumDeltaFixedFavoredPayoff']>=-1e-9 and agg['sumDeltaFloor']>=-1e-9 and agg['sumDeltaFills']>=0)
    out={'version':'V3B_DEPTH_VS_PRICE_DIRECTION_ONE_SHOT_SMOKE3','date':'2026-09-07','researchOnly':True,'winnerRuntimeInputUsed':False,
         'markets':MARKETS,'rows':rows,'aggregate':agg,'decision':'PROMISING_FOR_NEXT_FALSIFICATION' if promising else 'DO_NOT_PROMOTE_GLOBAL_DIRECTION_REPLACEMENT',
         'boundary':['one receipt per market only','depth-sign direction replaced once by binary midpoint-supported side','role/candidate/Pair/exact-FIFO/execution logic otherwise frozen V3B','same frozen suffix on resulting state','fixed favored side frozen pre-outcome','winner posthoc only','realistic HFT','no dream fill','max4','NEW24-B untouched','no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'ok':True,'aggregate':agg,'decision':out['decision']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
