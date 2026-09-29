"""Preregistered timing replication on first-cycle passive-exhausted markets.
A = frozen source retained to terminal then Active.
B = same source; if strict-past bestBid > own source price while zero-fill, cancel;
    only after explicit terminal zero-fill does inherited same-intent Active handoff occur.
"""
from __future__ import annotations
import argparse,json,math,tempfile,zipfile,sys,importlib.util
from pathlib import Path
ROOT=Path.cwd() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_passive_exhaustion_same_intent_route_relay_1823755_v1.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('staged_first_cycle_priority',_STAGED);rr=importlib.util.module_from_spec(sp);sp.loader.exec_module(rr)
else:
    import tools.run_passive_exhaustion_same_intent_route_relay_1823755_v1 as rr
v1=rr.v1;EPS=1e-9;MIDS=(1823755,1826386,1827418);CELLS=('A_TTL_ACTIVE_HANDOFF','B_PRIORITY_LOSS_ACTIVE_HANDOFF')

class FirstPriorityLossSim(rr.RouteRelaySim):
    def __init__(self,tape,enable:bool,trace_path):
        super().__init__(tape,rr.CELLS[2],trace_path)
        self.priority_enabled=bool(enable);self.priority_event=None;self.priority_cancel=False
    def _reanchor_stale(self,t):
        if self.priority_enabled and self.probe_key and not self.priority_cancel:
            o=self.orders.get(self.probe_key)
            if o and not o.get('cancelRequested') and float(o.get('cum') or 0)<=EPS:
                qv=rr.isp.base.v2.base.quotes(self.book)
                if qv:
                    bid=float(qv[str(o['side'])]['bid']);ask=float(qv[str(o['side'])]['ask']);px=float(o['price'])
                    if bid>px+EPS:
                        sid=next((sid for sid,key in self.slot_key.items() if key==self.probe_key),None)
                        if sid is not None:
                            self.priority_event={'t':int(t),'key':self.probe_key,'side':str(o['side']),'role':self.key_role.get(self.probe_key),
                                                 'ownPrice':px,'bestBid':bid,'bestAsk':ask,'ageMs':int(t)-int(o['placed']),'cum':float(o.get('cum') or 0)}
                            ok=self._request_cancel(t,int(sid),'FIRST_CYCLE_PRIORITY_LOST');self.priority_event['cancelRequestedSuccessfully']=bool(ok);self.priority_cancel=bool(ok)
        return super()._reanchor_stale(t)
    def run_priority(self):
        r=super().run_route();r.update({'priorityLossEnabled':self.priority_enabled,'priorityLossEvent':self.priority_event,'priorityCancelRequested':self.priority_cancel,'automaticScaleAllowed':False});return r

def eq(a,b):
    if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(eq(a[k],b[k]) for k in a)
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-9)
    return a==b

def source_sig(r):
    pc=r.get('probeCandidate') or {};return {k:pc.get(k) for k in ('side','role','probePrice','probeQty','probePairSum')}
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
    with tempfile.TemporaryDirectory(prefix='first_priority_repl3_') as folder:
        root=Path(folder)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in MIDS:z.extract(f'tapes/{mid}.json.xz',root)
        for mid in MIDS:
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper();rf=refs[mid]
            if v1.sha256(tape)!=rf['tapeSha256']:raise RuntimeError(f'Tape mismatch {mid}')
            by={}
            for cell,en in [(CELLS[0],False),(CELLS[1],True)]:
                tp=trace_dir/f'{mid}_{cell}.jsonl';sim=FirstPriorityLossSim(tape,en,tp)
                try:r=sim.run_priority()
                finally:sim.close()
                r['pnlDiagnosticOnly']=r['upQty' if winner=='UP' else 'downQty']-r['buyNotional'];row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,'tapeSha256':rf['tapeSha256'],'decisionTracePath':str(tp),**r}
                if cell==CELLS[0]:
                    row['baselineParity']={f:eq(row[f],rf[f]) for f in fields}
                    if not all(row['baselineParity'].values()):raise RuntimeError(f'A parity fail {mid}: {row["baselineParity"]}')
                rows.append(row);by[cell]=row;v1.write_json(trace_dir/f'{mid}_{cell}_result.json',row)
                ro=row.get('relayOutcome') or {};rm=row.get('relayMeta') or {}
                print(json.dumps({'marketId':mid,'cell':cell,'priorityLoss':row.get('priorityLossEvent'),'source':source_sig(row),
                                  'fills':row['fillEvents'],'alts':row['fillSideAlternations'],'repairQty':row['economicRepairQty'],'pnl':row['pnlDiagnosticOnly'],'floor':row['floor'],
                                  'activePrice':rm.get('price'),'activePair':rm.get('pairSum'),'activeSubmittedAt':rm.get('submittedAt'),
                                  'activeTerminal':(ro.get('terminal') or {}).get('status'),'activeFillQty':sum(float(x.get('confirmedQty') or 0) for x in ro.get('fills',[])),
                                  'activeRepairQty':sum(float(x.get('matchedRepairQty') or 0) for x in ro.get('fills',[]))},allow_nan=False),flush=True)
            A=by[CELLS[0]];B=by[CELLS[1]]
            if not eq(source_sig(A),source_sig(B)):raise RuntimeError(f'Source mismatch {mid}')
            pe=B.get('priorityLossEvent')
            pre_same=True if pe is None else prefix_sig(A,pe['t'])==prefix_sig(B,pe['t'])
            checks.append({'marketId':mid,'priorityLossObserved':pe is not None,'prefixSameBeforeTrigger':pre_same,
                           'sourceSame':eq(source_sig(A),source_sig(B))})
            if not pre_same:raise RuntimeError(f'Prefix mismatch {mid}')
    summary={}
    for cell in CELLS:
        xs=[r for r in rows if r['cell']==cell];summary[cell]={'markets':len(xs),'priorityLossEvents':sum(r.get('priorityLossEvent') is not None for r in xs),
            'totalFills':sum(int(r['fillEvents']) for r in xs),'totalAlternations':sum(int(r['fillSideAlternations']) for r in xs),
            'totalRepairQty':sum(float(r['economicRepairQty']) for r in xs),'totalPnlDiagnostic':sum(float(r['pnlDiagnosticOnly']) for r in xs),
            'activeFillQty':sum(sum(float(x.get('confirmedQty') or 0) for x in (r.get('relayOutcome') or {}).get('fills',[])) for r in xs),
            'activePriceMean':sum(float((r.get('relayMeta') or {}).get('price') or 0) for r in xs)/len(xs)}
    v1.write_json(op,{'version':'FIRST_CYCLE_PRIORITY_LOSS_ACTIVE_HANDOFF_REPLICATION3_V1','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,
        'markets':list(MIDS),'rows':rows,'checks':checks,'summary':summary,'bundleSha256':v1.sha256(a.bundle),'referenceSha256':v1.sha256(a.reference),'automaticScaleAllowed':False,
        'boundary':['same frozen inside-spread source','B differs only when bestBid>ownPrice while source zero-fill','cancel ack required before Active','same Active side/role/qty ceiling','500ms remainder','<=180s','realistic HFT/no dream fill/no 8781','no Target runtime','winner posthoc only','no age/pair threshold inference']})
if __name__=='__main__':main()
