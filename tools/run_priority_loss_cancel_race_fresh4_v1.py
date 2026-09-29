"""Fresh4 preregistered cancellation-race falsification.
P = frozen Pair-only parity.
A = inside-spread+TTL source, no Active.
B = immediate priority-loss cancel race; Active only after explicit terminal zero-fill.
C = same but loss must persist one 500ms execution-feedback horizon.
"""
from __future__ import annotations
import argparse,json,math,tempfile,zipfile,sys,importlib.util
from pathlib import Path
ROOT=Path.cwd() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_sustained_priority_loss_feedback_horizon_replication4_v1.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('staged_feedback_fresh4',_STAGED);fb=importlib.util.module_from_spec(sp);sp.loader.exec_module(fb)
else:
    import tools.run_sustained_priority_loss_feedback_horizon_replication4_v1 as fb
rr=fb.rr;v1=fb.v1;base=rr.isp.base
EPS=1e-9;MIDS=(1825962,1827839,1829561,1830604)
CELLS=('P_PAIR_ONLY_PARITY','A_INSIDE_SPREAD_TTL_NO_ACTIVE','B_IMMEDIATE_PRIORITY_LOSS_CANCEL_RACE','C_SUSTAINED_500MS_PRIORITY_LOSS_CANCEL_RACE')

def eq(a,b):
    if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(eq(a[k],b[k]) for k in a)
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-9)
    return a==b

def source_sig(r):
    pc=r.get('probeCandidate') or {}
    return {'used':bool(r.get('probeUsed')),'side':pc.get('side'),'role':pc.get('role'),'price':pc.get('probePrice'),'qty':pc.get('probeQty'),'pairSum':pc.get('probePairSum'),'key':r.get('probeKey')}
def source_out(r):
    po=r.get('probeOutcome') or {};return {'terminal':(po.get('terminal') or {}).get('status'),'terminalAt':(po.get('terminal') or {}).get('t'),
      'fillQty':sum(float(x.get('confirmedQty') or 0) for x in po.get('fills',[])),'repairQty':sum(float(x.get('matchedRepairQty') or 0) for x in po.get('fills',[]))}
