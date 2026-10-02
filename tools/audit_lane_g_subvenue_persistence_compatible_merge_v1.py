from __future__ import annotations
import argparse,json,os,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.run_lane_g_r239_confirmed_payment_binding_exact_fork_v1 import R239ConfirmedPaymentBindingFork,MID

OWNER2_BIRTH_T=1788534947089
OWNER2_ID=2
OWNER2_SIDE='UP'
OWNER2_DEBT=0.666773270050439


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    tmp=Path(tempfile.mkdtemp(prefix='lane_g_subvenue_merge_audit_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            tape=tmp/f'{MID}.json.xz'; tape.write_bytes(z.read(f'tapes/{MID}.json.xz'))
        s=R239ConfirmedPaymentBindingFork(tape,MID,'R239_BIND_CONFIRMED_NATIVE_REPAIR_BEFORE_SCOPE_FLIP')
        try:
            raw=s.run_fork(co[MID]['winner'])
            # Preserve exactly the preregistered candidate path; telemetry only.
            r239=[dict(e) for e in (getattr(s,'r239events',[]) or []) if int(e.get('t') or -1)>=OWNER2_BIRTH_T]
            split=[dict(e) for e in (getattr(s,'splitEvents',[]) or []) if int(e.get('t') or -1)>=OWNER2_BIRTH_T]
            slots=[dict(e) for e in (getattr(s,'slot_history',[]) or []) if int(e.get('t') or -1)>=OWNER2_BIRTH_T]
            obligations=[dict(o) for o in (getattr(s,'obligations',[]) or [])]
            owner2=next((o for o in obligations if int(o.get('id') or -1)==OWNER2_ID),None)
            later_births=[e for e in r239 if e.get('event') in {'R239_OVERFLOW_OBLIGATION_BORN','R239_OVERFLOW_OBLIGATION_AUGMENTED'} and int(e.get('t') or -1)>OWNER2_BIRTH_T]
            later_repair_fills=[e for e in split if e.get('event')=='ROLE_FILL_SPLIT' and float(e.get('repairAllocated') or 0.0)>1e-12 and int(e.get('t') or -1)>OWNER2_BIRTH_T]
            scope_events=[e for e in slots if 'SCOPE' in str(e.get('event') or '') or 'GENERATION' in str(e.get('event') or '')]
            compatible=[]
            for e in later_births:
                # A compatible merge candidate must represent authoritative unresolved debt whose Repair side equals owner2's Repair side.
                # R239 obligation side is overflow/expand side, so same overflow side as owner2 means same Repair side.
                if str(e.get('side'))==OWNER2_SIDE and float(e.get('outstanding') or e.get('overflowQty') or 0.0)>1e-12:
                    compatible.append(e)
            persistent=bool(owner2 and float(owner2.get('outstanding') or 0.0)>1e-12)
            verdict='COMPATIBLE_LATER_RESPONSIBILITY_FOUND' if compatible else ('PERSISTENT_UNSERVICEABLE_TAIL_LIABILITY' if persistent else 'OWNER2_CLEARED_WITHOUT_MERGE')
            out={
              'version':'LANE_G_SUB_VENUE_MIN_RESPONSIBILITY_PERSISTENCE_COMPATIBLE_MERGE_AUDIT_V1_RESULT_20260907',
              'researchOnly':True,'behaviorMutation':False,'marketId':MID,
              'owner2BirthT':OWNER2_BIRTH_T,'owner2Debt':OWNER2_DEBT,'owner2Final':owner2,
              'laterR239Births':later_births,'compatibleLaterResponsibilities':compatible,
              'laterRepairFillSplits':later_repair_fills,'scopeGenerationEvents':scope_events,
              'terminal':raw.get('terminal'),'allR239EventsAfterBirth':r239,
              'classification':verdict,
              'correctness':{
                'owner2Present':owner2 is not None,
                'owner2DebtMatches':bool(owner2 and abs(float(owner2.get('originOverflowQty') or 0.0)-OWNER2_DEBT)<=1e-12),
                'noNewBehavior':True,
                'upstreamCorrect':bool(raw.get('correct')),
              },
              'boundary':['consumed 1946468 only','payment-binding candidate path','telemetry/read-only only','no new order/authority/credit','fresh untouched','no dream fill','no 8781','no fixed-time/rank-age gate']
            }
        finally:s.close()
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output)
        op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'classification':out['classification'],'laterBirths':len(later_births),'compatible':len(compatible),'laterRepairFills':len(later_repair_fills),'owner2Final':owner2},ensure_ascii=False),flush=True)
    finally:
        import shutil; shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
