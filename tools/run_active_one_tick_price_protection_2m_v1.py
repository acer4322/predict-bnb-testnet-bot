"""Preregistered 2-market test of one-tick Active price protection under frozen immediate priority-loss handoff."""
from __future__ import annotations
import argparse,json,math,tempfile,zipfile,sys,importlib.util
from pathlib import Path
ROOT=Path.cwd() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_sustained_priority_loss_feedback_horizon_replication4_v1.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('staged_fb_price_protect',_STAGED);fb=importlib.util.module_from_spec(sp);sp.loader.exec_module(fb)
else:
    import tools.run_sustained_priority_loss_feedback_horizon_replication4_v1 as fb
rr=fb.rr;v1=fb.v1;EPS=1e-9;TICK=0.01;MIDS=(1827839,1829561);CELLS=('A_CURRENT_ASK_ACTIVE','B_ONE_TICK_PROTECTED_ACTIVE')

class ProtectedActiveSim(fb.FeedbackHandoffSim):
    def __init__(self,tape,trace_path):
        super().__init__(tape,'B',trace_path)
        self.price_protection_ticks=1
    def _submit_active_relay(self,t,qv):
        pnd=self.pending_relay;side=pnd['side'];role=pnd['role'];qty=float(pnd['remainingQty'])
        ask=float(qv[side]['ask']);bid=float(qv[side]['bid']);limit=round(min(0.99,ask+TICK),10)
        if limit<=ask+EPS:
            self.relay_block_reason='ONE_TICK_PROTECTION_NOT_AVAILABLE';return False
        if len(self.slot_key)>=self.max_slots:
            self.relay_block_reason='NO_FREE_SLOT';return False
        free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
        if free is None:
            self.relay_block_reason='NO_FREE_SLOT';return False
        n=self.n;self.n+=1;ex=rr.isp.base.v2.base.ex;native_side,native_price=ex.native_order(side,limit)
        try:
            if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,qty,ex.hbt.GTC,ex.LIMIT,False))
            else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,qty,ex.hbt.GTC,ex.LIMIT,False))
        except Exception as exc:
            self.relay_block_reason='ACTIVE_SUBMIT_EXCEPTION:'+str(exc);return False
        key=f'{side}_{n}'
        self.orders[key]={'n':n,'side':side,'price':limit,'qty':qty,'cum':0.0,'placed':int(t),'status':'NEW','cancelRequested':False}
        self.placeHist.append((int(t),side,qty,limit));self.submits+=1;self.slot_key[int(free)]=key;self.key_role[key]=role;self.role_submits[role]+=1
        credit,spend=self._pair_edge(side,limit,qty);self.marginal_pair_credit[role]+=credit;self.marginal_pair_risk_spend[role]+=spend
        self.detail_outcomes[key]={'decisionId':self.detail_index,'key':key,'role':role,'side':side,'route':'ACTIVE_RELAY_PROTECTED_1T',
            'submittedAt':int(t),'submittedPrice':limit,'submittedQty':qty,'fills':[],'terminal':None}
        self.relay_key=key;self.relay_used=True;self.active_submitted_at=int(t)
        self.relay_meta={'route':'ACTIVE','sourceKey':pnd['sourceKey'],'submittedAt':int(t),'side':side,'role':role,'price':limit,'qty':qty,
            'bidAtRelay':bid,'askAtRelay':ask,'decisionAsk':ask,'limitPrice':limit,'priceProtectionTicks':1,'submitRc':rc,**self._pair_diag(side,limit)}
        self.slot_history.append({'t':int(t),'event':'SAME_INTENT_ACTIVE_RELAY_SUBMIT','sourceKey':pnd['sourceKey'],'key':key,'slotId':int(free),
            'side':side,'role':role,'price':limit,'decisionAsk':ask,'priceProtectionTicks':1,'qty':qty,'submitRc':rc})
        return True

def eq(a,b):
    if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(eq(a[k],b[k]) for k in a)
    if isinstance(a,list) and isinstance(b,list):return len(a)==len(b) and all(eq(x,y) for x,y in zip(a,b))
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-9)
    return a==b

def source_sig(r):
    pc=r.get('probeCandidate') or {};po=r.get('probeOutcome') or {}
    return {'used':bool(r.get('probeUsed')),'side':pc.get('side'),'role':pc.get('role'),'price':pc.get('probePrice'),'qty':pc.get('probeQty'),'pairSum':pc.get('probePairSum'),'key':r.get('probeKey'),
        'terminal':(po.get('terminal') or {}).get('status'),'terminalAt':(po.get('terminal') or {}).get('t'),'fillQty':sum(float(x.get('confirmedQty') or 0) for x in po.get('fills',[]))}
