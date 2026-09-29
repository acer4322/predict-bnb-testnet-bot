from __future__ import annotations
import argparse, copy, hashlib, json, tempfile, zipfile
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import build_b3_handoff_native_bundle_oneshot_manifest_v1 as manifest
from tools import run_native_selection_margin_prevalence_stagea16_v1 as margin

b2=margin.b2;base=margin.base;v3=b2.v3

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def eq_ident(a,b):
    if a is None or b is None:return a is b
    for k in set(a)|set(b):
        av=a.get(k);bv=b.get(k)
        if k in {'price','qty'}:
            if float(av).hex()!=float(bv).hex():return False
        elif av!=bv:return False
    return True

def cert_to_plain(c):
    if c is None:return None
    return {k:(float.fromhex(v['hex']) if k in {'price','qty'} and isinstance(v,dict) and 'hex' in v else v) for k,v in c.items()}

class Fork(margin.clock.InstrumentedFork):
    def commit_passive_priority_override_once(self,t,qv,end):
        # This is exactly the existing V3 passive fallback chain after the current Active-first
        # dispatch point.  q_ladder/q_pending_active are deliberately retained; no owner rewrite.
        if self.q_pending_active is None or self.q_ladder is None or self.q_ladder.get('route')!='PENDING_ACTIVE':
            raise RuntimeError('NO_PENDING_ACTIVE_AUTHORITY')
        self.q_arm=self._arm_for_open_qty(int(t),qv)  # native result is None while pendingActive exists
        try:
            return super(v3.QuantityResponsibilityLadderV3,self)._open_one_option(int(t),qv,int(end))
        finally:
            self.q_arm=None

def replay(tape,spec,seam):
    sim=Fork(tape,spec,'II','N')
    qv,end,seed_phase=manifest.replay_to(sim,int(seam['phaseOrdinal']),int(seam['eventTimestampMs']))
    return sim,qv,end,seed_phase

def new_submit(sim,before_n,before_sub,before_hist):
    keys=[]
    for n in range(before_n,int(sim.n)):
        for s in ('UP','DOWN'):
            k=f'{s}_{n}'
            if k in sim.orders:keys.append(k)
    recs=copy.deepcopy(sim.slot_history[before_hist:])
    return {'keys':keys,'submitDelta':int(sim.submits)-int(before_sub),'slotEvents':recs}

def key_identity(sim,key):
    o=sim.orders[key]
    return {'side':str(o.get('side')),'role':str(sim.key_role.get(key)),'price':float(o.get('price')),'qty':float(o.get('qty'))}

