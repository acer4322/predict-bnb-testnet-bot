from __future__ import annotations
import argparse,copy,hashlib,json,math,os,sys,tempfile,zipfile
from pathlib import Path
try:
    import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b
except ModuleNotFoundError:
    sys.path.insert(0,str(Path.cwd().parent))
    import run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b
EPS=v3b.EPS

def norm(x):
    if isinstance(x,dict):return {str(k):norm(v) for k,v in sorted(x.items(),key=lambda kv:str(kv[0]))}
    if isinstance(x,(list,tuple)):return [norm(v) for v in x]
    if isinstance(x,float):return round(x,12) if math.isfinite(x) else str(x)
    return x

def sha(x):return hashlib.sha256(json.dumps(norm(x),sort_keys=True,separators=(',',':')).encode()).hexdigest()

class CompositeOneShot(v3b.FifoAggregateResponsibilityLadderV3B):
    def __init__(self,tape,frozen,mode):
        super().__init__(tape);self.frozen=frozen;self.mode=mode;self.targetObserved=None;self.targetKey=None;self.targetFillAccounting=[];self.overrideUsed=False
    def _snapshot(self,t):
        live={}
        for sid,key in self.slot_key.items():
            o=self.orders.get(key)
            if o: live[key]={'slot':int(sid),'side':o.get('side'),'price':float(o.get('price') or 0),'qty':float(o.get('qty') or 0),'cum':float(o.get('cum') or 0),'placed':int(o.get('placed') or 0),'cancelRequested':bool(o.get('cancelRequested')),'role':self.key_role.get(key)}
        return {'t':int(t),'inv':dict(self.inv),'cost':float(self.cost),'n':int(self.n),'slots':dict(self.slot_key),'live':live,'responsibilities':self.serializable_lots(),'payments':copy.deepcopy(self.resp_payment_rows),'qLadder':copy.deepcopy(self.q_ladder),'qPending':copy.deepcopy(self.q_pending_active)}
    def _is_target(self,t,side,role,p,q):
        f=self.frozen
        return int(t)==int(f['t']) and str(side)==str(f['repairSide']) and str(role)==str(f['role']) and abs(float(p)-float(f['normalPrice']))<=1e-10 and abs(float(q)-float(f['normalQty']))<=1e-8
    def _submit_role(self,t,side,role,p,q,proj,source):
        if not self.overrideUsed and self._is_target(t,side,role,p,q):
            snap=self._snapshot(t);dig=sha(snap);before_n=self.n
            if self.mode=='TREATMENT':
                q2=float(self.frozen['proposedCompositeQty']);ok=super()._submit_role(t,side,role,p,q2,proj,source);self.overrideUsed=True
                key=f'{side}_{before_n}' if ok else None;self.targetKey=key;self.targetObserved={'prefixDigest':dig,'prefix':snap,'submitted':bool(ok),'key':key,'t':int(t),'side':side,'role':role,'price':float(p),'baselineQtyArgument':float(q),'physicalQty':q2,'source':source}
                return ok
            ok=super()._submit_role(t,side,role,p,q,proj,source);self.overrideUsed=True
            key=f'{side}_{before_n}' if ok else None;self.targetKey=key;self.targetObserved={'prefixDigest':dig,'prefix':snap,'submitted':bool(ok),'key':key,'t':int(t),'side':side,'role':role,'price':float(p),'baselineQtyArgument':float(q),'physicalQty':float(q),'source':source}
            return ok
        return super()._submit_role(t,side,role,p,q,proj,source)
    def process(self,t):
        before=len(self.fill_accounting);super().process(t)
        if self.targetKey:
            for a in self.fill_accounting[before:]:
                if str(a.get('key'))==str(self.targetKey):self.targetFillAccounting.append(copy.deepcopy(a))
    def run_one(self):
        r=self.run_qty('__UNSCORED__');r['_targetObserved']=self.targetObserved;r['_targetFillAccounting']=self.targetFillAccounting;return r

