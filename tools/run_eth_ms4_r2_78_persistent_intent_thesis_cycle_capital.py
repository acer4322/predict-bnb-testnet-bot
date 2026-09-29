from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_75_passive_cycle_capital_rearm as r275
r264=r275.r264; v2=r275.v2; EPS=1e-9

class PersistentIntentThesisCycleCapitalSim(r275.PassiveCycleCapitalRearmSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        self.intentThesisSide=None;self.intentThesisBornAt=None;self.intentThesisSourceKey=None
        self.r278Events=[];self.r278Stats={}
        super().__init__(tape,fanout_limit,max_slots)

    def _inc278(self,k,n=1):self.r278Stats[k]=int(self.r278Stats.get(k,0))+int(n)

    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        before_n=self.n
        ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
        if ok and role=='PROBE_CORE' and self.intentThesisSide is None:
            self.intentThesisSide=str(side);self.intentThesisBornAt=int(t);self.intentThesisSourceKey=f'{side}_{before_n}'
            ev={'t':int(t),'event':'R278_INTENT_THESIS_BIRTH','side':self.intentThesisSide,'sourceKey':self.intentThesisSourceKey,
                'source':'FIRST_SUCCESSFUL_PROBE_CORE_SUBMIT'}
            self.r278Events.append(ev);self.slot_history.append(ev);self._inc278('THESIS_BIRTH')
        return ok

    def _mint_token_if_qualified(self,t,z):
        before=len(self.cycleTokens)
        super()._mint_token_if_qualified(t,z)
        if len(self.cycleTokens)>before:
            tok=self.cycleTokens[-1];tok['sideNeutral']=True;tok['intentThesisAtMint']=self.intentThesisSide
            self.r278Events.append({'t':int(t),'event':'R278_SIDE_NEUTRAL_TOKEN_MINT','tokenId':tok['tokenId'],
                'sourceTrancheSide':z['side'],'intentThesisSide':self.intentThesisSide,'sourcePairEdge':float(z['pairEdge'])})

    def _expire_stale_tokens(self,t):
        # Side-neutral cycle capital survives responsibility scope/generation transitions.
        # It is not action authority by itself and will still be blocked after <=180s.
        return

    def _available_token(self):
        for tok in self.cycleTokens:
            if tok['spent'] or tok['expired'] or tok['reservedBy'] is not None:continue
            return tok
        return None

    def _try_cycle_rearm(self,t,end):
        tok=self._available_token()
        if tok is None:return False
        thesis=self.intentThesisSide
        if thesis not in ('UP','DOWN'):
            self._inc278('BLOCK_NO_INTENT_THESIS');return False
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:
            self._inc278('BLOCK_LATE_180S');return False
        if self.scopeSide is None:
            self._inc278('BLOCK_NO_LIVE_SCOPE');return False
        if str(self.scopeSide)!=str(thesis):
            self._inc278('BLOCK_RESPONSIBILITY_NOT_THESIS_ALIGNED');return False
        if self._has_stale_scope_reservation():
            self._inc278('BLOCK_STALE_SCOPE');return False
        if self._last_new_receipt==int(t):
            self._inc278('BLOCK_ORDINARY_ACTION_OWNS_RECEIPT');return False
        if len(self.slot_key)+len(self.activeKeys)>=self.max_slots:
            self._inc278('BLOCK_SHARED_CAPACITY');return False
        cand=self._candidate_from_levels_v8(thesis,'SATELLITE_EXPAND',False)
        if cand is None:
            self._inc278('BLOCK_NO_THESIS_EXPAND_CANDIDATE');return False
        p,q,proj,split=cand
        risk=max(0.0,float(self._physical_floor())-float(self._candidate_alone_floor(thesis,p,q)))
        if risk<=EPS:
            self._inc278('BLOCK_NOT_RISK_BEARING');return False
        ordinary=float(self._available_expand_risk_credit())
        if ordinary+EPS>=risk:
            self._inc278('BLOCK_ORDINARY_CREDIT_SUFFICIENT');return False
        if float(tok['principal'])+EPS<risk:
            self._inc278('BLOCK_TOKEN_PRINCIPAL_INSUFFICIENT');return False
        before_n=self.n;tok['attempts']+=1
        if not self._submit_role_v8(t,thesis,'SATELLITE_EXPAND',p,q,proj,None):
            self._inc278('SUBMIT_BLOCKED');return False
        key=f'{thesis}_{before_n}'
        self.riskTrancheKeys.add(key)
        self.riskTrancheMeta[key]={'key':key,'generation':int(self.scopeGeneration),'scopeSide':self.scopeSide,
            'submittedAt':int(t),'price':float(p),'qty':float(q),'authorized':float(risk),
            'held':float(risk),'spent':0.0,'released':0.0,'terminal':None,
            'creditAvailableAtSubmit':ordinary,'repairProgressAtSubmit':int(self.scopeRepairProgressClocks),
            'kind':'R278_PERSISTENT_INTENT_THESIS_REARM','cycleTokenId':int(tok['tokenId']),'intentThesisSide':thesis}
        self.riskTrancheAuthorizedRisk+=risk
        tok['reservedBy']=key;self.cycleRearmKeyToken[key]=int(tok['tokenId'])
        self._inc278('REARM_SUBMIT');self.r275['REARM_SUBMIT']+=1
        ev={'t':int(t),'event':'R278_INTENT_THESIS_CYCLE_REARM_SUBMIT','tokenId':tok['tokenId'],'key':key,
            'generation':int(self.scopeGeneration),'thesisSide':thesis,'scopeSide':self.scopeSide,'price':float(p),'qty':float(q),
            'riskAuthorized':risk,'ordinaryCredit':ordinary,'sourcePairEdge':float(tok['pairEdge'])}
        self.r278Events.append(ev);self.r275Events.append(dict(ev));self.slot_history.append(ev);self._audit_r247();return True

    def process(self,t):
        split0=len(self.splitEvents)
        super().process(t)
        for ev in self.splitEvents[split0:]:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key'));inc=float(ev.get('fillInc') or 0.0)
            if key in self.cycleRearmKeyToken and inc>EPS and self.riskTrancheMeta.get(key,{}).get('kind')=='R278_PERSISTENT_INTENT_THESIS_REARM':
                self._inc278('REARM_FILL')
                self.r278Events.append({'t':int(t),'event':'R278_INTENT_THESIS_CYCLE_REARM_FILL','key':key,
                    'side':ev.get('side'),'fillQty':inc,'price':float(ev.get('price') or 0.0),'generation':ev.get('generationAtSubmit')})

    def run_r278(self,winner):
        r=super().run_r275(winner)
        correct=bool(r.get('r275CorrectnessPass'))
        r.update({'r278Version':'MS4_R2_78_PERSISTENT_INTENT_THESIS_CYCLE_CAPITAL_V1','r278Stats':dict(self.r278Stats),
                  'r278Events':self.r278Events[:5000],'r278IntentThesisSide':self.intentThesisSide,
                  'r278IntentThesisBornAt':self.intentThesisBornAt,'r278IntentThesisSourceKey':self.intentThesisSourceKey,
                  'r278RearmSubmits':int(self.r278Stats.get('REARM_SUBMIT',0)),'r278RearmFills':int(self.r278Stats.get('REARM_FILL',0)),
                  'r278CorrectnessPass':correct})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r278_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];cmp=[]
        for m in mids:
            w=co[m]['winner'];tape=tmp/f'{m}.json.xz'
            b=r264.ExecutionRepresentedPreRepairReexpandSim(tape,1,4)
            try:br=b.run_r264(w)
            finally:b.close()
            s=PersistentIntentThesisCycleCapitalSim(tape,1,4)
            try:r=s.run_r278(w)
            finally:s.close()
            rows += [{'marketId':m,'cell':'R264_CONTROL','winnerPostHocOnly':w,**br},
                     {'marketId':m,'cell':'R278_PERSISTENT_INTENT_THESIS_CYCLE_CAPITAL','winnerPostHocOnly':w,**r}]
            d={'marketId':m,'winnerPostHocOnly':w,'intentThesis':r.get('r278IntentThesisSide'),'thesisWinnerAligned':r.get('r278IntentThesisSide')==w,
               'tokenMinted':int(r.get('r275TokenMinted') or 0),'tokenSpent':int(r.get('r275TokenSpent') or 0),
               'rearmSubmits':int(r.get('r278RearmSubmits') or 0),'rearmFills':int(r.get('r278RearmFills') or 0),
               'pnlDelta':float(r['pnlDiagnosticOnly'])-float(br['pnlDiagnosticOnly']),'floorDelta':float(r['floor'])-float(br['floor']),
               'bestDelta':float(r['best'])-float(br['best']),'gapDelta':(float(r['best'])-float(r['floor']))-(float(br['best'])-float(br['floor'])),
               'fillDelta':int(r['fillEvents'])-int(br['fillEvents']),'candidatePnl':float(r['pnlDiagnosticOnly']),
               'candidateBest':float(r['best']),'candidateFloor':float(r['floor']),'bestGt2':float(r['best'])>2.0,'floorGtMinus1':float(r['floor'])>-1.0,
               'correct':bool(r.get('r278CorrectnessPass')),'repairQuotaExcessMax':float(r.get('repairQuotaExcessMax',0.0)),
               'unauthorizedOverflowQty':float(r.get('unauthorizedOverflowQty',0.0))}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_78_PERSISTENT_INTENT_THESIS_CYCLE_CAPITAL_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),'thesisBornAll':all(x['intentThesis'] in ('UP','DOWN') for x in cmp),
                      'tokenMintExercised':any(x['tokenMinted']>0 for x in cmp),'rearmFillExercised':any(x['rearmFills']>0 for x in cmp),
                      'negativeControl1945898NoToken':all(x['tokenMinted']==0 for x in cmp if x['marketId']==1945898)},
             'boundary':['exact R264 control','first successful PROBE_CORE submit births persistent intent thesis','fill/scope/Repair cannot overwrite thesis','cycle capital side-neutral and may survive responsibility generation transitions','token spends only when current scope is thesis-aligned and ordinary monetary credit is insufficient','no instantaneous signal hold gate','no winner/Target/future runtime input','max4','<=180s','realistic HFT','consumed causal test only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
