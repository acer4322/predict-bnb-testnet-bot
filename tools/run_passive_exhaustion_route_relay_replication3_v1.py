"""Preregistered 3-market conditional execution-ladder replication.

P = consumed100 Pair-only parity.
A = first eligible costly inside-spread Passive + TTL hold, no relay.
B = A source terminal zero-fill -> one second Passive frontier relay, same intent/qty.
C = A source terminal zero-fill -> one Active ask relay, same intent/qty.

No automatic scale; no winner/Target runtime authority.
"""
from __future__ import annotations
import argparse,json,math,tempfile,zipfile,sys,importlib.util
from pathlib import Path
ROOT=Path.cwd() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_passive_exhaustion_same_intent_route_relay_1823755_v1.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('staged_route_relay',_STAGED);rr=importlib.util.module_from_spec(sp);sp.loader.exec_module(rr)
else:
    import tools.run_passive_exhaustion_same_intent_route_relay_1823755_v1 as rr
v1=rr.v1
MIDS=(1826386,1827135,1827418)
CELLS=rr.CELLS
EPS=1e-9

def eq(a,b):
    if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(eq(a[k],b[k]) for k in a)
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-9)
    return a==b

def source_signature(r):
    pc=r.get('probeCandidate') or {};po=r.get('probeOutcome') or {}
    return {'used':bool(r.get('probeUsed')),'side':pc.get('side'),'role':pc.get('role'),'price':pc.get('probePrice'),
            'qty':pc.get('probeQty'),'pairSum':pc.get('probePairSum'),'key':r.get('probeKey'),
            'terminal':(po.get('terminal') or {}).get('status'),
            'fillQty':sum(float(x.get('confirmedQty') or 0) for x in po.get('fills',[])),
            'repairQty':sum(float(x.get('matchedRepairQty') or 0) for x in po.get('fills',[]))}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--bundle',required=True);ap.add_argument('--reference',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    op=Path(a.output).resolve()
    if op.exists():ap.error('Do not overwrite result')
    ref=json.loads(Path(a.reference).read_text(encoding='utf-8'));expected={int(x['marketId']):x for x in ref['rows']}
    if tuple(sorted(expected))!=tuple(sorted(MIDS)):raise RuntimeError('Reference market set mismatch')
    trace_dir=op.parent/(op.stem+'_traces');trace_dir.mkdir(parents=True,exist_ok=False)
    rows=[];checks=[]
    with tempfile.TemporaryDirectory(prefix='route_relay_repl3_') as folder:
        root=Path(folder)
        with zipfile.ZipFile(a.bundle) as z:
            cohort={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in MIDS:z.extract(f'tapes/{mid}.json.xz',root)
        for mid in MIDS:
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(cohort[mid]['winner']).upper();exp=expected[mid]
            # Frozen Pair-only parity cell.
            p=rr.isp.base.MinimalPairRoleSim(tape,4,False)
            try:pr=p.run_minimal(winner)
            finally:p.close()
            pmap={'submits':pr['submits'],'fills':pr['fillEvents'],'filledQty':pr['filledQty'],'floor':pr['floor'],'best':pr['best'],
                  'roleSubmits':pr['roleSubmits'],'roleFills':pr['roleFills'],'roleFillQty':pr['roleFillQty'],
                  'fillSideAlternations':pr['fillSideAlternations'],'twoSidedMaterialized':pr['twoSidedMaterialized'],'reanchors':pr['reanchors']}
            parity={k:eq(pmap[k],exp[k]) for k in pmap}
            rows.append({'marketId':mid,'cell':'P_PAIR_ONLY_PARITY','winnerPostHocOnly':winner,**pmap,'baselineParity':parity})
            if not all(parity.values()):raise RuntimeError(f'Pair-only parity failed {mid}: {parity}')
            by={}
            for cell in CELLS:
                tp=trace_dir/f'{mid}_{cell}.jsonl';sim=rr.RouteRelaySim(tape,cell,tp)
                try:r=sim.run_route()
                finally:sim.close()
                r['pnlDiagnosticOnly']=r['upQty' if winner=='UP' else 'downQty']-r['buyNotional']
                row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,'tapeSha256':v1.sha256(tape),'decisionTracePath':str(tp),**r}
                rows.append(row);by[cell]=row;v1.write_json(trace_dir/f'{mid}_{cell}_result.json',row)
                ss=source_signature(row);ro=row.get('relayOutcome') or {};rm=row.get('relayMeta') or {}
                print(json.dumps({'marketId':mid,'cell':cell,'source':ss,'submits':row['submits'],'fills':row['fillEvents'],
                    'alts':row['fillSideAlternations'],'repairQty':row['economicRepairQty'],'pnl':row['pnlDiagnosticOnly'],'floor':row['floor'],
                    'relayUsed':row.get('relayUsed'),'relayRoute':rm.get('route'),'relayPrice':rm.get('price'),'relayPairSum':rm.get('pairSum'),
                    'relayTerminal':(ro.get('terminal') or {}).get('status'),
                    'relayFillQty':sum(float(x.get('confirmedQty') or 0) for x in ro.get('fills',[])),
                    'relayRepairQty':sum(float(x.get('matchedRepairQty') or 0) for x in ro.get('fills',[])),
                    'relayOverflowQty':sum(float(x.get('overflowQty') or 0) for x in ro.get('fills',[]))},allow_nan=False),flush=True)
            sigs={c:source_signature(by[c]) for c in CELLS}
            source_same=all(eq(sigs[CELLS[0]],sigs[c]) for c in CELLS[1:])
            if not source_same:raise RuntimeError(f'Source path differs before relay {mid}: {sigs}')
            src=sigs[CELLS[0]];source_failed=src['used'] and src['terminal'] not in {None,'FILLED'} and src['fillQty']<=EPS
            route_checks={}
            for cell in CELLS[1:]:
                row=by[cell];rm=row.get('relayMeta') or {}
                route_checks[cell]={
                    'relayConditionalityOk': bool(row.get('relayUsed'))==bool(source_failed),
                    'sameSide': (not row.get('relayUsed')) or rm.get('side')==src['side'],
                    'sameRole': (not row.get('relayUsed')) or rm.get('role')==src['role'],
                    'qtyNotExpanded': (not row.get('relayUsed')) or float(rm.get('qty') or 0)<=float(src.get('qty') or 0)+EPS,
                    'submittedAfterSourceTerminal': (not row.get('relayUsed')) or int(rm.get('submittedAt'))>=int((by[cell].get('probeOutcome') or {}).get('terminal',{}).get('t') or 0)
                }
                if not all(route_checks[cell].values()):raise RuntimeError(f'Relay contract failed {mid} {cell}: {route_checks[cell]}')
            checks.append({'marketId':mid,'sourceSignature':src,'sourceSameAcrossCells':source_same,'sourceFailed':source_failed,'routeChecks':route_checks})
    # Summaries are descriptive; not promotion authority.
    summary={}
    for cell in ('P_PAIR_ONLY_PARITY',)+CELLS:
        xs=[r for r in rows if r['cell']==cell]
        summary[cell]={'markets':len(xs),'totalFills':sum(int(r.get('fillEvents',r.get('fills',0))) for r in xs),
                       'totalAlternations':sum(int(r.get('fillSideAlternations') or 0) for r in xs),
                       'totalRepairQty':sum(float(r.get('economicRepairQty') or 0) for r in xs),
                       'relayUsed':sum(bool(r.get('relayUsed')) for r in xs),
                       'relayFillQty':sum(sum(float(f.get('confirmedQty') or 0) for f in (r.get('relayOutcome') or {}).get('fills',[])) for r in xs),
                       'totalPnlDiagnostic':sum(float(r.get('pnlDiagnosticOnly',0) or 0) for r in xs)}
    v1.write_json(op,{'version':'PASSIVE_EXHAUSTION_ROUTE_RELAY_REPLICATION3_V1','date':'2026-09-06','researchOnly':True,
                      'runtimeAuthority':False,'markets':list(MIDS),'rows':rows,'checks':checks,'summary':summary,
                      'bundleSha256':v1.sha256(a.bundle),'referenceSha256':v1.sha256(a.reference),'automaticScaleAllowed':False,
                      'boundary':['market IDs preregistered before execution','P Pair-only parity required','A/B/C source path identical before source terminal','B/C relay only conditional on source terminal zero-fill','same side/role/source qty ceiling','one relay maximum','B Passive GTX retained to TTL','C Active GTC ask, 500ms remainder','<=180s','realistic HFT/no dream fill/no 8781','winner posthoc only','no Target runtime','pair overage diagnostic only']})
if __name__=='__main__':main()