def run_arm(tape,spec,seam,manrow,arm):
    sim,qv,end,seed_phase=replay(tape,spec,seam)
    try:
        prefix=b2.behavior_state_digest(sim)
        aud=margin.audit_current_state(sim,seam['phaseOrdinal'],seam['eventTimestampMs'],qv,end)
        ref=aud['reference'];candidate=copy.deepcopy(ref['candidates']['BOUNDED_ACTIVE' if arm=='A' else 'PASSIVE_PRIMARY']['identity'])
        pre_pending=copy.deepcopy(sim.q_pending_active);pre_ladder=copy.deepcopy(sim.q_ladder);pre_pay=copy.deepcopy(sim.resp_payment_rows);pre_r0=manifest.r0(sim)
        before_n=int(sim.n);before_sub=int(sim.submits);before_hist=len(sim.slot_history)
        if arm=='A':sim._open_one_option(int(seam['eventTimestampMs']),qv,int(end))
        else:sim.commit_passive_priority_override_once(int(seam['eventTimestampMs']),qv,int(end))
        ns=new_submit(sim,before_n,before_sub,before_hist)
        key=ns['keys'][0] if len(ns['keys'])==1 else None
        actual=None if key is None else key_identity(sim,key)
        if actual is not None and arm=='A':actual['targetExpandSide']=candidate.get('targetExpandSide')
        if actual is not None and arm=='P':
            actual['managed']=False;actual['originResponsibilityId']=None;actual['targetExpandSide']=None
        post_pending=copy.deepcopy(sim.q_pending_active);post_ladder=copy.deepcopy(sim.q_ladder)
        expected=cert_to_plain(manrow['rawBundleIdentity'][arm])
        checks={
          'prefixMatchesManifest':prefix==manrow['behaviorPrefixDigest'],
          'rawReferenceStillLegal':ref['candidates']['BOUNDED_ACTIVE' if arm=='A' else 'PASSIVE_PRIMARY']['L']=='TRUE' and ref['candidates']['BOUNDED_ACTIVE' if arm=='A' else 'PASSIVE_PRIMARY']['A']=='TRUE',
          'rawCandidateMatchesFrozenManifest':eq_ident(candidate,expected),
          'exactOneNativePhysicalSubmit':len(ns['keys'])==1 and ns['submitDelta']==1,
          'submittedIdentityMatchesNativeCandidate':eq_ident(actual,expected),
          'preAccountingMatchesFrozenManifest':pre_pay==manrow['forkAuthority']['paymentsBefore'] and b2.stable(pre_r0)==b2.stable(manrow['R0']),
          'noSyntheticPaymentAtCommit':sim.resp_payment_rows==pre_pay,
          'max4':len(sim.slot_key)<=4,
        }
        if arm=='A':
            checks.update({'AConsumesPendingAuthorityNatively':post_pending is None and post_ladder is not None and post_ladder.get('route')=='ACTIVE' and post_ladder.get('activeKey')==key,
                           'AUsesNativeBoundedActiveReceipt':any(str(x.get('event'))=='QTY_FIFO_MANAGED_ACTIVE_SUBMIT' and x.get('key')==key for x in sim.q_events)})
        else:
            checks.update({'PLeavesUnselectedAAuthorityIntact':b2.stable(post_pending)==b2.stable(pre_pending) and post_ladder is not None and post_ladder.get('route')=='PENDING_ACTIVE' and b2.stable(post_ladder)==b2.stable(pre_ladder),
                           'PNotArtificiallyBoundToAOwner':sim.q_ladder.get('activeKey') is None and sim.q_pending_active.get('sourceKey')==pre_pending.get('sourceKey'),
                           'PUsesNativePassiveCommitReceipt':any(x.get('event')=='ROLE_SLOT_SUBMIT' and x.get('key')==key for x in ns['slotEvents'])})
        return {'arm':arm,'prefixDigest':prefix,'referenceIdentity':candidate,'frozenIdentity':expected,'submit':ns,'selectedKey':key,'actualIdentity':actual,
                'prePendingActive':pre_pending,'postPendingActive':post_pending,'preQLadder':pre_ladder,'postQLadder':post_ladder,'checks':checks,'pass':all(checks.values())}
    finally:sim.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--cohort',required=True);ap.add_argument('--manifest',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();co=json.loads(Path(a.cohort).read_text(encoding='utf-8'));specmap={int(x['marketId']):x for x in co['states']};mp=json.loads(Path(a.manifest).read_text(encoding='utf-8'));mmap={int(x['marketId']):x for x in mp['rows']};rows=[]
    with tempfile.TemporaryDirectory(prefix='b3_treatment_preflight_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for s in manifest.SEAMS:z.extract(f"tapes/{s['marketId']}.json.xz",root)
        for s in manifest.SEAMS:
            mid=s['marketId'];A=run_arm(root/'tapes'/f'{mid}.json.xz',specmap[mid],s,mmap[mid],'A');P=run_arm(root/'tapes'/f'{mid}.json.xz',specmap[mid],s,mmap[mid],'P')
            checks={'ACommitPass':A['pass'],'PCommitPass':P['pass'],'sameFrozenPrefix':A['prefixDigest']==P['prefixDigest']==mmap[mid]['behaviorPrefixDigest'],
                    'bothNativeCandidatesAtSameBoundary':mmap[mid]['checks']['ACompleteLegal'] and mmap[mid]['checks']['PCompleteLegal'],
                    'noAExecuteUndoNeededForP':P['submit']['submitDelta']==1 and P['preQLadder'].get('route')=='PENDING_ACTIVE',
                    'activePriorityIsDiscretionaryLegalSubmitNotMandatoryReconciliation':mmap[mid]['nativeReference']['priority']=='BOUNDED_ACTIVE' and mmap[mid]['nativeReference']['A'].get('reason')=='LEGAL'}
            row={'panel':s['panel'],'marketId':mid,'checks':checks,'pass':all(checks.values()),'A':A,'P':P};rows.append(row)
            print(json.dumps({'marketId':mid,'panel':s['panel'],'pass':row['pass'],'checks':checks,'Akey':A['selectedKey'],'Pkey':P['selectedKey']},ensure_ascii=False),flush=True)
    allp=all(r['pass'] for r in rows)
    out={'version':'B3_HANDOFF_NATIVE_BUNDLE_ONESHOT_SMOKE4_V1_TREATMENT_PREFLIGHT_20260908','researchOnly':True,'runtimeAuthority':False,'rows':rows,'pass':allp,
         'verdict':'TREATMENT_CONTRACT_PASS' if allp else 'TREATMENT_CONTRACT_BLOCKED',
         'contract':['same raw pre-action boundary','A uses untouched native BOUNDED_ACTIVE commit','P overrides only current priority dispatch and enters existing native passive fallback chain','P keeps q_ladder/q_pending_active authority unchanged until real downstream lifecycle changes it','no A execute/fail/cancel prerequisite for P','no price/qty/owner/responsibility rewrite'],
         'sha256':{'runner':sha(Path(__file__)),'manifest':sha(a.manifest),'bundle':sha(a.bundle),'cohort':sha(a.cohort)}}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':allp,'verdict':out['verdict'],'output':a.output},ensure_ascii=False))
if __name__=='__main__':main()
