"""Preregistered 4-market falsification of immediate vs one-feedback-horizon priority-loss Active handoff.
A = frozen inside-spread source + TTL, no Active.
B = immediate bestBid>ownPrice cancel -> explicit terminal zero-fill -> same-intent Active.
C = same, but priority loss must persist continuously >=500ms (250ms entry + 250ms response latency).
"""
from __future__ import annotations
import argparse,json,math,tempfile,zipfile,sys,importlib.util
from pathlib import Path
ROOT=Path.cwd() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_passive_exhaustion_same_intent_route_relay_1823755_v1.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('staged_rr_feedback',_STAGED);rr=importlib.util.module_from_spec(sp);sp.loader.exec_module(rr)
else:
    import tools.run_passive_exhaustion_same_intent_route_relay_1823755_v1 as rr
v1=rr.v1;EPS=1e-9;MIDS=(1823755,1826386,1827135,1827418);HORIZON_MS=500
CELLS=('A_PASSIVE_SOURCE_NO_ACTIVE','B_IMMEDIATE_PRIORITY_LOSS_ACTIVE','C_SUSTAINED_500MS_PRIORITY_LOSS_ACTIVE')

class FeedbackHandoffSim(rr.RouteRelaySim):
    def __init__(self,tape,mode,trace_path):
        relay_cell=rr.CELLS[0] if mode=='A' else rr.CELLS[2]
        super().__init__(tape,relay_cell,trace_path)
        self.feedback_mode=mode;self.loss_start=None;self.loss_resets=0;self.priority_event=None;self.priority_cancel=False
    def _reanchor_stale(self,t):
        if self.feedback_mode!='A' and self.probe_key and not self.priority_cancel:
            o=self.orders.get(self.probe_key)
            if o and not o.get('cancelRequested') and float(o.get('cum') or 0.0)<=EPS:
                qv=rr.isp.base.v2.base.quotes(self.book)
                if qv:
                    side=str(o['side']);bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);px=float(o['price'])
                    lost=bid>px+EPS
                    if lost:
                        if self.loss_start is None:self.loss_start=int(t)
                        elapsed=int(t)-int(self.loss_start)
                        eligible=(self.feedback_mode=='B' or elapsed>=HORIZON_MS)
                        if eligible:
                            sid=next((sid for sid,key in self.slot_key.items() if key==self.probe_key),None)
                            if sid is not None:
                                self.priority_event={'t':int(t),'lossStartedAt':int(self.loss_start),'sustainedLossMs':elapsed,'requiredMs':0 if self.feedback_mode=='B' else HORIZON_MS,
                                    'key':self.probe_key,'side':side,'role':self.key_role.get(self.probe_key),'ownPrice':px,'bestBid':bid,'bestAsk':ask,
                                    'ageMs':int(t)-int(o['placed']),'cum':float(o.get('cum') or 0.0)}
                                ok=self._request_cancel(t,int(sid),'FEEDBACK_HORIZON_PRIORITY_LOSS');self.priority_event['cancelRequestedSuccessfully']=bool(ok);self.priority_cancel=bool(ok)
                    elif self.loss_start is not None:
                        self.loss_start=None;self.loss_resets+=1
        return super()._reanchor_stale(t)
    def run_feedback(self):
        r=super().run_route();r.update({'feedbackMode':self.feedback_mode,'priorityLossEvent':self.priority_event,'priorityCancelRequested':self.priority_cancel,
            'priorityLossResets':self.loss_resets,'feedbackHorizonMs':HORIZON_MS,'automaticScaleAllowed':False});return r

def eq(a,b):
    if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(eq(a[k],b[k]) for k in a)
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-9)
    return a==b

def source_sig(r):
    pc=r.get('probeCandidate') or {};po=r.get('probeOutcome') or {}
    return {'side':pc.get('side'),'role':pc.get('role'),'price':pc.get('probePrice'),'qty':pc.get('probeQty'),'pairSum':pc.get('probePairSum'),'key':r.get('probeKey'),
            'terminal':(po.get('terminal') or {}).get('status'),'fillQty':sum(float(x.get('confirmedQty') or 0) for x in po.get('fills',[]))}