def metrics(r,winner,favored):
    u=float(r['upQty']);d=float(r['downQty']);cost=float(r['buyNotional']);up=u-cost;dn=d-cost;fav=up if favored=='UP' else dn;opp=dn if favored=='UP' else up
    best=max(up,dn);floor=min(up,dn);rr=(abs(floor)/best if best>EPS and floor<0 else 0.0 if best>EPS else None)
    led=r.get('quantityLedgerSummary') or {};viol=led.get('invariantViolations') or {}
    return {'upPayoff':up,'downPayoff':dn,'fixedFavoredPayoff':fav,'oppositePayoff':opp,'terminalFloor':floor,'terminalBest':best,'riskRewardRatio':rr,'winnerPnlPostHoc':up if winner=='UP' else dn,'fills':int(r['fillEvents']),'submits':int(r['submits']),'alternations':int(r.get('fillSideAlternations') or 0),'maxSlots':int(r.get('maxSimultaneousSlots') or 0),'twoSided':bool(r.get('twoSidedMaterialized')),'ledgerViolations':viol}

def run_market(tape,mid,winner,frozen):
    A=CompositeOneShot(tape,frozen,'CONTROL')
    try:ra=A.run_one()
    finally:A.close()
    B=CompositeOneShot(tape,frozen,'TREATMENT')
    try:rb=B.run_one()
    finally:B.close()
    ta=ra.get('_targetObserved');tb=rb.get('_targetObserved');fa=ra.get('_targetFillAccounting') or [];fb=rb.get('_targetFillAccounting') or []
    favored=str(frozen['repairSide']);ma=metrics(ra,winner,favored);mb=metrics(rb,winner,favored)
    cfill=sum(float(x.get('confirmedQty') or 0) for x in fa);tfill=sum(float(x.get('confirmedQty') or 0) for x in fb);trepair=sum(float(x.get('matchedRepairQty') or 0) for x in fb);tover=sum(float(x.get('overflowQty') or 0) for x in fb);auth=float(frozen['venueMinOverflowQty'])
    delta={k:mb[k]-ma[k] for k in ('fixedFavoredPayoff','oppositePayoff','terminalFloor','terminalBest','winnerPnlPostHoc','fills','submits','alternations')}
    checks={'controlTargetSubmitted':bool(ta and ta.get('submitted')),'treatmentTargetSubmitted':bool(tb and tb.get('submitted')),'prefixParity':bool(ta and tb and ta['prefixDigest']==tb['prefixDigest']),'sameT':bool(ta and tb and ta['t']==tb['t']),'sameSide':bool(ta and tb and ta['side']==tb['side']),'sameRole':bool(ta and tb and ta['role']==tb['role']),'samePrice':bool(ta and tb and abs(float(ta['price'])-float(tb['price']))<=1e-12),'controlQtyExact':bool(ta and abs(float(ta['physicalQty'])-float(frozen['normalQty']))<=1e-8),'treatmentQtyExact':bool(tb and abs(float(tb['physicalQty'])-float(frozen['proposedCompositeQty']))<=1e-8),'controlLedgerClean':not bool(ma['ledgerViolations']),'treatmentLedgerClean':not bool(mb['ledgerViolations']),'max4Control':ma['maxSlots']<=4,'max4Treatment':mb['maxSlots']<=4,'twoSidedRetained':(not ma['twoSided']) or mb['twoSided'],'authorizedOverflowNotExceeded':tover<=auth+1e-8}
    valid=all(bool(v) for k,v in checks.items() if k!='twoSidedRetained') and checks['twoSidedRetained']
    return {'marketId':mid,'winnerPostHocOnly':winner,'fixedFavoredSide':favored,'frozenTarget':frozen,'control':ma,'treatment':mb,'deltaTreatmentMinusControl':delta,'checks':checks,'valid':valid,'physicalComposite':{'controlTargetFillQty':cfill,'treatmentTargetFillQty':tfill,'treatmentMatchedRepairQty':trepair,'treatmentOverflowQty':tover,'authorizedOverflowQty':auth,'confirmedOverflowExercised':tover>EPS,'targetFillAccounting':fb},'controlTarget':ta,'treatmentTarget':tb}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--prereg',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();pr=json.loads(Path(a.prereg).read_text(encoding='utf-8'));targets={int(k):v for k,v in pr['frozenTargets'].items()};mids=[int(x) for x in pr['markets']];rows=[]
    with tempfile.TemporaryDirectory(prefix='comp_smoke3_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            r=run_market(root/'tapes'/f'{mid}.json.xz',mid,str(co[mid]['winner']).upper(),targets[mid]);rows.append(r);print(json.dumps({'progress':i,'of':len(mids),'marketId':mid,'valid':r['valid'],'delta':r['deltaTreatmentMinusControl'],'checks':r['checks'],'physicalComposite':{k:v for k,v in r['physicalComposite'].items() if k!='targetFillAccounting'}},ensure_ascii=False),flush=True)
    valid=[r for r in rows if r['valid']];cf=sum(r['control']['fills'] for r in valid);tf=sum(r['treatment']['fills'] for r in valid)
    agg={'markets':len(rows),'validMarkets':len(valid),'allCorrectnessPass':len(valid)==len(rows),'confirmedOverflowMarkets':sum(r['physicalComposite']['confirmedOverflowExercised'] for r in valid),'totalCompositeRepairQty':sum(r['physicalComposite']['treatmentMatchedRepairQty'] for r in valid),'totalCompositeOverflowQty':sum(r['physicalComposite']['treatmentOverflowQty'] for r in valid),'aggregateControlFills':cf,'aggregateTreatmentFills':tf,'aggregateFillRetention':tf/cf if cf else None,'sumDeltaFavored':sum(r['deltaTreatmentMinusControl']['fixedFavoredPayoff'] for r in valid),'sumDeltaOpposite':sum(r['deltaTreatmentMinusControl']['oppositePayoff'] for r in valid),'sumDeltaFloor':sum(r['deltaTreatmentMinusControl']['terminalFloor'] for r in valid),'sumDeltaBest':sum(r['deltaTreatmentMinusControl']['terminalBest'] for r in valid),'sumDeltaWinnerPnlPostHoc':sum(r['deltaTreatmentMinusControl']['winnerPnlPostHoc'] for r in valid),'sumDeltaFills':sum(r['deltaTreatmentMinusControl']['fills'] for r in valid),'sumDeltaAlternations':sum(r['deltaTreatmentMinusControl']['alternations'] for r in valid),'favoredImprovedMarkets':sum(r['deltaTreatmentMinusControl']['fixedFavoredPayoff']>EPS for r in valid),'favoredHarmedMarkets':sum(r['deltaTreatmentMinusControl']['fixedFavoredPayoff']<-EPS for r in valid),'floorImprovedMarkets':sum(r['deltaTreatmentMinusControl']['terminalFloor']>EPS for r in valid),'floorHarmedMarkets':sum(r['deltaTreatmentMinusControl']['terminalFloor']<-EPS for r in valid)}
    functionally_exercised=agg['allCorrectnessPass'] and agg['confirmedOverflowMarkets']>=1
    activity_ok=agg['sumDeltaFills']>=0 and agg['sumDeltaAlternations']>=0
    out={'version':'V3B_DECONTAMINATED_ATOMIC_COMPOSITE_SERVICE_SMOKE3','date':'2026-09-07','researchOnly':True,'winnerRuntimeInputUsed':False,'preregisteredFrom':str(a.prereg),'rows':rows,'aggregate':agg,'decision':'FUNCTIONAL_AND_ACTIVITY_PASS_INSPECT_ECONOMICS' if functionally_exercised and activity_ok else 'DO_NOT_SCALE' if functionally_exercised else 'NOT_EXERCISED_OR_CORRECTNESS_FAIL','boundary':['same V3B managed Repair t/side/role/price/slot semantics','qty-only one-shot intervention','partial fill before FIFO boundary is Repair-only','only exact-ledger confirmed overflow is new exposure','overflow authorization capped at one frozen venue-min tranche','no cancel handoff/no extra slot','no old monetary credit/Floor/scope/recoverability admission','winner posthoc only','realistic HFT/no dream fill','NEW24-B untouched','no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg,'decision':out['decision']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
