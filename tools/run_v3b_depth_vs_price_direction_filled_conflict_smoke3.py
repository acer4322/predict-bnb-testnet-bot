from __future__ import annotations
import argparse,json,os,tempfile,zipfile,sys
from pathlib import Path
try:
    import tools.run_v3b_depth_vs_price_direction_one_shot_smoke3 as v1
except ModuleNotFoundError:
    sys.path.insert(0,str(Path.cwd().parent))
    import run_v3b_depth_vs_price_direction_one_shot_smoke3 as v1

EPS=v1.EPS
MARKETS=[1823553,1823603,1823611]
FROZEN={
 1823553:{'submitT':1788158755287,'side':'DOWN','price':0.40,'qty':2.5,'baselineFillT':1788158760692,'baselineFillQty':2.5},
 1823603:{'submitT':1788159310646,'side':'DOWN','price':0.37,'qty':2.7027027027027026,'baselineFillT':1788159314254,'baselineFillQty':2.7027027027027026},
 1823611:{'submitT':1788159614469,'side':'UP','price':0.38,'qty':2.6315789473684212,'baselineFillT':1788159619272,'baselineFillQty':2.6315789473684212},
}

class FilledConflict(v1.DirectionOneShot):
    def __init__(self,tape,mid,mode='CONTROL',target=None):
        super().__init__(tape,mode,target);self.mid=int(mid);self.frozen=FROZEN[self.mid]

    def _base_open(self,t,qv,end):
        return v1.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)

    def _open_one_option(self,t,qv,end):
        ft=int(self.frozen['submitT'])
        if self.mode=='CONTROL' and self.target_found is None and int(t)==ft:
            q=self._qualifies(qv)
            if q is not None:
                snap,dig=self._snapshot_prefix(t);before=len(self.slot_history)
                out=self._base_open(t,qv,end)
                ev=[x for x in self.slot_history[before:] if x.get('event')=='ROLE_SLOT_SUBMIT']
                hit=next((x for x in ev if str(x.get('role'))=='SATELLITE_EXPAND' and str(x.get('side'))==self.frozen['side'] and abs(float(x.get('price') or 0)-float(self.frozen['price']))<=1e-12 and abs(float(x.get('qty') or 0)-float(self.frozen['qty']))<=1e-9),None)
                if hit is not None:
                    self.target_found={'t':int(t),'prefixDigest':dig,'prefix':snap,**q,'controlSubmit':v1.norm(hit),'frozen':self.frozen}
                return out
        if self.mode=='TREATMENT' and not self.override_used and self.target is not None and int(t)==ft:
            q=self._qualifies(qv)
            if q is not None:
                snap,dig=self._snapshot_prefix(t)
                if dig==self.target['prefixDigest']:
                    before=len(self.slot_history);orig=v1.v3b.FifoAggregateResponsibilityLadderV3B._direction(self,qv);self.override_active=True
                    try:out=self._base_open(t,qv,end)
                    finally:self.override_active=False
                    self.override_used=True
                    ev=[x for x in self.slot_history[before:] if x.get('event')=='ROLE_SLOT_SUBMIT']
                    newdir='UP' if self._mid(qv,'UP')>=self._mid(qv,'DOWN') else 'DOWN'
                    self.intervention={'t':int(t),'prefixDigest':dig,'prefixParity':True,'originalDepthDirection':orig,'midpointDirection':newdir,'dominantMidBefore':q['dominantMid'],'newSlotEvents':v1.norm(ev)}
                    return out
        return self._base_open(t,qv,end)

