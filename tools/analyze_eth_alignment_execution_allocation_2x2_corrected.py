from __future__ import annotations
import argparse,json
from pathlib import Path
EPS=1e-9

def fill_events(diag,shared):
    name='ALIGNMENT_ALLOCATION_V2_FILL' if shared else 'COMPOSITE_FILL_ALLOCATION'
    return [e for e in (diag.get('allocationEvents') or []) if e.get('event')==name and float(e.get('fillInc') or 0.0)>EPS]

def active_submit_map(diag):
    out={}
    for e in diag.get('v89cEvents') or []:
        if e.get('event') in ('ALIGNMENT_PER_RESPONSIBILITY_ACTIVE_SUBMIT','V89D_ACTIVE_COMPOSITE_SUBMIT'):
            out[int(e.get('parentId'))]=str(e.get('key'))
    for e in diag.get('residualActiveEvents') or []:
        if e.get('event')=='RESIDUAL_REPAIR_ACTIVE_HANDOFF_SUBMIT' and e.get('submitOk'):
            out[int(e.get('parentId'))]=str(e.get('activeKey'))
    return out

def residual_parent(diag):
    cands=[]
    for e in diag.get('residualActiveEvents') or []:
        if e.get('event')!='RESIDUAL_REPAIR_ACTIVE_HANDOFF_CHECK': continue
        if float(e.get('residualDebt') or 0.0)>EPS and str(e.get('truthRole'))=='REPAIR':
            cands.append(e)
    if not cands:return None
    # Prefer a parent that later reaches terminal passive release; otherwise earliest positive residual check.
    by={}
    for e in cands: by.setdefault(int(e.get('parentId')) ,[]).append(e)
    for pid,rows in by.items():
        if any(bool(x.get('passiveReleaseConfirmed')) for x in rows): return pid
    return int(cands[0].get('parentId'))

def side_from_key(k):
    s=str(k).split('_',1)[0].upper(); return s if s in ('UP','DOWN') else None

def analyze_cell(label,cell,diag):
    shared=bool(cell.get('sharedParentAllocationV2'))
    fills=fill_events(diag,shared)
    amap=active_submit_map(diag)
    pid=residual_parent(diag)
    akey=amap.get(pid) if pid is not None else None
    af=[e for e in fills if str(e.get('key'))==str(akey)] if akey else []
    at=min((int(e.get('t')) for e in af),default=None)
    aside=side_from_key(akey) if akey else None
    opp='DOWN' if aside=='UP' else 'UP' if aside=='DOWN' else None
    later=[e for e in fills if at is not None and int(e.get('t') or -1)>at and str(e.get('key'))!=str(akey) and float(e.get('repairInc') or 0.0)>EPS and (opp is None or side_from_key(e.get('key'))==opp)]
    chain={
      'residualParentId':pid,'activeKey':akey,'activeFillEvents':af,'laterOppositeRepairFills':later[:8],
      'activeRepairFillQty':sum(float(e.get('repairInc') or 0.0) for e in af),
      'laterRepairFillQty':sum(float(e.get('repairInc') or 0.0) for e in later),
      'fullResidualRelayPhysical':bool(pid is not None and af and later),
    }
    return chain

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    src=json.load(open(a.input,encoding='utf-8')); rows=[]
    by={x['label']:x for x in src.get('cells',[])}
    for label,cell in by.items():
        diag=(src.get('diagnostics') or {}).get(label,{})
        rows.append({'label':label,**analyze_cell(label,cell,diag),'safetyZero':bool(cell.get('safetyZero')),'allocationConservation':bool(cell.get('allocationConservation')),'sharedParentDebtBounded':bool(cell.get('sharedParentDebtBounded')),'fills':cell.get('fills'),'floor':cell.get('floor'),'pnlDiagnosticOnly':cell.get('pnlDiagnosticOnly')})
    rr={x['label']:x for x in rows}; d=rr.get('D_CAP_OFF_SHARED_ALLOC_V2',{})
    gates={'D_fullResidualRelayPhysical':bool(d.get('fullResidualRelayPhysical')),'D_safetyZero':bool(d.get('safetyZero')),'D_allocationConservation':bool(d.get('allocationConservation')),'D_sharedParentDebtBounded':bool(d.get('sharedParentDebtBounded'))}
    decision='CORRECTED_INTERACTION_D_PASS_TO_ONE_FRESH_REPLICATION' if all(gates.values()) else 'CORRECTED_INTERACTION_STILL_INCOMPLETE'
    out={'version':'ALIGNMENT_EXECUTION_ALLOCATION_2X2_CORRECTED_SCORING_V1','date':'2026-09-04','source':a.input,'behaviorMutation':False,'decision':decision,'gates':gates,'cells':rows,'correction':'Original score anchored later Repair only to residualActiveKeys. In uncapped cells the generic per-responsibility router legitimately claimed the same residual Repair parent first, so the physical Active key was absent from residualActiveKeys even though it filled and was followed by opposite Repair. Corrected score binds the residual parent to whichever single Active execution authority actually owns it, then checks confirmed fill followed by opposite Repair fill.','boundary':['artifact-only rescoring','no runtime mutation','no Target input','no threshold/qty/price change','no 8781']}
    Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'D':d},ensure_ascii=False))
if __name__=='__main__':main()
