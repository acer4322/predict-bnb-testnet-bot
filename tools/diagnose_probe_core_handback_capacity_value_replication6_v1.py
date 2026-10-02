from __future__ import annotations
import argparse, importlib.util, json, tempfile, zipfile, sys
from pathlib import Path

ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd().parent/'run_eth_quantity_responsibility_ladder_v3f_initial_probe_core_handback.py'
if _STAGED.exists():
    spec=importlib.util.spec_from_file_location('staged_v3f',_STAGED); v3f=importlib.util.module_from_spec(spec); spec.loader.exec_module(v3f)
else:
    import tools.run_eth_quantity_responsibility_ladder_v3f_initial_probe_core_handback as v3f
v3b=v3f.v3b
EPS=1e-12


def first_event(events,name, pred=lambda e: True):
    return next((e for e in events if e.get('event')==name and pred(e)),None)

def role_submit_key(slot_hist,t,side,role,price,qty):
    c=[]
    for e in slot_hist:
        if e.get('event')!='ROLE_SLOT_SUBMIT': continue
        if int(e.get('t',-1))!=int(t): continue
        if e.get('side')!=side or e.get('role')!=role: continue
        if abs(float(e.get('price',0))-float(price))>1e-9: continue
        if abs(float(e.get('qty',0))-float(qty))>1e-7: continue
        c.append(e)
    return c[0].get('key') if c else None

def key_lifecycle(slot_hist,key,t0):
    if not key:return {'key':None}
    fills=[e for e in slot_hist if e.get('key')==key and e.get('event')=='ROLE_FILL' and int(e.get('t',0))>=t0]
    rel=[e for e in slot_hist if e.get('key')==key and e.get('event')=='SLOT_RELEASE' and int(e.get('t',0))>=t0]
    return {
        'key':key,
        'firstFillDelayMs': (int(fills[0]['t'])-t0) if fills else None,
        'fillQty':sum(float(e.get('fillInc',0)) for e in fills),
        'firstFillPrice':float(fills[0].get('price')) if fills else None,
        'terminalDelayMs':(int(rel[0]['t'])-t0) if rel else None,
        'terminalStatus':rel[0].get('status') if rel else None,
        'terminalCum':float(rel[0].get('cum',0)) if rel else None,
    }

def window_stats(slot_hist,t0,ms):
    fills=[e for e in slot_hist if e.get('event')=='ROLE_FILL' and t0<=int(e.get('t',0))<=t0+ms]
    submits=[e for e in slot_hist if e.get('event')=='ROLE_SLOT_SUBMIT' and t0<=int(e.get('t',0))<=t0+ms]
    by_role={}; by_side={}; qty_role={}
    for e in fills:
        r=str(e.get('role')); s=str(e.get('side')); q=float(e.get('fillInc',0))
        by_role[r]=by_role.get(r,0)+1; by_side[s]=by_side.get(s,0)+1; qty_role[r]=qty_role.get(r,0)+q
    return {'fillEvents':len(fills),'submits':len(submits),'byRole':by_role,'bySide':by_side,'qtyByRole':qty_role}

def active_fill_for_origin(qevents,origin,t0):
    sub=next((e for e in qevents if e.get('event')=='QTY_FIFO_MANAGED_ACTIVE_SUBMIT' and int(e.get('originResponsibilityId',-1))==origin and int(e.get('t',0))>=t0),None)
    if not sub:return {'submit':None,'fill':None}
    key=sub.get('key')
    fill=next((e for e in qevents if e.get('event')=='QTY_FIFO_MANAGED_FILL' and e.get('route')=='ACTIVE' and e.get('key')==key and int(e.get('t',0))>=int(sub['t'])),None)
    return {'submit':sub,'fill':fill}