def run_market(tape,mid,winner):
    A=FilledConflict(tape,mid,'CONTROL')
    try:ra=A.run_probe()
    finally:A.close()
    target=ra.get('directionTarget')
    if not target:return {'valid':False,'reason':'CONTROL_TARGET_NOT_REPRODUCED'}
    ce=target['controlSubmit'];ckey=str(ce['key'])
    fills=[x for x in ra.get('fillSideSequence',[]) if str(x.get('key'))==ckey]
    control_fill_qty=sum(float(x.get('incQty') or 0) for x in fills)
    control_fill_ok=control_fill_qty+1e-9>=float(FROZEN[mid]['baselineFillQty'])
    favored=str(target['dominant'])
    B=FilledConflict(tape,mid,'TREATMENT',target)
    try:rb=B.run_probe()
    finally:B.close()
    ma,mb=v1.metrics(ra,winner,favored),v1.metrics(rb,winner,favored);fillret=mb['fills']/ma['fills'] if ma['fills'] else None
    checks={'controlTargetReproduced':True,'controlTargetPhysicalFill':control_fill_ok,'controlTargetFillQty':control_fill_qty,
      'prefixParity':bool((rb.get('directionIntervention') or {}).get('prefixParity')),'overrideUsedOnce':bool(rb.get('directionOverrideUsed')),
      'controlLedgerClean':not bool(ma['ledgerViolations']),'treatmentLedgerClean':not bool(mb['ledgerViolations']),
      'max4Control':ma['maxSlots']<=4,'max4Treatment':mb['maxSlots']<=4,'twoSidedRetained':(not ma['twoSided']) or mb['twoSided'],
      'fillRetention':fillret,'fillRetentionGe90':fillret is None or fillret>=.90}
    delta={k:mb[k]-ma[k] for k in ('winnerPnlPostHoc','fixedFavoredPayoff','oppositePayoff','terminalFloor','terminalBest','fills','submits','alternations')}
    valid=all(bool(checks[k]) for k in ('controlTargetReproduced','controlTargetPhysicalFill','prefixParity','overrideUsedOnce','controlLedgerClean','treatmentLedgerClean','max4Control','max4Treatment'))
    return {'valid':valid,'target':target,'control':ma,'treatment':mb,'deltasTreatmentMinusControl':delta,'checks':checks,'intervention':rb.get('directionIntervention')}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();rows=[]
    with tempfile.TemporaryDirectory(prefix='v3b_dir_fill_') as td:
      root=Path(td)
      with zipfile.ZipFile(a.bundle) as z:
        co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
        for mid in MARKETS:z.extract(f'tapes/{mid}.json.xz',root)
      for i,mid in enumerate(MARKETS,1):
        r=run_market(root/'tapes'/f'{mid}.json.xz',mid,str(co[mid]['winner']).upper());r['marketId']=mid;rows.append(r)
        print(json.dumps({'progress':i,'marketId':mid,'valid':r.get('valid'),'delta':r.get('deltasTreatmentMinusControl'),'checks':r.get('checks'),'intervention':r.get('intervention')},ensure_ascii=False),flush=True)
    valid=[r for r in rows if r.get('valid')]
    agg={'markets':len(rows),'validMarkets':len(valid),'allCorrectnessPass':len(valid)==len(rows),'allFillRetentionGe90':all(r['checks']['fillRetentionGe90'] for r in valid) if valid else False,
      'sumDeltaFixedFavoredPayoff':sum(r['deltasTreatmentMinusControl']['fixedFavoredPayoff'] for r in valid),'sumDeltaOppositePayoff':sum(r['deltasTreatmentMinusControl']['oppositePayoff'] for r in valid),
      'sumDeltaWinnerPnlPostHoc':sum(r['deltasTreatmentMinusControl']['winnerPnlPostHoc'] for r in valid),'sumDeltaFloor':sum(r['deltasTreatmentMinusControl']['terminalFloor'] for r in valid),
      'sumDeltaFills':sum(r['deltasTreatmentMinusControl']['fills'] for r in valid),'sumDeltaAlternations':sum(r['deltasTreatmentMinusControl']['alternations'] for r in valid),
      'favoredImprovedMarkets':sum(r['deltasTreatmentMinusControl']['fixedFavoredPayoff']>1e-9 for r in valid),'favoredHarmedMarkets':sum(r['deltasTreatmentMinusControl']['fixedFavoredPayoff']<-1e-9 for r in valid),
      'floorImprovedMarkets':sum(r['deltasTreatmentMinusControl']['terminalFloor']>1e-9 for r in valid),'floorHarmedMarkets':sum(r['deltasTreatmentMinusControl']['terminalFloor']<-1e-9 for r in valid)}
    promote=bool(agg['allCorrectnessPass'] and agg['allFillRetentionGe90'] and agg['sumDeltaFixedFavoredPayoff']>=-1e-9 and agg['sumDeltaFloor']>=-1e-9 and agg['sumDeltaFills']>=0)
    out={'version':'V3B_DEPTH_VS_PRICE_DIRECTION_FILLED_CONFLICT_SMOKE3','date':'2026-09-07','researchOnly':True,'winnerRuntimeInputUsed':False,'markets':MARKETS,'frozenTargets':FROZEN,'rows':rows,'aggregate':agg,'decision':'PROMISING_FOR_NEXT_FALSIFICATION' if promote else 'DO_NOT_PROMOTE_GLOBAL_DIRECTION_REPLACEMENT','boundary':['first baseline physically-filled conflict carrier only','one direction override receipt per market','same frozen V3B suffix','fixed favored side pre-outcome','winner posthoc only','realistic HFT','no dream fill','max4','NEW24-B untouched','no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg,'decision':out['decision']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
