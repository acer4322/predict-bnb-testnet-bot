from __future__ import annotations
import argparse, copy, hashlib, json, tempfile, zipfile
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_native_selection_margin_prevalence_stagea16_v1 as margin

base=margin.base
b2=margin.b2
EPS=margin.EPS

SEAMS=[
 {'panel':'D1','marketId':1823598,'phaseOrdinal':31,'eventTimestampMs':1788159012971,'side':'DOWN','role':'SATELLITE_REPAIR','locatorHash':'de99602be99be891dfd3f9ec6c4dc75b301467f33b771ca49bd6fcc574fe72bb'},
 {'panel':'D2','marketId':1823603,'phaseOrdinal':102,'eventTimestampMs':1788159329536,'side':'UP','role':'ECONOMIC_CORE','locatorHash':'306b842dcbc8d0bf8e7f1bbf7658313a1e2a26af5d4fead0c47fe5a28c0419b1'},
 {'panel':'R1','marketId':1823614,'phaseOrdinal':78,'eventTimestampMs':1788159922047,'side':'UP','role':'SATELLITE_REPAIR','locatorHash':'ce71cc8c07db802955cd41ed1056e5f3ad8de1ba56b8300c206b88111ac4dc89'},
 {'panel':'R2','marketId':1823755,'phaseOrdinal':27,'eventTimestampMs':1788160211421,'side':'UP','role':'ECONOMIC_CORE','locatorHash':'db7ccf62d7fd07ccc202e85fd8dc46a766de19b5e969dd097b9c0abaa7720b1a'},
]

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def fcert(x):
    if x is None:return None
    x=float(x);return {'value':x,'repr':repr(x),'hex':x.hex()}
def ident_cert(x):
    if x is None:return None
    out={}
    for k,v in x.items():
        out[k]=fcert(v) if k in {'price','qty'} and v is not None else v
    return out

def qv_cert(qv):
    out={}
    for s in ('UP','DOWN'):
        out[s]={k:fcert(qv[s][k]) for k in ('bid','ask')}
    for k in ('bd','ad','tb','ta','imb','spread'):
        if k in qv:out[k]=fcert(qv[k])
    return out

def r0(sim):
    rows=[]
    for x in sim.serializable_lots():
        if x.get('completedAt') is None and float(x.get('remainingQty') or 0)>EPS:
            y=copy.deepcopy(x);y.pop('paymentClocks',None);rows.append(y)
    return rows

def authority(sim):
    return {'qLadder':copy.deepcopy(sim.q_ladder),'qPendingActive':copy.deepcopy(sim.q_pending_active),
            'slots':copy.deepcopy(sim.slot_key),'liveOrders':{str(k):copy.deepcopy(sim.orders.get(k)) for k in sim.slot_key.values()},
            'keyRole':{str(k):str(sim.key_role.get(k)) for k in sim.slot_key.values()},'R0':r0(sim),
            'paymentsBefore':copy.deepcopy(sim.resp_payment_rows),'inventory':copy.deepcopy(sim.inv),'cost':float(sim.cost)}

def replay_to(sim,target_phase,target_t):
    updates=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0])))
    first=int(sim.meta['firstReceivedMs']);base.v2.base.ex.advance_to(sim.bt,first)
    end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
    seed_phase=None
    for ordinal,u in enumerate(updates):
        t=int(u[1]);sim.set_phase(ordinal,t);base.v2.base.ex.advance_to(sim.bt,t);sim.process(t);sim.cancel_expired(t);sim._refresh_slots(t)
        base.v2.base.apply(sim.book,u);qv=base.v2.base.quotes(sim.book)
        if qv:sim._risk_contract_if_needed(t);sim._reanchor_stale(t)
        if ordinal==target_phase:
            if t!=target_t:raise RuntimeError(f'TARGET_TIMESTAMP_MISMATCH:{ordinal}:{t}:{target_t}')
            if qv is None:raise RuntimeError('TARGET_NO_QV')
            return qv,end,seed_phase
        before=bool(sim.postSeed)
        if qv:sim._open_one_option(t,qv,end)
        if (not before) and sim.postSeed:seed_phase=ordinal
        sim._sample_occupancy()
    raise RuntimeError('TARGET_PHASE_NOT_REACHED')

