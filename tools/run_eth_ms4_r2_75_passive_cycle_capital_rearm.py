from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter,defaultdict,deque
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
r263=r264.r263; r257=r264.r257; v2=r264.v2; EPS=1e-9

class PassiveCycleCapitalRearmSim(r264.ExecutionRepresentedPreRepairReexpandSim):
    """R2.75: successful passive risk->Repair cycles rearm bounded risk capacity.

    The pool starts empty. A token is earned only by full FIFO repayment of one
    confirmed explicit risk tranche, with passive-only Repair and realized pair
    edge >= 0. Frozen R2.64 continuation runs first. A token is used only when
    ordinary monetary continuation credit cannot fund the next venue-min Expand.
    """
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r275=Counter(); self.r275Events=[]
        self.cycleTranches=[]; self.cycleByGeneration=defaultdict(deque)
        self.cycleTokens=[]; self.nextCycleTrancheId=1; self.nextCycleTokenId=1
        self.cycleRearmKeyToken={}
        self.cycleTokenDoubleSpend=0; self.cycleTokenOverAuthorizationMax=0.0

    def _new_cycle_tranche(self,t,ev):
        key=str(ev.get('key')); meta=self.riskTrancheMeta.get(key)
        if meta is None:return
        inc=float(ev.get('fillInc') or 0.0)
        if inc<=EPS:return
        gen=int(meta.get('generation') or ev.get('generationAtSubmit') or -1)
        price=float(ev.get('price') or meta.get('price') or 0.0)
        z={'trancheId':self.nextCycleTrancheId,'key':key,'generation':gen,
           'side':str(ev.get('side') or meta.get('scopeSide')),'createdAt':int(t),
           'entryPrice':price,'qty':inc,'riskPrincipal':inc*price,'remaining':inc,
           'paidQty':0.0,'passivePaidQty':0.0,'activePaidQty':0.0,'pairEdge':0.0,
           'payments':[],'completedAt':None,'tokenMinted':False}
        self.nextCycleTrancheId+=1;self.cycleTranches.append(z);self.cycleByGeneration[gen].append(z)
        self.r275['RISK_FILL_TRANCHE']+=1
        self.r275Events.append({'t':int(t),'event':'R275_CYCLE_TRANCHE_BORN',**{k:z[k] for k in ['trancheId','key','generation','side','entryPrice','qty','riskPrincipal']}})

    def _mint_token_if_qualified(self,t,z):
        if z['tokenMinted'] or z['remaining']>EPS:return
        z['completedAt']=int(t);self.r275['CYCLE_COMPLETED']+=1
        passive_only=(z['passivePaidQty']>=z['qty']-EPS and z['activePaidQty']<=EPS)
        nonnegative=(z['pairEdge']>=-EPS)
        if not passive_only:
            self.r275['CYCLE_COMPLETE_ACTIVE_OR_MIXED_NO_TOKEN']+=1;return
        if not nonnegative:
            self.r275['CYCLE_COMPLETE_NEGATIVE_EDGE_NO_TOKEN']+=1;return
        tok={'tokenId':self.nextCycleTokenId,'sourceTrancheId':z['trancheId'],'generation':z['generation'],
             'side':z['side'],'mintedAt':int(t),'principal':float(z['riskPrincipal']),
             'pairEdge':float(z['pairEdge']),'reservedBy':None,'spent':False,'spentAt':None,
             'expired':False,'expiredAt':None,'attempts':0}
        self.nextCycleTokenId+=1;self.cycleTokens.append(tok);z['tokenMinted']=True
        self.r275['TOKEN_MINT']+=1
        self.r275Events.append({'t':int(t),'event':'R275_PASSIVE_NONNEG_CYCLE_TOKEN_MINT',**tok})

    def _apply_cycle_payments(self,t,new_split,new_r257):
        repair_lookup={(int(x.get('t') or t),str(x.get('key'))):x for x in new_split
                       if x.get('event')=='ROLE_FILL_SPLIT' and float(x.get('repairAllocated') or 0.0)>EPS}
        for e in new_r257:
            if e.get('event')!='R257_RISK_REPAIR_OBLIGATION_PAYMENT':continue
            q=float(e.get('paid') or 0.0)
            if q<=EPS:continue
            gen=int(e.get('generation') or -1); active=bool(e.get('active'))
            lk=repair_lookup.get((int(e.get('t') or t),str(e.get('key'))),{})
            price=float(lk.get('price') or 0.0)
            dq=self.cycleByGeneration[gen]
            while q>EPS and dq:
                z=dq[0]
                if z['remaining']<=EPS:
                    dq.popleft();continue
                take=min(q,z['remaining']);z['remaining']-=take;z['paidQty']+=take
                if active:z['activePaidQty']+=take
                else:z['passivePaidQty']+=take
                edge=take*(1.0-z['entryPrice']-price);z['pairEdge']+=edge
                z['payments'].append({'t':int(t),'key':str(e.get('key')),'price':price,'qty':take,'active':active,'edge':edge})
                self.r275['PAYMENT_ALLOCATION']+=1;q-=take
                if z['remaining']<=EPS:
                    z['remaining']=0.0;dq.popleft();self._mint_token_if_qualified(t,z)
            if q>EPS:self.r275['ORPHAN_PAYMENT_MILLI']+=int(round(q*1000))

    def _expire_stale_tokens(self,t):
        for tok in self.cycleTokens:
            if tok['spent'] or tok['expired']:continue
            valid=(self.scopeSide is not None and int(self.scopeGeneration)==int(tok['generation']) and str(self.scopeSide)==str(tok['side']))
            if valid:continue
            # If already tied to a physical order, let native stale-order handling finish it;
            # it is never available to another order meanwhile.
            if tok['reservedBy'] is not None:
                self.r275['TOKEN_STALE_WHILE_RESERVED']+=1;continue
            tok['expired']=True;tok['expiredAt']=int(t);self.r275['TOKEN_EXPIRE_SCOPE_LEFT']+=1
            self.r275Events.append({'t':int(t),'event':'R275_TOKEN_EXPIRE_SCOPE_LEFT','tokenId':tok['tokenId']})

    def _available_token(self):
        if self.scopeSide is None:return None
        for tok in self.cycleTokens:
            if tok['spent'] or tok['expired'] or tok['reservedBy'] is not None:continue
            if int(tok['generation'])!=int(self.scopeGeneration) or str(tok['side'])!=str(self.scopeSide):continue
            return tok
        return None

    def _try_cycle_rearm(self,t,end):
        tok=self._available_token()
        if tok is None:return False
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:
            self.r275['BLOCK_LATE_180S']+=1;return False
        if self._has_stale_scope_reservation():
            self.r275['BLOCK_STALE_SCOPE']+=1;return False
        # Frozen R2.64/native action always owns the receipt first.
        if self._last_new_receipt==int(t):
            self.r275['BLOCK_ORDINARY_ACTION_OWNS_RECEIPT']+=1;return False
        if len(self.slot_key)+len(self.activeKeys)>=self.max_slots:
            self.r275['BLOCK_SHARED_CAPACITY']+=1;return False
        side=str(self.scopeSide)
        cand=self._candidate_from_levels_v8(side,'SATELLITE_EXPAND',False)
        if cand is None:
            self.r275['BLOCK_NO_EXPAND_CANDIDATE']+=1;return False
        p,q,proj,split=cand
        risk=max(0.0,float(self._physical_floor())-float(self._candidate_alone_floor(side,p,q)))
        if risk<=EPS:
            self.r275['BLOCK_NOT_RISK_BEARING']+=1;return False
        ordinary=float(self._available_expand_risk_credit())
        if ordinary+EPS>=risk:
            self.r275['BLOCK_ORDINARY_CREDIT_SUFFICIENT']+=1;return False
        if float(tok['principal'])+EPS<risk:
            self.r275['BLOCK_TOKEN_PRINCIPAL_INSUFFICIENT']+=1;return False
        before_n=self.n
        tok['attempts']+=1
        if not self._submit_role_v8(t,side,'SATELLITE_EXPAND',p,q,proj,None):
            self.r275['SUBMIT_BLOCKED']+=1;return False
        key=f'{side}_{before_n}'
        # Register in the existing explicit-risk accounting so confirmed fill creates
        # real risk debt and R257 service obligation; no hidden/free credit path.
        self.riskTrancheKeys.add(key)
        self.riskTrancheMeta[key]={'key':key,'generation':int(self.scopeGeneration),'scopeSide':self.scopeSide,
            'submittedAt':int(t),'price':float(p),'qty':float(q),'authorized':float(risk),
            'held':float(risk),'spent':0.0,'released':0.0,'terminal':None,
            'creditAvailableAtSubmit':ordinary,'repairProgressAtSubmit':int(self.scopeRepairProgressClocks),
            'kind':'R275_PASSIVE_CYCLE_CAPITAL_REARM','cycleTokenId':int(tok['tokenId'])}
        self.riskTrancheAuthorizedRisk+=risk
        tok['reservedBy']=key;self.cycleRearmKeyToken[key]=int(tok['tokenId'])
        self.r275['REARM_SUBMIT']+=1
        ev={'t':int(t),'event':'R275_CYCLE_CAPITAL_REARM_SUBMIT','tokenId':tok['tokenId'],'key':key,
            'generation':int(self.scopeGeneration),'side':side,'price':float(p),'qty':float(q),'riskAuthorized':risk,
            'ordinaryCredit':ordinary,'tokenPrincipal':float(tok['principal']),'sourcePairEdge':float(tok['pairEdge'])}
        self.r275Events.append(ev);self.slot_history.append(ev);self._audit_r247();return True

    def _open_one_option(self,t,qv,end):
        super()._open_one_option(t,qv,end)
        self._try_cycle_rearm(t,end)

    def process(self,t):
        split_start=len(self.splitEvents); pay_start=len(self.r257Events)
        super().process(t)
        new_split=self.splitEvents[split_start:];new_r257=self.r257Events[pay_start:]
        # First create tranches for any new explicit-risk fills, then allocate Repair
        # payments FIFO. This preserves same-clock physical ordering after native process.
        for ev in new_split:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key'));inc=float(ev.get('fillInc') or 0.0)
            if key in self.riskTrancheMeta and inc>EPS:self._new_cycle_tranche(t,ev)
            if key in self.cycleRearmKeyToken and inc>EPS:
                tid=int(self.cycleRearmKeyToken[key]);tok=next((x for x in self.cycleTokens if int(x['tokenId'])==tid),None)
                if tok is not None and not tok['spent']:
                    if tok['reservedBy']!=key:self.cycleTokenDoubleSpend+=1
                    tok['spent']=True;tok['spentAt']=int(t);tok['reservedBy']=key
                    self.r275['TOKEN_SPENT_ON_CONFIRMED_FILL']+=1;self.r275['REARM_FILL']+=1
                    self.r275Events.append({'t':int(t),'event':'R275_TOKEN_SPENT_CONFIRMED_REARM_FILL','tokenId':tid,'key':key,'fillQty':inc})
                # R257 setdefault reuses a previously closed generation obligation.
                # Reopen only if this new confirmed risk leaves debt outstanding.
                gen=int(ev.get('generationAtSubmit') or self.scopeGeneration);ob=self.riskRepairObligations.get(gen)
                if ob is not None and float(ob.get('outstanding') or 0.0)>EPS and ob.get('closedAt') is not None:
                    ob['closedAt']=None;ob['closeReason']=None;self.r275['OBLIGATION_REOPEN']+=1
                    self.r275Events.append({'t':int(t),'event':'R275_REARM_RISK_OBLIGATION_REOPEN','generation':gen,'outstanding':float(ob['outstanding'])})
        self._apply_cycle_payments(t,new_split,new_r257)
        # Release token reservation only for zero-fill terminal; a confirmed fill spends it.
        for key,tid in list(self.cycleRearmKeyToken.items()):
            tok=next((x for x in self.cycleTokens if int(x['tokenId'])==int(tid)),None)
            if tok is None or tok['spent']:continue
            meta=self.riskTrancheMeta.get(key,{})
            st=meta.get('terminal')
            if st is not None and tok['reservedBy']==key:
                tok['reservedBy']=None;self.r275['TOKEN_RELEASE_ZERO_FILL']+=1
                self.r275Events.append({'t':int(t),'event':'R275_TOKEN_RELEASE_ZERO_FILL','tokenId':tid,'key':key,'status':st})
        self._expire_stale_tokens(t)
        # Token principal is a bound on the explicit risk authority it sponsors.
        for key,tid in self.cycleRearmKeyToken.items():
            meta=self.riskTrancheMeta.get(key);tok=next((x for x in self.cycleTokens if int(x['tokenId'])==int(tid)),None)
            if meta is not None and tok is not None:
                self.cycleTokenOverAuthorizationMax=max(self.cycleTokenOverAuthorizationMax,max(0.0,float(meta.get('authorized') or 0.0)-float(tok['principal'])))
        self._audit_r247()

    def run_r275(self,winner):
        r=super().run_r264(winner)
        minted=sum(1 for x in self.cycleTokens)
        spent=sum(1 for x in self.cycleTokens if x['spent'])
        available=sum(1 for x in self.cycleTokens if not x['spent'] and not x['expired'] and x['reservedBy'] is None)
        reserved=sum(1 for x in self.cycleTokens if not x['spent'] and not x['expired'] and x['reservedBy'] is not None)
        expired=sum(1 for x in self.cycleTokens if x['expired'])
        token_cons=(minted==spent+available+reserved+expired)
        correct=(bool(r.get('r264CorrectnessPass')) and self.cycleTokenDoubleSpend==0 and self.cycleTokenOverAuthorizationMax<=EPS and token_cons and
                 float(r.get('unauthorizedOverflowQty',0.0))<=EPS and float(r.get('repairQuotaExcessMax',0.0))<=EPS)
        r.update({'r275Version':'MS4_R2_75_PASSIVE_CYCLE_CAPITAL_REARM_V1','r275Stats':dict(self.r275),
                  'r275Events':self.r275Events[:5000],'r275CycleTranches':self.cycleTranches[:500],
                  'r275CycleTokens':self.cycleTokens[:500],'r275TokenMinted':minted,'r275TokenSpent':spent,
                  'r275TokenAvailable':available,'r275TokenReserved':reserved,'r275TokenExpired':expired,
                  'r275TokenConservationPass':bool(token_cons),'r275TokenDoubleSpend':int(self.cycleTokenDoubleSpend),
                  'r275TokenOverAuthorizationMax':float(self.cycleTokenOverAuthorizationMax),
                  'r275RearmSubmits':int(self.r275.get('REARM_SUBMIT',0)),'r275RearmFills':int(self.r275.get('REARM_FILL',0)),
                  'r275CorrectnessPass':bool(correct)})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r275_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:(tmp/f'{mid}.json.xz').write_bytes(z.read(f'tapes/{mid}.json.xz'))
        rows=[];cmp=[]
        for mid in mids:
            w=co[mid]['winner'];tape=tmp/f'{mid}.json.xz'
            bsim=r264.ExecutionRepresentedPreRepairReexpandSim(tape,1,4)
            try:b=bsim.run_r264(w)
            finally:bsim.close()
            sim=PassiveCycleCapitalRearmSim(tape,1,4)
            try:c=sim.run_r275(w)
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'R264_CONTROL','winnerPostHocOnly':w,**b},
                     {'marketId':mid,'cell':'R275_PASSIVE_CYCLE_CAPITAL_REARM','winnerPostHocOnly':w,**c}]
            d={'marketId':mid,'tokenMinted':c['r275TokenMinted'],'tokenSpent':c['r275TokenSpent'],
               'rearmSubmits':c['r275RearmSubmits'],'rearmFills':c['r275RearmFills'],
               'pnlDelta':float(c['pnlDiagnosticOnly'])-float(b['pnlDiagnosticOnly']),
               'floorDelta':float(c['floor'])-float(b['floor']),'bestDelta':float(c['best'])-float(b['best']),
               'gapDelta':(float(c['best'])-float(c['floor']))-(float(b['best'])-float(b['floor'])),
               'fillDelta':int(c['fillEvents'])-int(b['fillEvents']),'submitDelta':int(c['submits'])-int(b['submits']),
               'candidatePnl':float(c['pnlDiagnosticOnly']),'candidateFloor':float(c['floor']),'candidateBest':float(c['best']),
               'bestGt2':float(c['best'])>2.0,'floorGtMinus1':float(c['floor'])>-1.0,
               'correct':bool(c['r275CorrectnessPass']),'repairQuotaExcessMax':float(c.get('repairQuotaExcessMax',0.0)),
               'unauthorizedOverflowQty':float(c.get('unauthorizedOverflowQty',0.0))}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_75_PASSIVE_CYCLE_CAPITAL_REARM_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),'tokenMintExercised':any(x['tokenMinted']>0 for x in cmp),
                      'rearmSubmitExercised':any(x['rearmSubmits']>0 for x in cmp),'rearmFillExercised':any(x['rearmFills']>0 for x in cmp),
                      'negativeControl1945898NoToken':all(x['tokenMinted']==0 for x in cmp if x['marketId']==1945898)},
             'boundary':['exact R264 control','cycle pool starts at zero','only fully repaid passive-only nonnegative actual cycles mint token','ordinary continuation owns action first','token only used when ordinary monetary credit cannot fund candidate','same side/same generation only','one token one risk order','zero-fill releases token','confirmed rearm fill creates/reopens explicit R257 Repair obligation','no pair/Floor/direction gate on rearm entry','max4','<=180s unchanged','no Target/winner/future runtime input','realistic HFT','no dream fill','no 8781','consumed causal evidence only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
