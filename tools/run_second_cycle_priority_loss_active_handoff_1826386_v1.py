"""Research-only timing falsification for second-cycle Active handoff on 1826386.
A = frozen second-cycle inside-spread Passive retained to TTL, then Active.
B = same source; when strict-past bestBid moves strictly above own passive price while zero-fill,
    request cancel; only after explicit terminal zero-fill, same-intent Active current ask.
No new route/side/role/qty authority.
"""
from __future__ import annotations
import argparse,json,math,tempfile,zipfile,sys,importlib.util
from pathlib import Path
ROOT=Path.cwd() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_recursive_execution_ladder_second_cycle_1826386_v1.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('staged_recursive_ladder_priority',_STAGED);rl=importlib.util.module_from_spec(sp);sp.loader.exec_module(rl)
else:
    import tools.run_recursive_execution_ladder_second_cycle_1826386_v1 as rl
v1=rl.v1;EPS=1e-9
CELLS=('A_TTL_ACTIVE_HANDOFF','B_PRIORITY_LOSS_ACTIVE_HANDOFF')

class PriorityLossSim(rl.RecursiveLadderSim):
    def __init__(self,tape,priority_loss:bool,trace_path):
        super().__init__(tape,rl.CELLS[2],trace_path)
        self.priority_loss_enabled=bool(priority_loss)
        self.priority_loss_event=None
        self.priority_cancel_requested=False

    def _reanchor_stale(self,t):
        if self.priority_loss_enabled and self.second_source_key and not self.priority_cancel_requested and self.second_source_terminal is None:
            o=self.orders.get(self.second_source_key)
            if o and not o.get('cancelRequested') and float(o.get('cum') or 0.0)<=EPS:
                qv=rl.rr.isp.base.v2.base.quotes(self.book)
                if qv:
                    bid=float(qv[str(o['side'])]['bid']);ask=float(qv[str(o['side'])]['ask']);px=float(o['price'])
                    if bid>px+EPS:
                        sid=next((sid for sid,key in self.slot_key.items() if key==self.second_source_key),None)
                        if sid is not None:
                            self.priority_loss_event={'t':int(t),'key':self.second_source_key,'side':str(o['side']),'role':self.key_role.get(self.second_source_key),
                                'ownPrice':px,'bestBid':bid,'bestAsk':ask,'ageMs':int(t)-int(o['placed']),'cum':float(o.get('cum') or 0.0)}
                            ok=self._request_cancel(t,int(sid),'SECOND_CYCLE_PRIORITY_LOST')
                            self.priority_loss_event['cancelRequestedSuccessfully']=bool(ok)
                            self.priority_cancel_requested=bool(ok)
        return super()._reanchor_stale(t)

    def run_priority(self):
        r=super().run_recursive()
        r.update({'priorityLossEnabled':self.priority_loss_enabled,'priorityLossEvent':self.priority_loss_event,
                  'priorityCancelRequested':self.priority_cancel_requested,'automaticScaleAllowed':False})
        return r

def eq(a,b):
    if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(eq(a[k],b[k]) for k in a)
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-9)
    return a==b

