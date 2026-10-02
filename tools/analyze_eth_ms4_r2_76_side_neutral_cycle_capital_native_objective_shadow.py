from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_75_passive_cycle_capital_rearm as r275
r264=r275.r264; v2=r275.v2; EPS=1e-9

class SideNeutralCycleCapitalShadow(r275.PassiveCycleCapitalRearmSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r276=Counter();self.r276Rows=[];self.r276Seen=set()

    # Behavior inert: R2.75 token tracking remains, but token can never submit.
    def _try_cycle_rearm(self,t,end):
        return False

    # Keep a positive-cycle token while its source generation remains current; do not
    # interpret the source risk side as a new profit-direction authority.
    def _expire_stale_tokens(self,t):
        for tok in self.cycleTokens:
            if tok['spent'] or tok['expired']:continue
            if self.scopeSide is not None and int(self.scopeGeneration)==int(tok['generation']):continue
            if tok['reservedBy'] is not None:continue
            tok['expired']=True;tok['expiredAt']=int(t);self.r276['TOKEN_EXPIRE_GENERATION_LEFT']+=1

    def _shadow_native_request(self,t,qv,end):
        toks=[x for x in self.cycleTokens if not x['spent'] and not x['expired'] and x['reservedBy'] is None and int(x['generation'])==int(self.scopeGeneration)]
        if not toks or self.scopeSide is None:return
        tok=toks[0];mark=(int(tok['tokenId']),int(t))
        if mark in self.r276Seen:return
        self.r276Seen.add(mark)
        state=self._state();signal=self._direction(qv);scope=str(self.scopeSide);repair='DOWN' if scope=='UP' else 'UP'
        core_live=self._core_for_side(repair) is not None
        stale=self._has_stale_scope_reservation();capacity=(len(self.slot_key)+len(self.activeKeys)<self.max_slots)
        late=(int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS)
        cand=self._candidate_from_levels_v8(scope,'SATELLITE_EXPAND',False) if (state=='SCOPED' and core_live and not stale and capacity and not late) else None
        risk=None
        if cand is not None:
            p,q,proj,split=cand;risk=max(0.0,float(self._physical_floor())-float(self._candidate_alone_floor(scope,p,q)))
        credit=float(self._available_expand_risk_credit())
        direction_aligned=(signal==scope)
        would_native_expand=bool(state=='SCOPED' and core_live and direction_aligned and cand is not None and risk is not None and risk>EPS)
        blocked_only_credit=bool(would_native_expand and credit+EPS<risk and capacity and not stale and not late)
        row={'t':int(t),'tokenId':int(tok['tokenId']),'generation':int(self.scopeGeneration),'tokenSourceSide':tok.get('side'),
             'scopeSide':scope,'signalSide':signal,'state':state,'coreLive':bool(core_live),'capacityFree':bool(capacity),'stale':bool(stale),'late':bool(late),
             'candidate':({'price':float(cand[0]),'qty':float(cand[1]),'riskCost':float(risk)} if cand is not None else None),
             'ordinaryCredit':credit,'directionAligned':bool(direction_aligned),'wouldNativeExpand':bool(would_native_expand),
             'blockedOnlyByCredit':bool(blocked_only_credit),'sourcePairEdge':float(tok.get('pairEdge') or 0.0)}
        self.r276Rows.append(row)
        if would_native_expand:self.r276['WOULD_NATIVE_EXPAND']+=1
        if blocked_only_credit:self.r276['BLOCKED_ONLY_CREDIT']+=1
        if direction_aligned:self.r276['SIGNAL_SCOPE_ALIGNED']+=1

    def _open_one_option(self,t,qv,end):
        # Exact R264 behavior. We deliberately bypass R275 behavior and only retain its
        # cycle-token telemetry generated in process().
        r264.ExecutionRepresentedPreRepairReexpandSim._open_one_option(self,t,qv,end)
        self._shadow_native_request(t,qv,end)

    def run_shadow(self,winner):
        r=super().run_r275(winner)
        r.update({'r276Version':'MS4_R2_76_SIDE_NEUTRAL_CYCLE_CAPITAL_NATIVE_OBJECTIVE_SHADOW_V1',
                  'r276Stats':dict(self.r276),'r276Rows':self.r276Rows[:3000]})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r276_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for m in mids:
            s=SideNeutralCycleCapitalShadow(tmp/f'{m}.json.xz',1,4)
            try:r=s.run_shadow(co[m]['winner'])
            finally:s.close()
            rr=r.get('r276Rows') or [];first=next((x for x in rr if x.get('blockedOnlyByCredit')),None)
            out={'marketId':m,'winnerPostHocOnly':co[m]['winner'],'pnl':float(r['pnlDiagnosticOnly']),'best':float(r['best']),'floor':float(r['floor']),
                 'tokenMinted':int(r.get('r275TokenMinted') or 0),'checks':len(rr),'wouldNativeExpand':int((r.get('r276Stats') or {}).get('WOULD_NATIVE_EXPAND',0)),
                 'blockedOnlyCredit':int((r.get('r276Stats') or {}).get('BLOCKED_ONLY_CREDIT',0)),'signalScopeAligned':int((r.get('r276Stats') or {}).get('SIGNAL_SCOPE_ALIGNED',0)),
                 'firstBlockedOnlyCredit':first,'candidateCorrect':bool(r.get('r275CorrectnessPass'))}
            rows.append({'summary':out,'full':r});print(json.dumps(out,ensure_ascii=False),flush=True)
        agg={'markets':len(rows),'tokens':sum(x['summary']['tokenMinted'] for x in rows),'marketsWithBlockedNativeExpand':sum(x['summary']['blockedOnlyCredit']>0 for x in rows),
             'blockedNativeExpandStates':sum(x['summary']['blockedOnlyCredit'] for x in rows),'correct':all(x['summary']['candidateCorrect'] for x in rows)}
        payload={'version':'MS4_R2_76_SIDE_NEUTRAL_CYCLE_CAPITAL_NATIVE_OBJECTIVE_SHADOW_V1','researchOnly':True,'behaviorChange':False,'aggregate':agg,'rows':rows,
                 'boundary':['exact R264 behavior','R275 cycle token accounting only','R275 token behavior disabled','token side is not used as direction authority','shadow asks whether frozen OUR objective would request same-scope Expand but is blocked only by ordinary monetary credit','winner post-hoc scoring only','no future/Target runtime input','consumed HFT']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(payload,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
