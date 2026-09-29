"""Read-only worker diagnostic of the last complete but censored native episode.
No fitting, replay, threshold relaxation, source mutation or broad database reads.
"""
from pathlib import Path
from collections import defaultdict, Counter
import gzip, hashlib, json, lzma, math, os, time, traceback

BASE=Path('C:/BTC5M-worker/.lan_worker_v1')
JOB='minimal-student-open-funding-train-logfix-20260911-v2'
EXPECTED='194e817ab1d8d2332301edbf5f6bbe98cfc778facad9330cbd3b19bb04e1b6dc'


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()


def main():
    assert os.environ.get('BTC5M_LAN_RESULT_DIR') and int(os.environ.get('OMP_NUM_THREADS','999'))<=4
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);start=time.monotonic()
    result=dict(version='OPEN_FUNDING_BLOCKERS_DIAG_V3',native_runs=0,model_updates=0,read_only=True)
    try:
        p=BASE/'results'/JOB/'COMPACT.json';assert p.stat().st_size<160000
        src=json.loads(p.read_text(encoding='utf-8'));e=src['evaluations'][0]
        assert e['market_id']==2022527 and src['native_complete']==1
        trace=p.parent/e['trace']['path'];assert sha(trace)==EXPECTED
        owners={};receipts=defaultdict(list);cancels=defaultdict(list);term={};pending={};last_plan=None
        delta_sums=defaultdict(float);states=[];count=Counter();n=0;max_live=0;peak_details=None
        with gzip.open(trace,'rb') as f:
            for line in f:
                n+=len(line);assert n<32*1024**2
                z=json.loads(line);k=z['kind'];x=z['data'];count[k]+=1
                if k=='native_action':
                    if x['kind']=='NEW':
                        key=x['side']+'_'+str(x['n']);assert key not in owners;owners[key]=x
                    else:cancels[x['n']].append(x)
                elif k=='canonical_receipt':
                    delta_sums[x['key']]+=x['qty'];receipts[x['key']].append(x)
                elif k=='terminal_owner_once':
                    assert x['key'] not in term;term[x['key']]=x
                elif k=='complete_policy_step':
                    pending=x['pending_carriers'];last_plan=x
                    if len(pending)>max_live:
                        max_live=len(pending);peak_details=dict(t=x['t'],counts=dict(Counter(v['state'] for v in pending.values())))
                elif k=='own_state':states.append(x)
        # Pending-before-plan may still include carriers terminalized on the last
        # receipt, so inspect identities rather than overwrite terminal records.
        recorded={k:v['owner'] for k,v in term.items()}
        overlap=set(recorded)&set(pending)
        final={**pending,**recorded}
        diffs=[];gaps=[];receipt_presence=[]
        for key,o in owners.items():
            c=final.get(key);delta=delta_sums[key]
            if c is None:continue
            gaps.append(abs(delta-c['filled']))
            if (delta==0)!=(c['filled']==0):
                diffs.append(dict(key=key,delta_sum=delta,recorded_cumulative=c['filled'],original_qty=o['qty'],
                    final_record=c,receipts=[{k:r[k] for k in ['sequence','qty','cumulative_qty','leaves_qty','status']} for r in receipts[key]][:12]))
            if bool(receipts[key])!=(c['filled']>0):receipt_presence.append(key)
        unresolved=[]
        for key,c in pending.items():
            if key in recorded:continue
            o=owners[key];rows=receipts[key]
            unresolved.append(dict(key=key,placed=o['t'],price=o['price'],qty=o['qty'],state=c['state'],filled=c['filled'],
                last_receipt=None if not rows else {k:rows[-1][k] for k in ['sequence','receive_ts','exchange_ts','status','qty','cumulative_qty','leaves_qty']},
                cancel_requests=cancels[o['n']],last_observation_age_ms=states[-1]['t']-o['t']))
        # Read only the pinned, already consumed tape on this worker. No large DB.
        pack=BASE/'staging/minimal_student_training_rules_v2_clockfix_20260910'
        manifest=json.loads((pack/'MANIFEST.json').read_text(encoding='utf-8'))
        tp=pack/'tapes/2022527.json.xz';assert sha(tp)==manifest['files']['tapes/2022527.json.xz']['sha256']
        with lzma.open(tp,'rb') as f:b=f.read(48*1024**2+1)
        assert len(b)<=48*1024**2
        tape=json.loads(b);updates=tape['updates'];end=manifest['marketWindows']['2022527'][1]
        result.update(verdict='DIAGNOSTIC_COMPLETE',source_result_sha256=sha(p),trace_sha256=sha(trace),
            source_reported_zero_fill=e['zero_fill_orders'],submitted=len(owners),
            sum_positive_deltas_zero_count=sum(delta_sums[k]==0 for k in owners),
            no_receipt_count=sum(not receipts[k] for k in owners),
            recorded_cumulative_zero_count=sum(c['filled']==0 for c in final.values()),
            reconstructed_owner_count=len(final),missing_final_owners=sorted(set(owners)-set(final)),
            pending_terminal_snapshot_overlap=sorted(overlap),zero_classification_mismatch_count=len(diffs),
            mismatches=diffs[:16],max_quantity_difference=max(gaps,default=0.),
            total_delta_qty=math.fsum(delta_sums.values()),total_recorded_cum_qty=math.fsum(c['filled'] for c in final.values()),
            receipt_presence_vs_cumulative_mismatches=receipt_presence[:20],
            native_receipt_count=count['canonical_receipt'],terminal_record_count=len(term),
            stream_counts=dict(count),max_pending_snapshot_count=max_live,peak_pending=peak_details,
            final_unresolved=unresolved,reported_unresolved=e['unresolved_owners'],
            last_plan_clock=last_plan['t'],last_own_state_clock=states[-1]['t'],
            actual_market_end_ms=end,last_update_received_ms=max(int(x[1]) for x in updates),
            last_update_source_ms=max(int(x[0]) for x in updates),
            tape_payload_keys=list(tape),market_metadata=tape['market'],
            source_updates_after_market_end=sum(int(x[1])>=end for x in updates),
            last_own_state=states[-1],source_resource_censor_events=e['resource_censor_events'])
    except Exception as ex:
        result.update(verdict='DIAGNOSTIC_ERROR',error=type(ex).__name__+': '+str(ex),traceback=traceback.format_exc(limit=8))
    result['elapsed_seconds']=time.monotonic()-start
    (out/'COMPACT.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['verdict','zero_classification_mismatch_count','max_quantity_difference','reported_unresolved','elapsed_seconds'] if k in result}),flush=True)
    if result['verdict']=='DIAGNOSTIC_ERROR':raise SystemExit(2)


if __name__=='__main__':main()