def prefix_sig(r,cut):
    keep={'ROLE_SLOT_SUBMIT','SAME_INTENT_ACTIVE_RELAY_SUBMIT','ROLE_FILL','SLOT_RELEASE','SLOT_CANCEL_REQUEST'}
    return [(x.get('t'),x.get('event'),x.get('key'),x.get('role'),x.get('side'),x.get('price'),x.get('qty'),x.get('reason')) for x in r.get('slotHistory',[]) if x.get('event') in keep and int(x.get('t') or 0)<int(cut)]

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--bundle',required=True);ap.add_argument('--reference',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mid=1826386
    op=Path(a.output).resolve()
    if op.exists():ap.error('Do not overwrite')
    ref=json.loads(Path(a.reference).read_text(encoding='utf-8'))
    if ref.get('marketId')!=mid or ref.get('cell')!=rl.CELLS[2]:raise RuntimeError('Reference must be frozen C second-cycle ladder result')
    fields=('submits','fillEvents','filledQty','upQty','downQty','buyNotional','floor','best','fillSideAlternations','twoSidedMaterialized','roleSubmits','roleFills','roleFillQty','reanchors','economicRepairQty','economicOverflowQty')
    trace_dir=op.parent/(op.stem+'_traces');trace_dir.mkdir(parents=True,exist_ok=False);rows=[]
    with tempfile.TemporaryDirectory(prefix='priority_loss_1826386_') as folder:
        root=Path(folder)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']};z.extract(f'tapes/{mid}.json.xz',root)
        tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
        if v1.sha256(tape)!=ref['tapeSha256']:raise RuntimeError('Tape mismatch')
        for cell,en in [(CELLS[0],False),(CELLS[1],True)]:
            tp=trace_dir/f'{mid}_{cell}.jsonl';sim=PriorityLossSim(tape,en,tp)
            try:r=sim.run_priority()
            finally:sim.close()
            r['pnlDiagnosticOnly']=r['upQty' if winner=='UP' else 'downQty']-r['buyNotional'];row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,'tapeSha256':ref['tapeSha256'],'decisionTracePath':str(tp),**r}
            if cell==CELLS[0]:
                row['baselineParity']={f:eq(row[f],ref[f]) for f in fields}
                if not all(row['baselineParity'].values()):raise RuntimeError('A parity failed '+str(row['baselineParity']))
            rows.append(row);v1.write_json(trace_dir/f'{mid}_{cell}_result.json',row)
            so=row.get('secondSourceOutcome') or {};sm=row.get('secondSourceMeta') or {};ao=row.get('secondActiveOutcome') or {};am=row.get('secondActiveMeta') or {}
            print(json.dumps({'cell':cell,'submits':row['submits'],'fills':row['fillEvents'],'alts':row['fillSideAlternations'],'repairQty':row['economicRepairQty'],'pnl':row['pnlDiagnosticOnly'],'floor':row['floor'],
                'sourcePrice':sm.get('probePrice'),'sourcePair':sm.get('pairSum'),'sourceTerminal':(so.get('terminal') or {}).get('status'),'sourceFillQty':sum(float(x.get('confirmedQty') or 0) for x in so.get('fills',[])),
                'priorityLoss':row.get('priorityLossEvent'),'activeUsed':row.get('secondActiveUsed'),'activePrice':am.get('price'),'activePair':am.get('pairSum'),'activeSubmittedAt':am.get('submittedAt'),
                'activeTerminal':(ao.get('terminal') or {}).get('status'),'activeFillQty':sum(float(x.get('confirmedQty') or 0) for x in ao.get('fills',[])),'activeRepairQty':sum(float(x.get('matchedRepairQty') or 0) for x in ao.get('fills',[]))},allow_nan=False),flush=True)
    A,B=rows
    if not eq({k:A.get('secondSourceMeta',{}).get(k) for k in ('side','role','inheritedPrice','probePrice','probeQty')},{k:B.get('secondSourceMeta',{}).get(k) for k in ('side','role','inheritedPrice','probePrice','probeQty')}):raise RuntimeError('Second source mismatch')
    pe=B.get('priorityLossEvent')
    if pe is None:raise RuntimeError('No priority-loss event observed')
    contract={'firstActiveFillSame':A.get('firstActiveFillAt')==B.get('firstActiveFillAt'),
              'postActiveExpandFillSame':A.get('postActiveExpandFillAt')==B.get('postActiveExpandFillAt') and A.get('postActiveExpandFillKey')==B.get('postActiveExpandFillKey'),
              'prefixSameBeforePriorityLoss':prefix_sig(A,pe['t'])==prefix_sig(B,pe['t'])}
    if not all(contract.values()):raise RuntimeError('Pre-trigger contract fail '+str(contract))
    v1.write_json(op,{'version':'SECOND_CYCLE_PRIORITY_LOSS_ACTIVE_HANDOFF_1826386_V1','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'marketId':mid,'rows':rows,'preTriggerContract':contract,
        'bundleSha256':v1.sha256(a.bundle),'referenceSha256':v1.sha256(a.reference),'automaticScaleAllowed':False,
        'boundary':['same second-cycle inside-spread source','B changes only cancel timing at strict bestBid>ownPrice zero-fill state','Active only after explicit source terminal zero-fill','same side/role/qty ceiling','500ms remainder','<=180s','realistic HFT/no dream fill/no 8781','no Target runtime','winner posthoc only','no pair/age threshold']})
if __name__=='__main__':main()
