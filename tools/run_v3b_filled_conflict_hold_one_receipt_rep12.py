from __future__ import annotations
import argparse,json,os,sys,tempfile,zipfile
from pathlib import Path
try:
    import tools.run_v3b_depth_vs_price_direction_filled_conflict_replication12 as rep
    import tools.run_v3b_depth_vs_price_direction_one_shot_smoke3 as v1
except ModuleNotFoundError:
    sys.path.insert(0,str(Path.cwd().parent))
    import run_v3b_depth_vs_price_direction_filled_conflict_replication12 as rep
    import run_v3b_depth_vs_price_direction_one_shot_smoke3 as v1

class HoldOnce(v1.DirectionOneShot):
    def __init__(self,tape,frozen,target):
        super().__init__(tape,'TREATMENT',target);self.frozen=frozen
    def _base_open(self,t,qv,end):return v1.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
    def _open_one_option(self,t,qv,end):
        if not self.override_used and self.target is not None and int(t)==int(self.frozen['submitT']):
            q=self._qualifies(qv)
            if q is not None:
                snap,dig=self._snapshot_prefix(t)
                if dig==self.target['prefixDigest']:
                    self.override_used=True;self.intervention={'t':int(t),'prefixDigest':dig,'prefixParity':True,'action':'SKIP_ONE_CONFLICT_EXPAND_RECEIPT','wouldHaveDepthDirection':v1.v3b.FifoAggregateResponsibilityLadderV3B._direction(self,qv),'dominantMidBefore':q['dominantMid'],'newSlotEvents':[]}
                    return None
        return self._base_open(t,qv,end)

def run_market(tape,mid,winner,frozen):
    A=rep.FilledConflict(tape,mid,frozen,'CONTROL')
    try:ra=A.run_probe()
    finally:A.close()
    target=ra.get('directionTarget')
    if not target:return {'valid':False,'reason':'CONTROL_TARGET_NOT_REPRODUCED'}
    key=str(target['controlSubmit']['key']);fills=[x for x in ra.get('fillSideSequence',[]) if str(x.get('key'))==key];fq=sum(float(x.get('incQty') or 0) for x in fills);fillok=fq+1e-9>=float(frozen['baselineFillQty']);fav=str(target['dominant'])
    B=HoldOnce(tape,frozen,target)
    try:rb=B.run_probe()
    finally:B.close()
    ma,mb=v1.metrics(ra,winner,fav),v1.metrics(rb,winner,fav);ret=mb['fills']/ma['fills'] if ma['fills'] else None
    checks={'controlTargetReproduced':True,'controlTargetPhysicalFill':fillok,'controlTargetFillQty':fq,'prefixParity':bool((rb.get('directionIntervention') or {}).get('prefixParity')),'skipUsedOnce':bool(rb.get('directionOverrideUsed')),'controlLedgerClean':not bool(ma['ledgerViolations']),'holdLedgerClean':not bool(mb['ledgerViolations']),'max4Control':ma['maxSlots']<=4,'max4Hold':mb['maxSlots']<=4,'twoSidedRetained':(not ma['twoSided']) or mb['twoSided'],'fillRetention':ret}
    delta={k:mb[k]-ma[k] for k in ('winnerPnlPostHoc','fixedFavoredPayoff','oppositePayoff','terminalFloor','terminalBest','fills','submits','alternations')}
    valid=all(bool(checks[k]) for k in ('controlTargetReproduced','controlTargetPhysicalFill','prefixParity','skipUsedOnce','controlLedgerClean','holdLedgerClean','max4Control','max4Hold'))
    return {'valid':valid,'target':target,'control':ma,'hold':mb,'deltasHoldMinusControl':delta,'checks':checks,'intervention':rb.get('directionIntervention')}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--prereg',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();pr=json.loads(Path(a.prereg).read_text(encoding='utf-8'));frozen={int(k):v for k,v in pr['frozenTargets'].items()};markets=[int(x) for x in pr['markets']];rows=[]
    with tempfile.TemporaryDirectory(prefix='hold12_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in markets:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(markets,1):
            r=run_market(root/'tapes'/f'{mid}.json.xz',mid,str(co[mid]['winner']).upper(),frozen[mid]);r['marketId']=mid;rows.append(r);print(json.dumps({'progress':i,'of':len(markets),'marketId':mid,'valid':r.get('valid'),'delta':r.get('deltasHoldMinusControl'),'checks':r.get('checks')},ensure_ascii=False),flush=True)
    valid=[r for r in rows if r.get('valid')];cf=sum(r['control']['fills'] for r in valid);hf=sum(r['hold']['fills'] for r in valid)
    agg={'markets':len(rows),'validMarkets':len(valid),'allCorrectnessPass':len(valid)==len(rows),'aggregateControlFills':cf,'aggregateHoldFills':hf,'aggregateFillRetention':hf/cf if cf else None,'sumDeltaFixedFavoredPayoff':sum(r['deltasHoldMinusControl']['fixedFavoredPayoff'] for r in valid),'sumDeltaOppositePayoff':sum(r['deltasHoldMinusControl']['oppositePayoff'] for r in valid),'sumDeltaWinnerPnlPostHoc':sum(r['deltasHoldMinusControl']['winnerPnlPostHoc'] for r in valid),'sumDeltaFloor':sum(r['deltasHoldMinusControl']['terminalFloor'] for r in valid),'sumDeltaFills':sum(r['deltasHoldMinusControl']['fills'] for r in valid),'sumDeltaAlternations':sum(r['deltasHoldMinusControl']['alternations'] for r in valid),'favoredImprovedMarkets':sum(r['deltasHoldMinusControl']['fixedFavoredPayoff']>1e-9 for r in valid),'favoredHarmedMarkets':sum(r['deltasHoldMinusControl']['fixedFavoredPayoff']<-1e-9 for r in valid),'floorImprovedMarkets':sum(r['deltasHoldMinusControl']['terminalFloor']>1e-9 for r in valid),'floorHarmedMarkets':sum(r['deltasHoldMinusControl']['terminalFloor']<-1e-9 for r in valid)}
    out={'version':'V3B_FILLED_CONFLICT_HOLD_ONE_RECEIPT_REPLICATION12','date':'2026-09-07','researchOnly':True,'winnerRuntimeInputUsed':False,'preregisteredFrom':str(a.prereg),'markets':markets,'rows':rows,'aggregate':agg,'boundary':['same frozen physically-filled conflict targets','skip exactly one ordinary action receipt','no replacement Repair','next receipt onward frozen V3B','winner posthoc only','realistic HFT/no dream fill','max4','NEW24-B untouched','no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