def prefix_sig(r,cut):
    keep={'ROLE_SLOT_SUBMIT','ROLE_FILL','SLOT_RELEASE','SLOT_CANCEL_REQUEST'}
    return [(x.get('t'),x.get('event'),x.get('key'),x.get('role'),x.get('side'),x.get('price'),x.get('qty'),x.get('reason')) for x in r.get('slotHistory',[]) if x.get('event') in keep and int(x.get('t') or 0)<int(cut)]

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--bundle',required=True);ap.add_argument('--reference',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    op=Path(a.output).resolve()
    if op.exists():ap.error('Do not overwrite')
    ref=json.loads(Path(a.reference).read_text(encoding='utf-8'));refs={int(r['marketId']):r for r in ref['rows']}
    if tuple(sorted(refs))!=tuple(sorted(MIDS)):raise RuntimeError('Reference market set mismatch')
    trace_dir=op.parent/(op.stem+'_traces');trace_dir.mkdir(parents=True,exist_ok=False);rows=[];checks=[]
    parity_fields=('submits','fills','filledQty','floor','best','roleSubmits','roleFills','roleFillQty','pairChecks','pairBlocks','serializationBlocks','fillSideAlternations','twoSidedMaterialized','reanchors','veto')
    with tempfile.TemporaryDirectory(prefix='cancel_race_fresh4_') as folder:
        root=Path(folder)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in MIDS:z.extract(f'tapes/{mid}.json.xz',root)
        for mid in MIDS:
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper();rf=refs[mid]
            # P parity first: stop candidate cells if substrate changed.
            sim=base.MinimalPairRoleSim(tape,4,False)
            try:pr=sim.run_minimal(winner)
            finally:sim.close()
            ps=base.slim(pr);par={f:eq(ps[f],rf[f]) for f in parity_fields}
            prow={'marketId':mid,'cell':CELLS[0],'winnerPostHocOnly':winner,'parity':par,**ps};rows.append(prow)
            if not all(par.values()):raise RuntimeError(f'P parity fail {mid}: {par}')
            by={}
            for cell,mode in [(CELLS[1],'A'),(CELLS[2],'B'),(CELLS[3],'C')]:
                tp=trace_dir/f'{mid}_{cell}.jsonl';sim=fb.FeedbackHandoffSim(tape,mode,tp)
                try:r=sim.run_feedback()
                finally:sim.close()
                r['pnlDiagnosticOnly']=r['upQty' if winner=='UP' else 'downQty']-r['buyNotional'];row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,'decisionTracePath':str(tp),**r};rows.append(row);by[cell]=row;v1.write_json(trace_dir/f'{mid}_{cell}_result.json',row)
                so=source_out(row);rm=row.get('relayMeta') or {};ro=row.get('relayOutcome') or {}
                print(json.dumps({'marketId':mid,'cell':cell,'source':source_sig(row),'sourceOutcome':so,'priorityLoss':row.get('priorityLossEvent'),'lossResets':row.get('priorityLossResets'),
                    'activeUsed':bool(row.get('relayUsed')),'activePrice':rm.get('price') if rm.get('route')=='ACTIVE' else None,'activePair':rm.get('pairSum') if rm.get('route')=='ACTIVE' else None,
                    'activeSubmittedAt':rm.get('submittedAt') if rm.get('route')=='ACTIVE' else None,'activeFillQty':sum(float(x.get('confirmedQty') or 0) for x in ro.get('fills',[])) if rm.get('route')=='ACTIVE' else 0,
                    'activeRepairQty':sum(float(x.get('matchedRepairQty') or 0) for x in ro.get('fills',[])) if rm.get('route')=='ACTIVE' else 0,'fills':row['fillEvents'],'alts':row['fillSideAlternations'],'repairQty':row['economicRepairQty'],'pnl':row['pnlDiagnosticOnly'],'floor':row['floor']},allow_nan=False),flush=True)
            A,B,C=by[CELLS[1]],by[CELLS[2]],by[CELLS[3]]
            sa=source_sig(A)
            for X in (B,C):
                sx=source_sig(X)
                for k in ('used','side','role','price','qty','pairSum','key'):
                    if not eq(sa[k],sx[k]):raise RuntimeError(f'Source mismatch {mid} {X["cell"]} {k}: {sa[k]} != {sx[k]}')
                pe=X.get('priorityLossEvent');pre=True if pe is None else prefix_sig(A,pe['t'])==prefix_sig(X,pe['t'])
                if not pre:raise RuntimeError(f'Prefix mismatch {mid} {X["cell"]}')
                rm=X.get('relayMeta') or {};so=source_out(X)
                if rm.get('route')=='ACTIVE' and (so['terminal'] in (None,'FILLED') or so['fillQty']>EPS or int(rm['submittedAt'])<int(so['terminalAt'])):
                    raise RuntimeError(f'Active-before-valid-terminal {mid} {X["cell"]}')
            checks.append({'marketId':mid,'source':sa,'A_outcome':source_out(A),'B_outcome':source_out(B),'C_outcome':source_out(C),
                'B_trigger':B.get('priorityLossEvent'),'C_trigger':C.get('priorityLossEvent'),
                'A_passiveSuccessPreservedByB': not(source_out(A)['fillQty']>EPS) or source_out(B)['fillQty']>EPS,
                'A_passiveSuccessPreservedByC': not(source_out(A)['fillQty']>EPS) or source_out(C)['fillQty']>EPS})
    summary={}
    for cell in CELLS[1:]:
        xs=[r for r in rows if r.get('cell')==cell]
        summary[cell]={'markets':len(xs),'sourceUsed':sum(source_sig(r)['used'] for r in xs),'sourceFills':sum(source_out(r)['fillQty']>EPS for r in xs),
            'priorityLossEvents':sum(r.get('priorityLossEvent') is not None for r in xs),'activeRelays':sum((r.get('relayMeta') or {}).get('route')=='ACTIVE' for r in xs),
            'activeFillQty':sum(sum(float(x.get('confirmedQty') or 0) for x in (r.get('relayOutcome') or {}).get('fills',[])) if (r.get('relayMeta') or {}).get('route')=='ACTIVE' else 0 for r in xs),
            'totalRepairQty':sum(float(r['economicRepairQty']) for r in xs),'totalFills':sum(int(r['fillEvents']) for r in xs),'totalAlternations':sum(int(r['fillSideAlternations']) for r in xs),'totalPnlDiagnostic':sum(float(r['pnlDiagnosticOnly']) for r in xs)}
    v1.write_json(op,{'version':'PRIORITY_LOSS_CANCEL_RACE_FRESH4_V1','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':list(MIDS),'rows':rows,'checks':checks,'summary':summary,
        'bundleSha256':v1.sha256(a.bundle),'referenceSha256':v1.sha256(a.reference),'feedbackHorizonMs':fb.HORIZON_MS,'automaticScaleAllowed':False,
        'boundary':['fresh4 selected before candidate outcomes','P consumed parity first','inside-spread source role/side/price/qty frozen across A/B/C','Active only after explicit terminal zero-fill','cancel-vs-fill race allowed to resolve naturally under frozen 250ms response latency','no Target runtime/no winner action/no 8781/no auto-scale']})
if __name__=='__main__':main()