def relay_summary(r):
    rm=r.get('relayMeta') or {};ro=r.get('relayOutcome') or {};fills=ro.get('fills') or []
    return {'route':rm.get('route'),'submittedAt':rm.get('submittedAt'),'decisionAsk':rm.get('decisionAsk',rm.get('askAtRelay')),'limitPrice':rm.get('limitPrice',rm.get('price')),
        'priceProtectionTicks':rm.get('priceProtectionTicks',0),'pairSum':rm.get('pairSum'),'terminal':(ro.get('terminal') or {}).get('status'),
        'fillQty':sum(float(x.get('confirmedQty') or 0) for x in fills),'repairQty':sum(float(x.get('matchedRepairQty') or 0) for x in fills),'overflowQty':sum(float(x.get('overflowQty') or 0) for x in fills),
        'actualExecPrices':[x.get('executionPriceFromInheritedSubstrate') for x in fills],'fifoCredit':sum(float(x.get('realizedFifoPairCredit') or 0) for x in fills)}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--bundle',required=True);ap.add_argument('--reference',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    op=Path(a.output).resolve()
    if op.exists():ap.error('Do not overwrite')
    ref=json.loads(Path(a.reference).read_text(encoding='utf-8'));refs={int(r['marketId']):r for r in ref['rows']}
    if tuple(sorted(refs))!=tuple(sorted(MIDS)):raise RuntimeError('reference mids')
    if v1.sha256(a.bundle)!=ref['bundleSha256']:raise RuntimeError('bundle mismatch')
    fields=('submits','fillEvents','filledQty','upQty','downQty','buyNotional','floor','best','fillSideAlternations','twoSidedMaterialized','roleSubmits','roleFills','roleFillQty','reanchors','economicRepairQty','economicOverflowQty')
    trace_dir=op.parent/(op.stem+'_traces');trace_dir.mkdir(parents=True,exist_ok=False);rows=[];checks=[]
    with tempfile.TemporaryDirectory(prefix='active_1t_2m_') as folder:
        root=Path(folder)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in MIDS:z.extract(f'tapes/{mid}.json.xz',root)
        for mid in MIDS:
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper();rf=refs[mid];by={}
            for cell,protected in [(CELLS[0],False),(CELLS[1],True)]:
                tp=trace_dir/f'{mid}_{cell}.jsonl';sim=ProtectedActiveSim(tape,tp) if protected else fb.FeedbackHandoffSim(tape,'B',tp)
                try:r=sim.run_feedback()
                finally:sim.close()
                r['pnlDiagnosticOnly']=r['upQty' if winner=='UP' else 'downQty']-r['buyNotional'];row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,'decisionTracePath':str(tp),**r};rows.append(row);by[cell]=row;v1.write_json(trace_dir/f'{mid}_{cell}_result.json',row)
                if not protected:
                    parity={f:eq(row[f],rf[f]) for f in fields};row['baselineParity']=parity
                    if not all(parity.values()):raise RuntimeError(f'A parity fail {mid}: {parity}')
                    if not eq(row.get('priorityLossEvent'),rf.get('priorityLossEvent')):raise RuntimeError(f'A trigger parity fail {mid}')
                    if not eq(source_sig(row),source_sig(rf)):raise RuntimeError(f'A source parity fail {mid}')
                    if not eq(relay_summary(row),relay_summary(rf)):raise RuntimeError(f'A relay parity fail {mid}')
                print(json.dumps({'marketId':mid,'cell':cell,'source':source_sig(row),'priorityLoss':row.get('priorityLossEvent'),'relay':relay_summary(row),
                    'fills':row['fillEvents'],'alts':row['fillSideAlternations'],'repairQty':row['economicRepairQty'],'pnl':row['pnlDiagnosticOnly'],'floor':row['floor']},allow_nan=False),flush=True)
            A,B=by[CELLS[0]],by[CELLS[1]]
            if not eq(source_sig(A),source_sig(B)):raise RuntimeError(f'source mismatch {mid}')
            if not eq(A.get('priorityLossEvent'),B.get('priorityLossEvent')):raise RuntimeError(f'trigger mismatch {mid}')
            ar,br=relay_summary(A),relay_summary(B)
            if ar['submittedAt']!=br['submittedAt'] or ar['decisionAsk']!=br['decisionAsk']:raise RuntimeError(f'relay timing/book mismatch {mid}')
            if abs(float(br['limitPrice'])-float(ar['limitPrice'])-TICK)>1e-9:raise RuntimeError(f'not exactly one tick {mid}')
            checks.append({'marketId':mid,'sourceSame':True,'triggerSame':True,'relaySubmittedAtSame':True,'decisionAskSame':True,'baseline':ar,'protected':br,
                'protectedPreservedBaselineFill':not(ar['fillQty']>EPS) or br['fillQty']>EPS,'protectedRecoveredMiss':ar['fillQty']<=EPS and br['fillQty']>EPS})
    v1.write_json(op,{'version':'ACTIVE_ONE_TICK_PRICE_PROTECTION_2M_V1','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':list(MIDS),'rows':rows,'checks':checks,
        'bundleSha256':v1.sha256(a.bundle),'referenceSha256':v1.sha256(a.reference),'tickSize':TICK,'automaticScaleAllowed':False,
        'boundary':['same trigger/source/side/role/qty','B changes only Active limit by +1 side-price tick','GTC LIMIT / 250ms entry+response / risk queue unchanged','actual execution price separately recorded','no sweep/no Target runtime/no winner action/no 8781/no scale']})
if __name__=='__main__':main()