def prefix_sig(r,cut):
    keep={'ROLE_SLOT_SUBMIT','ROLE_FILL','SLOT_RELEASE','SLOT_CANCEL_REQUEST'}
    return [(x.get('t'),x.get('event'),x.get('key'),x.get('role'),x.get('side'),x.get('price'),x.get('qty'),x.get('reason')) for x in r.get('slotHistory',[]) if x.get('event') in keep and int(x.get('t') or 0)<int(cut)]

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--bundle',required=True);ap.add_argument('--reference',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    op=Path(a.output).resolve()
    if op.exists():ap.error('Do not overwrite')
    ref=json.loads(Path(a.reference).read_text(encoding='utf-8'));refs={int(r['marketId']):r for r in ref['rows']}
    if tuple(sorted(refs))!=tuple(sorted(MIDS)):raise RuntimeError('Reference market set mismatch')
    fields=('submits','fillEvents','filledQty','upQty','downQty','buyNotional','floor','best','fillSideAlternations','twoSidedMaterialized','roleSubmits','roleFills','roleFillQty','reanchors','economicRepairQty','economicOverflowQty')
    trace_dir=op.parent/(op.stem+'_traces');trace_dir.mkdir(parents=True,exist_ok=False);rows=[];checks=[]
    with tempfile.TemporaryDirectory(prefix='feedback_horizon_repl4_') as folder:
        root=Path(folder)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in MIDS:z.extract(f'tapes/{mid}.json.xz',root)
        for mid in MIDS:
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper();rf=refs[mid]
            if v1.sha256(tape)!=rf['tapeSha256']:raise RuntimeError(f'Tape mismatch {mid}')
            by={}
            for cell,mode in [(CELLS[0],'A'),(CELLS[1],'B'),(CELLS[2],'C')]:
                tp=trace_dir/f'{mid}_{cell}.jsonl';sim=FeedbackHandoffSim(tape,mode,tp)
                try:r=sim.run_feedback()
                finally:sim.close()
                r['pnlDiagnosticOnly']=r['upQty' if winner=='UP' else 'downQty']-r['buyNotional'];row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,'tapeSha256':rf['tapeSha256'],'decisionTracePath':str(tp),**r}
                if cell==CELLS[0]:
                    row['baselineParity']={f:eq(row[f],rf[f]) for f in fields}
                    if not all(row['baselineParity'].values()):raise RuntimeError(f'A parity fail {mid}: {row["baselineParity"]}')
                rows.append(row);by[cell]=row;v1.write_json(trace_dir/f'{mid}_{cell}_result.json',row)
                rm=row.get('relayMeta') or {};ro=row.get('relayOutcome') or {};src=source_sig(row)
                print(json.dumps({'marketId':mid,'cell':cell,'source':src,'priorityLoss':row.get('priorityLossEvent'),'lossResets':row.get('priorityLossResets'),
                    'activeUsed':bool(row.get('relayUsed')),'activePrice':rm.get('price') if rm.get('route')=='ACTIVE' else None,'activePair':rm.get('pairSum') if rm.get('route')=='ACTIVE' else None,
                    'activeSubmittedAt':rm.get('submittedAt') if rm.get('route')=='ACTIVE' else None,'activeFillQty':sum(float(x.get('confirmedQty') or 0) for x in ro.get('fills',[])) if rm.get('route')=='ACTIVE' else 0,
                    'activeRepairQty':sum(float(x.get('matchedRepairQty') or 0) for x in ro.get('fills',[])) if rm.get('route')=='ACTIVE' else 0,
                    'fills':row['fillEvents'],'alts':row['fillSideAlternations'],'repairQty':row['economicRepairQty'],'pnl':row['pnlDiagnosticOnly'],'floor':row['floor']},allow_nan=False),flush=True)
            A,B,C=by[CELLS[0]],by[CELLS[1]],by[CELLS[2]]
            # Source identity must be unchanged through candidate cells before early cancel.
            for X in (B,C):
                sa,sx=source_sig(A),source_sig(X)
                for k in ('side','role','price','qty','pairSum','key'):
                    if not eq(sa[k],sx[k]):raise RuntimeError(f'Source mismatch {mid} {cell} {k}')
                pe=X.get('priorityLossEvent');pre=True if pe is None else prefix_sig(A,pe['t'])==prefix_sig(X,pe['t'])
                if not pre:raise RuntimeError(f'Prefix mismatch {mid} {X["cell"]}')
            checks.append({'marketId':mid,'A_source':source_sig(A),'B_trigger':B.get('priorityLossEvent'),'C_trigger':C.get('priorityLossEvent'),
                'C_suppressedKnownSuccess': mid!=1827135 or (not bool(C.get('relayUsed')) and source_sig(C)['fillQty']>0)})
    summary={}
    for cell in CELLS:
        xs=[r for r in rows if r['cell']==cell]
        summary[cell]={'markets':len(xs),'priorityLossEvents':sum(r.get('priorityLossEvent') is not None for r in xs),'activeRelays':sum(bool(r.get('relayUsed')) for r in xs),
            'sourceFills':sum(source_sig(r)['fillQty']>EPS for r in xs),'totalFills':sum(int(r['fillEvents']) for r in xs),'totalAlternations':sum(int(r['fillSideAlternations']) for r in xs),
            'totalRepairQty':sum(float(r['economicRepairQty']) for r in xs),'totalPnlDiagnostic':sum(float(r['pnlDiagnosticOnly']) for r in xs),
            'activeFillQty':sum(sum(float(x.get('confirmedQty') or 0) for x in (r.get('relayOutcome') or {}).get('fills',[])) if (r.get('relayMeta') or {}).get('route')=='ACTIVE' else 0 for r in xs)}
    v1.write_json(op,{'version':'SUSTAINED_PRIORITY_LOSS_FEEDBACK_HORIZON_REPLICATION4_V1','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,
        'markets':list(MIDS),'feedbackHorizonMs':HORIZON_MS,'rows':rows,'checks':checks,'summary':summary,'bundleSha256':v1.sha256(a.bundle),'referenceSha256':v1.sha256(a.reference),
        'automaticScaleAllowed':False,'boundary':['500ms is one structural entry+response feedback horizon; no sweep','same source role/side/price/qty','explicit terminal zero-fill before Active','same-intent Active only','no new responsibility/direction','realistic HFT/no dream fill/no 8781','winner posthoc only','no automatic scale']})
if __name__=='__main__':main()