def run_one(tape,Cls):
    sim=Cls(tape)
    try:return sim.run_qty('__UNSCORED__')
    finally:sim.close()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--market-ids',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]; out=[]
    with tempfile.TemporaryDirectory(prefix='probe_core_capacity_diag_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for mid in mids:
            tape=root/'tapes'/f'{mid}.json.xz'
            A=run_one(tape,v3b.FifoAggregateResponsibilityLadderV3B)
            B=run_one(tape,v3f.InitialProbeCoverageCoreHandbackV3F)
            qB=B.get('quantityLadderEvents',[])
            hb=first_event(qB,'QTY_FIFO_INITIAL_PROBE_CORE_ACTIVE_HANDBACK_ELIGIBLE')
            if hb is None:
                out.append({'marketId':mid,'eligible':False}); continue
            t=int(hb['t']); origin=int(hb['originResponsibilityId'])
            hbe=first_event(qB,'QTY_FIFO_CROSSLOT_ACTIVE_HANDBACK',lambda e:int(e.get('originResponsibilityId',-1))==origin and int(e.get('t',0))==t)
            ord_e=first_event(qB,'QTY_FIFO_ORDINARY_HANDBACK_SUBMIT',lambda e:int(e.get('originResponsibilityId',-1))==origin and int(e.get('t',0))>=t)
            a_act=active_fill_for_origin(A.get('quantityLadderEvents',[]),origin,t)
            ord_key=None; ord_life=None
            if ord_e:
                ord_key=role_submit_key(B.get('slotHistory',[]),int(ord_e['t']),ord_e['side'],ord_e['role'],ord_e['price'],ord_e['qty'])
                ord_life=key_lifecycle(B.get('slotHistory',[]),ord_key,int(ord_e['t']))
            active_sub=a_act['submit']; active_fill=a_act['fill']
            active_delay=(int(active_fill['t'])-int(active_sub['t'])) if active_sub and active_fill else None
            winner=str(co[mid]['winner']).upper(); pnlA=float(A['upQty' if winner=='UP' else 'downQty'])-float(A['buyNotional']); pnlB=float(B['upQty' if winner=='UP' else 'downQty'])-float(B['buyNotional'])
            row={
                'marketId':mid,'eligible':True,'handbackT':t,'originResponsibilityId':origin,
                'ordinary':ord_e,'ordinaryLifecycle':ord_life,
                'baselineActiveSubmit':active_sub,'baselineActiveFill':active_fill,'baselineActiveFillDelayMs':active_delay,
                'activeVsOrdinaryPriceTicks': ((float(active_sub['limitPrice'])-float(ord_e['price']))/0.01) if active_sub and ord_e and active_sub.get('side')==ord_e.get('side') else None,
                'ordinarySameIntentAsActive': bool(active_sub and ord_e and active_sub.get('side')==ord_e.get('side') and active_sub.get('role')==ord_e.get('role')),
                'windows':{},
                'terminalDelta':{'pnl':pnlB-pnlA,'floor':float(B['floor'])-float(A['floor']),'fills':int(B['fillEvents'])-int(A['fillEvents']),'submits':int(B['submits'])-int(A['submits']),'alternations':int(B['fillSideAlternations'])-int(A['fillSideAlternations'])},
                'ledgerA':A['quantityLedgerSummary']['invariantViolations'],'ledgerB':B['quantityLedgerSummary']['invariantViolations']
            }
            for ms in (1000,3000,5000,10000): row['windows'][str(ms)]={'A':window_stats(A.get('slotHistory',[]),t,ms),'B':window_stats(B.get('slotHistory',[]),t,ms)}
            out.append(row)
            print(json.dumps({'marketId':mid,'ordRole':ord_e.get('role') if ord_e else None,'ordSide':ord_e.get('side') if ord_e else None,'ordPx':ord_e.get('price') if ord_e else None,'ordFillDelay':ord_life.get('firstFillDelayMs') if ord_life else None,'activeLimit':active_sub.get('limitPrice') if active_sub else None,'activeFillDelay':active_delay,'priceTicks':row['activeVsOrdinaryPriceTicks'],'delta':row['terminalDelta']},ensure_ascii=False),flush=True)
    doc={'version':'PROBE_CORE_HANDBACK_CAPACITY_VALUE_REPLICATION6_V1','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':out,'boundary':['fixed outcome-blind replication6 only','behavior-inert diagnostic; no strategy mutation','winner/PnL posthoc labels only','no NEW24-B','activity density explicitly retained']}
    op=Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'markets':len(out)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