def build_row(tape,spec,seam):
    sim=margin.clock.InstrumentedFork(tape,spec,'II','N')
    try:
        qv,end,seed_phase=replay_to(sim,seam['phaseOrdinal'],seam['eventTimestampMs'])
        before=b2.behavior_state_digest(sim)
        a=margin.audit_current_state(sim,seam['phaseOrdinal'],seam['eventTimestampMs'],qv,end)
        after=b2.behavior_state_digest(sim)
        ref=a['reference'];A=ref['candidates']['BOUNDED_ACTIVE'];P=ref['candidates']['PASSIVE_PRIMARY']
        checks={
          'locatorHashExact':a['stateHash']==seam['locatorHash'],
          'observerReadOnly':before==after and a['observerMutationInert'],
          'menuReferenceClean':a['errorsClean'],
          'nativePriorityActive':ref['priority']=='BOUNDED_ACTIVE',
          'ACompleteLegal':A.get('identity') is not None and A.get('L')=='TRUE' and A.get('A')=='TRUE',
          'PCompleteLegal':P.get('identity') is not None and P.get('L')=='TRUE' and P.get('A')=='TRUE',
          'sameSideRole':A.get('identity',{}).get('side')==P.get('identity',{}).get('side')==seam['side'] and A.get('identity',{}).get('role')==P.get('identity',{}).get('role')==seam['role'],
          'pendingActiveAuthorityPresent':sim.q_pending_active is not None and sim.q_ladder is not None and sim.q_ladder.get('route')=='PENDING_ACTIVE',
          'seedOccurredBeforeSeam':seed_phase is not None and seed_phase<seam['phaseOrdinal'],
        }
        return {'panel':seam['panel'],'marketId':seam['marketId'],'phaseOrdinal':seam['phaseOrdinal'],'eventTimestampMs':seam['eventTimestampMs'],
                'compactLocatorHash':seam['locatorHash'],'rawStateHash':a['stateHash'],'seedPhaseOrdinal':seed_phase,
                'rawQv':qv_cert(qv),'rawBundleIdentity':{'A':ident_cert(A['identity']),'P':ident_cert(P['identity'])},
                'nativeReference':{'priority':ref['priority'],'A':A,'P':P,'universeSize':ref['universeSize']},
                'forkAuthority':authority(sim),'R0':r0(sim),'PPlannedQuoteNotional':float(P['identity']['price'])*float(P['identity']['qty']),
                'deltaMateriality':0.01*float(P['identity']['price'])*float(P['identity']['qty']),
                'behaviorPrefixDigest':before,'checks':checks,'pass':all(checks.values())}
    finally:sim.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--cohort',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();co=json.loads(Path(a.cohort).read_text(encoding='utf-8'));specmap={int(x['marketId']):x for x in co['states']};rows=[]
    with tempfile.TemporaryDirectory(prefix='b3_manifest_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for s in SEAMS:z.extract(f"tapes/{s['marketId']}.json.xz",root)
        for s in SEAMS:
            row=build_row(root/'tapes'/f"{s['marketId']}.json.xz",specmap[s['marketId']],s);rows.append(row)
            print(json.dumps({'marketId':s['marketId'],'panel':s['panel'],'pass':row['pass'],'checks':row['checks'],'u':row['PPlannedQuoteNotional'],'delta':row['deltaMateriality']},ensure_ascii=False),flush=True)
    out={'version':'B3_HANDOFF_NATIVE_BUNDLE_ONESHOT_SMOKE4_V1_PREREG_20260908','createdBeforeTreatmentCommit':True,'researchOnly':True,'runtimeAuthority':False,
         'question':'same-side/same-role PENDING_ACTIVE one-shot complete native PASSIVE_PRIMARY vs BOUNDED_ACTIVE, then native continuation',
         'rows':rows,'allRawRestorePass':all(r['pass'] for r in rows),'fixedVerdictThresholds':{'behavior':1e-8,'cashflow':1e-7,'materialityRule':'delta_i = 0.01 * raw native P planned quote notional','throughputRatio':0.90},
         'boundaries':['fixed four consumed seams only','raw native replay/certificate; compact identities are locators only','no winner/future fill/PnL in restore or candidate validation','A/P side/price/qty/role are recomputed from raw native state','no treatment commit in manifest builder','no fresh/no live8781/no dream fill'],
         'sha256':{'builder':sha(Path(__file__)),'bundle':sha(a.bundle),'cohort':sha(a.cohort),'stageA16Runner':sha(Path(margin.__file__)),'b2Runner':sha(Path(b2.__file__)),'v3bRuntime':sha(Path(margin.b2.v3b.__file__))}}
    op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':out['allRawRestorePass'],'output':str(op),'rows':len(rows)},ensure_ascii=False))
if __name__=='__main__':main()
