"""Second-worker-only bounded dataset build from already accepted source/traces.
No DB, raw tapes, HFT engine, model fit, guessed expert or future-informed policy.
"""
import bisect
from collections import Counter, defaultdict
from copy import deepcopy
import gzip
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import sys
import time
import traceback

BUNDLE=Path(__file__).resolve().parent
MIDS=[2022527,2022538,2022602]
SPLIT={2022527:'TRAIN',2022538:'TRAIN',2022602:'PIPELINE_CHECK'}
FEATURES=['predictUpMid','spotMinusStrikeBps','chainlinkMinusStrikeBps','spotMinusChainlinkBps',
          'spotReturn1sBps','spotReturn3sBps','futuresReturn1sBps','futuresReturn3sBps',
          'perpSpotBasisBps','spotQueueImbalance','futuresQueueImbalance',
          'spotTakerImbalance1s','futuresTakerImbalance1s']
MARKS=[f'{r}_{s}_{q}' for r in ('MAKER','TAKER') for s in ('UP','DOWN') for q in ('BID','ASK')]


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(262144),b''):h.update(block)
    return h.hexdigest()


def load_source(p):
    with gzip.open(p,'rb') as f:raw=f.read(8*1024**2+1)
    assert len(raw)<=8*1024**2
    return json.loads(raw)


def read_trace(p):
    total=0
    with gzip.open(p,'rb') as f:
        for line in f:
            total+=len(line)
            assert total<=32*1024**2 and len(line)<512*1024,'source stream bound'
            yield json.loads(line)


class Writer:
    def __init__(self,out,mid,lane):
        self.rel=f'data/{SPLIT[mid]}/{mid}_{lane}.jsonl.gz'
        p=out/self.rel;p.parent.mkdir(parents=True,exist_ok=True)
        self.path=p;self.raw=p.open('wb');self.gz=gzip.GzipFile(filename='',mode='wb',fileobj=self.raw,mtime=0)
        self.rows=0;self.decoded=0;self.h=hashlib.sha256();self.mid=mid;self.lane=lane
    def add(self,row):
        b=(json.dumps(row,separators=(',',':'),ensure_ascii=False,allow_nan=False)+'\n').encode('utf-8')
        self.decoded+=len(b);assert self.decoded<=32*1024**2,'output stream bound'
        self.h.update(b);self.gz.write(b);self.rows+=1
    def close(self):
        self.gz.close();self.raw.close()
        return dict(path=self.rel,market_id=self.mid,partition=SPLIT[self.mid],lane=self.lane,
                    rows=self.rows,bytes=self.path.stat().st_size,decoded_bytes=self.decoded,
                    sha256=sha(self.path),content_sha256=self.h.hexdigest())


class Index:
    def __init__(self,rows,clock):
        self.rows=sorted((r for r in rows if clock(r) is not None),key=clock)
        self.times=[clock(r) for r in self.rows]
    def before(self,t):
        i=bisect.bisect_left(self.times,t)-1
        return (None,None) if i<0 else (self.rows[i],self.times[i])


def book_clock(b):
    clocks=[b.get('source_ms'),b.get('received_ms')]
    return max(int(x) for x in clocks) if all(x is not None and int(x)>0 for x in clocks) else None


class Features:
    def __init__(self,source):
        self.market=source['market'];self.mid=self.market['market_id']
        self.pub=Index(source['public'],lambda p:p['available_ms'])
        self.book=Index(source['books'],book_clock)
        self.joins=Counter();self.missing=Counter()
        for p in source['public']:
            if p['available_ms'] is not None:
                assert p['available_ms']>=max(p['sampled_at_ms'],(int(p['timestamp_ns'])+999999)//1000000,p['archived_at_ms'])
    def make(self,t,own=None,fixture=None):
        lo,hi=self.market['window_start_ms'],self.market['window_end_ms']
        p,pt=self.pub.before(t);b,bt=self.book.before(t)
        assert pt is None or pt<t
        assert bt is None or bt<t
        x={'seconds_left':(hi-t)/1000.,'phase':(t-lo)/(hi-lo),
           'public_age_ms':None if pt is None else t-pt,
           'book_age_ms':None if bt is None else t-bt}
        for k in FEATURES:x['public_'+k]=p['features'].get(k) if p is not None else None
        names=['up_bid','up_ask','down_bid','down_ask','up_bid_qty','up_ask_qty',
               'down_bid_qty','down_ask_qty','up_top5_bid_qty','up_top5_ask_qty']
        x.update({k:None for k in names})
        if b is not None:
            bid=b['best_bid'];ask=b['best_ask']
            bids={float(k):float(v) for k,v in b['bids']};asks={float(k):float(v) for k,v in b['asks']}
            x.update(up_bid=bid,up_ask=ask,down_bid=None if ask is None else 1.-ask,
                     down_ask=None if bid is None else 1.-bid,
                     up_bid_qty=bids.get(bid),up_ask_qty=asks.get(ask),
                     down_bid_qty=asks.get(ask),down_ask_qty=bids.get(bid),
                     up_top5_bid_qty=sum(bids.values()),up_top5_ask_qty=sum(asks.values()))
        if own is not None:
            assert fixture is not None
            u,d=float(own['inv']['UP']),float(own['inv']['DOWN']);cost=float(own['cost'])
            x.update(own_up_qty=u,own_down_qty=d,own_cost=cost,own_net_qty=u-d,
                     own_floor=min(u,d)-cost,own_upside=max(u,d)-cost,
                     own_pending_orders=len(own['pending']),
                     declared_case_qty=fixture['requestedCase'],declared_capital=100.,
                     legacy_new_order_window_open=float(hi-t>180000))
            for pid,side in [('1','UP'),('2','DOWN')]:
                pref='own_'+side.lower()+'_';g=own['grants'][pid]
                pending=[o for o in own['pending'] if o['side']==side]
                x[pref+'pending_leaves']=sum(max(0.,o['requestedQty']-o['confirmedQty']) for o in pending)
                x[pref+'cancel_pending_orders']=sum(bool(o['cancelRequested']) for o in pending)
                x[pref+'unpaired_qty']=sum(float(v[0]) for v in own['unpaired'][side])
                x[pref+'reserved_cash']=float(g['reserved_cash'])
                x[pref+'reserved_qty']=float(g['reserved_qty'])
                x[pref+'available_cash']=50.-float(g['spent'])-float(g['reserved_cash'])
                x[pref+'filled_qty']=float(g['add_filled'])
                x[pref+'cash_fraction_used']=(float(g['spent'])+float(g['reserved_cash']))/50.
            x['own_qty_to_best_up_bid_depth']=None if x['up_bid_qty'] is None or x['up_bid_qty']<=0 else fixture['requestedCase']/x['up_bid_qty']
            x['own_qty_to_best_down_bid_depth']=None if x['down_bid_qty'] is None or x['down_bid_qty']<=0 else fixture['requestedCase']/x['down_bid_qty']
        for k,v in x.items():
            assert v is None or (isinstance(v,(float,int)) and not isinstance(v,bool) and math.isfinite(v)),k
        meta=dict(public_id=None if p is None else p['id'],public_available_ms=pt,
                  book_available_ms=bt,decision_clock_ms=t,
                  joins_valid=(p is not None and b is not None),source_clocks_verified=True,
                  collector_end_to_end_verified=False)
        return x,meta


def target_groups(actions):
    groups=defaultdict(list)
    for a in actions:groups[int(a['event_ms'])].append(a)
    return sorted(groups.items())


def blank_marks():return {k:dict(legs=0,filled_qty=0.,cash=0.,fill_vwap=None) for k in MARKS}


def mark_labels(actions):
    marks=blank_marks()
    for a in actions:
        k=f"{a['role']}_{a['side']}_{a['quote_type']}";assert k in marks
        m=marks[k];m['legs']+=1;m['filled_qty']+=a['shares'];m['cash']+=a['shares']*a['price']
    for m in marks.values():
        if m['filled_qty']>0:m['fill_vwap']=m['cash']/m['filled_qty']
    return marks


def core_tests():
    checks=[]
    def check(name,cond):assert cond,name;checks.append(name)
    idx=Index([dict(t=10,v='past'),dict(t=20,v='equal'),dict(t=30,v='future')],lambda r:r['t'])
    check('strict_past_excludes_equal',idx.before(20)[0]['v']=='past')
    check('no_prior_is_missing_not_backfilled',idx.before(10)==(None,None))
    same=[dict(event_ms=10,role='MAKER',side='UP',quote_type='BID',shares=.5,price=.4),
          dict(event_ms=10,role='MAKER',side='DOWN',quote_type='BID',shares=2.,price=.5)]
    check('simultaneous_target_events_stay_one_batch',len(target_groups(same))==1)
    labs=mark_labels(same)
    check('partial_fill_below18_preserved',labs['MAKER_UP_BID']['filled_qty']==.5)
    same.append(dict(event_ms=10,role='TAKER',side='UP',quote_type='BID',shares=2.,price=.5))
    check('active_mark_not_rewritten_as_hold',mark_labels(same)['TAKER_UP_BID']['legs']==1)
    check('market_disjoint_chronological_split',set(m for m in MIDS if SPLIT[m]=='TRAIN').isdisjoint(m for m in MIDS if SPLIT[m]=='PIPELINE_CHECK'))
    return checks


def parse_own(path,accept,summary):
    events=Counter();decisions=[];orders={};receipts=[];seen=set();last_seq=0;last_t=None
    actions=[];reject=Counter();reject_detail=Counter();prev_decision_seq=0
    total_inv={'UP':0.,'DOWN':0.};total_cost=0.;last_receipt=None
    for e in read_trace(path):
        seq=e['sequence'];assert seq==last_seq+1;last_seq=seq;events[e['event']]+=1
        if e.get('t') is not None:
            assert last_t is None or e['t']>=last_t
            last_t=e['t']
        if e['event']=='EXACT_SUBMIT_RESULT':
            assert e['ok'] and e['requestedQty']==e['submittedQty']==accept['requestedCase']
            assert e['key'] not in orders
            a={k:e[k] for k in ['key','side','role','price','requestedQty','submittedQty','intentProvenance','ok','t']}
            a['log_sequence']=seq;orders[e['key']]=a;actions.append(a)
        elif e['event']=='QUANTITY_REJECT':
            reject[e['reason']]+=1
            reject_detail[e['reason']+'|'+str(e.get('side','UNKNOWN'))]+=1
        elif e['event']=='OWN_RECEIPT_STATE':
            last_receipt=e
            for r in e['receipts']:
                assert r['sequence'] not in seen;seen.add(r['sequence'])
                assert r['key'] in orders and r['receive_ts']<=int(e['t'])*1000000
                side=orders[r['key']]['side'];total_inv[side]+=r['qty'];total_cost+=r['qty']*r['contractPrice']
                rr=dict(r,log_sequence=seq,observed_at=e['t'],contract_side=side);receipts.append(rr)
            for side in ('UP','DOWN'):assert abs(e['after']['inv'][side]-total_inv[side])<1e-7
            assert abs(e['after']['cost']-total_cost)<1e-7
        elif e['event']=='OWN_DECISION':
            assert all(a['t']==e['t'] for a in actions)
            for side in ('UP','DOWN'):assert abs(e['before']['inv'][side]-total_inv[side])<1e-7
            assert abs(e['before']['cost']-total_cost)<1e-7
            decisions.append(dict(event=e,actions=actions,rejects=dict(reject),
                rejection_candidates_by_reason_side=dict(reject_detail),start_seq=prev_decision_seq+1))
            actions=[];reject.clear();reject_detail.clear();prev_decision_seq=seq
        else:raise AssertionError('unexpected trace event '+e['event'])
    assert not actions and not reject,'unassigned trailing action/rejection'
    assert last_receipt is not None
    assert dict(events)==accept['traceEvents']
    assert len(orders)==accept['submits'] and len(receipts)==accept['nativeReceipts']
    for side in ('UP','DOWN'):assert abs(total_inv[side]-accept[side])<1e-7
    assert abs(total_cost-accept['cost'])<1e-7
    final_orders={o['key']:o for o in summary['orders']}
    assert set(final_orders)==set(orders)
    by_order=defaultdict(list)
    for r in receipts:by_order[r['key']].append(r)
    for key,o in final_orders.items():
        assert o['ledgerState']=='TERMINAL' and o['requestedQty']==orders[key]['requestedQty']
        assert abs(sum(r['qty'] for r in by_order[key])-o['filledQty'])<1e-7
    return decisions,orders,receipts,last_receipt,final_orders,events


def dataset_build(out,manifest):
    accept=json.loads((BUNDLE/'inputs/ACCEPTANCE.json').read_text(encoding='utf-8'))
    prev=json.loads((BUNDLE/'inputs/SOURCE_MANIFEST.json').read_text(encoding='utf-8'))
    summaries={}
    for name in ['NATIVE_V1_COMPACT.json','NATIVE_RETRY_COMPACT.json']:
        data=json.loads((BUNDLE/'inputs'/name).read_text(encoding='utf-8'))
        for r in data['rows']:summaries[r['marketId']]=r
    assert list(r['marketId'] for r in accept['rows'])==MIDS
    files=[];stats=[];checks=core_tests();xschemas={};examples={};common_lineage={}
    for ar in accept['rows']:
        mid=ar['marketId'];t0=time.monotonic()
        source_path=BUNDLE/f'inputs/input_{mid}.json.gz'
        assert sha(source_path)==prev['files'][f'input_{mid}.json.gz']['sha256']
        trace=BUNDLE/f'inputs/OWN_TRAJECTORY_{mid}.jsonl.gz'
        assert sha(trace)==ar['traceSha256']
        src=load_source(source_path);assert src['market']['market_id']==mid
        feat=Features(src);decisions,orders,receipts,final,terminal,event_counts=parse_own(trace,ar,summaries[mid])
        receipt_sequences=[r['log_sequence'] for r in receipts]
        public_join=Counter();book_join=Counter();feature_missing=Counter()
        loss_totals=Counter();audit_masks=Counter();source_legs=set();mark_counts=Counter()
        lineage=dict(source_sha256=sha(source_path),own_trace_sha256=sha(trace),
                     native_sha256=accept['nativeSha256'],era='BTC5M_20260907_CONSUMED')
        common_lineage[str(mid)]=lineage
        w=Writer(out,mid,'own_transitions')
        for i,item in enumerate(decisions):
            e=item['event'];nex=decisions[i+1]['event'] if i+1<len(decisions) else None
            nstate=nex['before'] if nex is not None else final['after']
            nseq=nex['sequence'] if nex is not None else final['sequence']
            nt=nex['t'] if nex is not None else final['t']
            assert nseq>e['sequence'] and nt>=e['t']
            lo=bisect.bisect_right(receipt_sequences,e['sequence']);hi=bisect.bisect_left(receipt_sequences,nseq)
            # A final drain receipt *is* the last next-state event, so include it.
            if nex is None:hi=bisect.bisect_right(receipt_sequences,nseq)
            rr=receipts[lo:hi]
            du={s:nstate['inv'][s]-e['after']['inv'][s] for s in ('UP','DOWN')}
            dc=nstate['cost']-e['after']['cost']
            for s in ('UP','DOWN'):assert abs(du[s]-sum(r['qty'] for r in rr if r['contract_side']==s))<1e-7
            assert abs(dc-sum(r['qty']*r['contractPrice'] for r in rr))<1e-7
            x,j=feat.make(e['t'],e['before'],ar)
            assert all(not k.startswith('target_') for k in x)
            xschema=sorted(x);assert 'own' not in xschemas or xschema==xschemas['own'];xschemas['own']=xschema
            for k,v in x.items():
                if v is None:feature_missing[k]+=1
            public_join['own_present' if j['public_available_ms'] is not None else 'own_missing']+=1
            book_join['own_present' if j['book_available_ms'] is not None else 'own_missing']+=1
            masks=dict(own_inventory_delta=True,expert_policy=False,target_original_qty=False,
                       target_hold=False,cancel_action=False,complete_action_history=False)
            row=dict(row_id=f'OUR:{mid}:{e["sequence"]}',market_id=mid,partition=SPLIT[mid],
                lane='own_transitions',t=e['t'],x=x,x_missing={k:v is None for k,v in x.items()},
                feature_provenance=j,own_before=e['before'],own_after_decision=e['after'],
                recorded_action=dict(new_submits=item['actions'],rejection_counts=item['rejects'],
                    rejection_candidates_by_reason_side=item['rejection_candidates_by_reason_side'],
                    role=e['role'],blocked_before_role=e['blockedBeforeRole'],
                    explicit_cancel_action=None,action_is_expert=False),
                labels=dict(next_t=nt,next_own_state=nstate,delta_inventory=du,delta_cost=dc,
                    native_receipts=rr,terminal_transition=nex is None),loss_masks=masks,
                source=dict(**lineage,decision_log_sequence=e['sequence'],next_log_sequence=nseq),
                scale_provenance='OUR_FIXED_TRANSPORT_FIXTURE_NOT_TARGET_SAME_SCALE',
                teacher_action=None,teacher_original_qty=None)
            w.add(row);loss_totals['own_inventory_delta']+=1
            if 'own' not in examples:examples['own']=row
        files.append(w.close())
        w=Writer(out,mid,'target_batches')
        batches=target_groups(src['targetActions']);grouped_legs=0;all_delays=[]
        for t,aa in batches:
            assert src['market']['window_start_ms']<=t<src['market']['window_end_ms']
            x,j=feat.make(t);xschema=sorted(x)
            assert 'target' not in xschemas or xschema==xschemas['target'];xschemas['target']=xschema
            marks=mark_labels(aa);grouped_legs+=len(aa)
            for a in aa:
                assert a['source_leg_id'] not in source_legs;source_legs.add(a['source_leg_id'])
                all_delays.append(a['observed_at_ms']-a['event_ms'])
                mark_counts[f"{a['role']}_{a['side']}_{a['quote_type']}"]+=1
            eligible=j['joins_valid']
            masks=dict(observed_fill_marks=eligible,observed_fill_price=eligible,
                       original_order_qty=False,placement_action=False,placement_timing=False,
                       target_hold=False,cancel_action=False,our_expert_action=False)
            row=dict(row_id=f'TARGET_OBSERVED:{mid}:{t}',market_id=mid,partition=SPLIT[mid],
                lane='target_batches',t=t,x=x,x_missing={k:v is None for k,v in x.items()},
                feature_provenance=j,labels=dict(marks=marks,observed_fill_legs=len(aa),
                    original_requested_qty=None,original_placement_time=None,
                    original_cancel_intent=None,hold_action=None),loss_masks=masks,
                batch_same_timestamp=True,event_conditioned=True,
                unsupported_by_current_student=sorted({a['role'] for a in aa if a['role']=='TAKER'}),
                audit=dict(source_leg_ids=[a['source_leg_id'] for a in aa],
                    observed_at_ms=[a['observed_at_ms'] for a in aa],
                    target_order_ids=sorted(set(a['order_hash'] for a in aa)),
                    target_private_state=None),source=lineage,
                warning='OBSERVED_FILL_MARKS_NOT_ORIGINAL_ORDER_OR_PLACEMENT_DECISIONS')
            w.add(row);loss_totals['observed_fill_marks']+=int(eligible)
            public_join['target_present' if j['public_available_ms'] is not None else 'target_missing']+=1
            book_join['target_present' if j['book_available_ms'] is not None else 'target_missing']+=1
            if 'target' not in examples:examples['target']=row
        assert grouped_legs==len(src['targetActions'])
        files.append(w.close())
        w=Writer(out,mid,'target_order_audit')
        parent_keys=set();grouped=defaultdict(lambda:dict(qty=0.,cash=0.,n=0))
        for a in src['targetActions']:
            key=(a['role'],a['side'],a['quote_type'],a['order_hash']);g=grouped[key]
            g['qty']+=a['shares'];g['cash']+=a['shares']*a['price'];g['n']+=1
        for p in src['targetParents']:
            key=(p['role'],p['side'],p['quote_type'],p['order_hash']);assert key not in parent_keys;parent_keys.add(key)
            g=grouped[key]
            assert abs(g['qty']-p['shares'])<1e-8 and abs(g['cash']-p['shares']*p['average_price'])<1e-8 and g['n']==p['fill_legs']
            row=dict(row_id=f'TARGET_ORDER:{mid}:'+':'.join(key),market_id=mid,partition=SPLIT[mid],
                lane='target_order_audit',observed_parent=p,original_requested_qty=None,
                original_remaining_qty=None,original_terminal=None,original_placement_time=None,
                exact_size_label_eligible=False,zero_fill_order_universe_known=False,
                cumulative_fills_are_original_qty=False,source=lineage)
            w.add(row)
        assert parent_keys==set(grouped);files.append(w.close())
        w=Writer(out,mid,'own_order_outcomes')
        by_order=defaultdict(list)
        for r in receipts:by_order[r['key']].append(r)
        for key,o in orders.items():
            z=terminal[key];rr=by_order[key]
            st='ZERO_FILL' if z['filledQty']==0 else ('PARTIAL_TERMINAL' if z['filledQty']<z['requestedQty']-1e-8 else 'FULL_FILL')
            audit_masks[st]+=1
            row=dict(row_id=f'OUR_ORDER:{mid}:{key}',market_id=mid,partition=SPLIT[mid],
                lane='own_order_outcomes',recorded_submit=o,
                labels=dict(final_filled_qty=z['filledQty'],fill_fraction=z['filledQty']/z['requestedQty'],
                    outcome=st,status=z['status'],ledger_state=z['ledgerState'],native_receipts=rr),
                qty_is_known_original=True,qty_provenance='OUR_PREDECLARED_FIXTURE_NOT_TEACHER',
                terminal_time=None,terminal_time_exact_known=False,
                loss_masks=dict(recorded_execution_outcome=True,expert_policy=False,keep_cancel_counterfactual=False),
                source=lineage)
            w.add(row)
        files.append(w.close())
        # Leakage perturbation: changing only future external values leaves earlier inputs untouched.
        check_t=decisions[len(decisions)//3]['event']['t']
        before=feat.make(check_t)[0];alter=deepcopy(src)
        changed=0
        for p in alter['public']:
            if p['available_ms'] is not None and p['available_ms']>=check_t:
                p['features']={k:99999. for k in FEATURES};changed+=1
        for b in alter['books']:
            if book_clock(b) is not None and book_clock(b)>=check_t:
                b['best_bid']=.01;b['best_ask']=.99;b['bids']=[[.01,99999.]];b['asks']=[[.99,99999.]];changed+=1
        assert changed>0 and Features(alter).make(check_t)[0]==before
        for a in alter['targetActions']:a['shares']=999999.;a['side']='UP'
        assert Features(alter).make(check_t)[0]==before,'Target labels leaked into features'
        stats.append(dict(market_id=mid,partition=SPLIT[mid],target_fill_legs=grouped_legs,
            target_batches=len(batches),target_orders=len(parent_keys),own_decisions=len(decisions),
            own_orders=len(orders),native_receipts=len(receipts),own_outcomes=dict(audit_masks),
            loss_rows=dict(loss_totals),public_join=dict(public_join),book_join=dict(book_join),
            own_missing_features=dict(feature_missing),target_mark_legs=dict(mark_counts),
            target_event_all_mod1000_zero=all(t%1000==0 for t,_ in batches),
            observed_event_timestamp_note='RECORDED_RESOLUTION_NOT_PRIVATE_PLACEMENT_TIME',
            target_observed_delay_ms=dict(min=min(all_delays),median=statistics.median(all_delays),max=max(all_delays)),
            repeated_rejection_candidate_records=event_counts['QUANTITY_REJECT'],
            source_hashes=lineage,elapsed_seconds=time.monotonic()-t0))
        print(json.dumps(dict(market=mid,stage='dataset_written',own=len(decisions),
                             target_batches=len(batches),orders=len(orders))),flush=True)
    checks.extend(['all_source_hashes_and_complete_retry_trace_verified','all_native_own_receipts_reconciled',
        'all_order_outcomes_including_zero_and_partial_retained','all_external_joins_strict_past',
        'future_public_book_mutation_does_not_change_earlier_x','target_label_mutation_does_not_change_x',
        'no_expert_qty_hold_cancel_labels_fabricated','no_target_private_state_in_own_features',
        'target_order_aggregates_reconcile','own_feature_schema_fixed_across_markets',
        'observed_target_feature_schema_fixed_across_markets'])
    dmeta=dict(version='MINIMAL_STUDENT_TRAINING_DATA_SMOKE3_V1',data_files=files,
        market_split={str(k):v for k,v in SPLIT.items()},feature_allowlists=xschemas,
        source_lineage=common_lineage,rows_by_market=stats,source_manifest_sha256=sha(BUNDLE/'MANIFEST.json'),
        supported_tasks=['observed_fill_marks','own_inventory_delta'],
        unsupported_tasks=['target_exact_size','placement_timing','placement_policy','HOLD','cancel_policy','OUR_expert_policy'],
        source_era='BTC5M_20260907_CONSUMED',new_era_training_rows=0,
        full_behavior_cloning_ready=False,original_target_requested_qty_coverage=0,
        own_sizing_fixture_not_same_scale_target=True,fresh_holdout=False,model_fits=0,HFT=0)
    (out/'DATASET_MANIFEST.json').write_text(json.dumps(dmeta,indent=2),encoding='utf-8')
    (out/'SAMPLE_ROWS.json').write_text(json.dumps(examples,indent=2),encoding='utf-8')
    return dmeta,checks


def train_stats_and_reader(out,meta,reader):
    stats={};counts={};checks=[]
    for task in reader.TASKS:
        acc={};n=0;train_ids=set()
        for item in reader.iter_task(out,task,'TRAIN'):
            train_ids.add(item['market_id']);n+=1
            for k,v in item['x'].items():
                s=acc.setdefault(k,dict(n=0,mean=0.,m2=0.,missing=0))
                if v is None:s['missing']+=1;continue
                s['n']+=1;delta=v-s['mean'];s['mean']+=delta/s['n'];s['m2']+=delta*(v-s['mean'])
        assert train_ids=={2022527,2022538} and n>0
        vals={k:dict(n=s['n'],missing=s['missing'],mean=s['mean'],
                     scale=math.sqrt(s['m2']/s['n']) if s['n']>1 and s['m2']>1e-20 else 1.) for k,s in acc.items()}
        stats[task]=dict(training_market_ids=sorted(train_ids),fit_partition='TRAIN',row_count=n,
                         feature_names=sorted(vals),features=vals,unknown_imputation='TRAIN_MEAN_PLUS_MISSINGNESS_MASK')
        counts[task]={}
        for split in ('TRAIN','PIPELINE_CHECK'):
            nr=0;row_ids=set()
            for item in reader.iter_task(out,task,split):
                assert item['row_id'] not in row_ids;row_ids.add(item['row_id'])
                arr=reader.vectorize(item['x'],stats[task]);assert all(math.isfinite(v) for v in arr)
                assert len(arr)==len(stats[task]['feature_names'])*2;nr+=1
            counts[task][split]=nr
        assert counts[task]['TRAIN']==n and counts[task]['PIPELINE_CHECK']>0
    for task in ['target_exact_size','OUR_expert_policy','HOLD','cancel_policy']:
        try:list(reader.iter_task(out,task,'TRAIN'))
        except ValueError:pass
        else:raise AssertionError('unknown teacher task unexpectedly enabled')
    (out/'TRAIN_ONLY_FEATURE_STATS.json').write_text(json.dumps(stats,indent=2),encoding='utf-8')
    checks.extend(['preprocessing_stats_fit_on_train_markets_only','both_loader_tasks_iterate_both_partitions',
                   'normalization_preserves_explicit_missingness','loader_blocks_unknown_expert_tasks',
                   'all_loader_feature_arrays_finite','loader_never_flattens_after_state_or_audit_ids'])
    return counts,checks


def main():
    if '--child' not in sys.argv:
        helper=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        spec=importlib.util.spec_from_file_location('bound',helper)
        bound=importlib.util.module_from_spec(spec);spec.loader.exec_module(bound)
        bound.bounded('minimal-student-data',[sys.executable,str(Path(__file__).resolve()),'--child'],180)
        return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR'),'must run through second LAN worker'
    assert int(os.environ.get('OMP_NUM_THREADS','999'))<=4
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);started=time.monotonic()
    result=dict(version='MINIMAL_STUDENT_TRAINING_DATA_SMOKE3_V1',verdict='RUNNING',
        model_fits=0,HFT=0,live_changes=0,new_markets=0,worker_hostname=os.environ.get('COMPUTERNAME'),
        max_threads=int(os.environ['OMP_NUM_THREADS']),processing_location='SECOND_LAN_WORKER')
    try:
        manifest=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8'))
        for rel,m in manifest['files'].items():
            p=(BUNDLE/rel).resolve();assert p.is_relative_to(BUNDLE)
            assert p.stat().st_size==m['bytes'] and sha(p)==m['sha256'],rel
        spec=importlib.util.spec_from_file_location('dataset_reader',BUNDLE/'minimal_student_dataset_reader_v1.py')
        reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
        meta,checks=dataset_build(out,manifest)
        counts,more=train_stats_and_reader(out,meta,reader);checks+=more
        assert len(checks)==len(set(checks))
        total=Counter()
        for r in meta['rows_by_market']:
            for k in ['target_fill_legs','target_batches','target_orders','own_decisions','own_orders','native_receipts']:
                total[k]+=r[k]
            total['zero_fill_orders']+=r['own_outcomes'].get('ZERO_FILL',0)
            total['partial_terminal_orders']+=r['own_outcomes'].get('PARTIAL_TERMINAL',0)
        assert total['own_orders']==63 and total['native_receipts']==42 and total['zero_fill_orders']==49 and total['partial_terminal_orders']==3
        assert total['own_decisions']==4458 and total['target_fill_legs']==1899 and total['target_orders']==1179
        shutil.copy2(BUNDLE/'minimal_student_dataset_reader_v1.py',out/'minimal_student_dataset_reader_v1.py')
        shutil.copy2(BUNDLE/'PREREG.md',out/'PREREG.md')
        card='''# 極簡學生小型訓練資料 V1\n\n此包是三場已消耗 BTC5M 的資料與loader驗收，不是完整Target行動老師或績效測試。\n\n## 可訓練範圍\nobserved_fill_marks：在Target有可見成交事件的條件下，預測各Maker/Taker×UP/DOWN×BID/ASK的成交標記。不是掛單動作、掛單時機、HOLD、完整意圖或精確原單尺寸。相同event_ms所有成交合成一筆，不在粗時間戳內猜先後。\nown_inventory_delta：已記錄OUR狀態与實際送單下的下一觀測持倉/成本變化輔助任務。此為單一路徑與既定後續控制器，不是反事實決策價值，也不把固定q30/55當專家。\n\n## 檔案\ndata/TRAIN與data/PIPELINE_CHECK每市場各四條lane：own_transitions、target_batches、target_order_audit、own_order_outcomes。DATASET_MANIFEST.json含檔案/內容hash、精確欄位allowlist與來源。TRAIN_ONLY_FEATURE_STATS.json只由前兩場估計，缺值以訓練均值置中並附missingness。SAMPLE_ROWS.json用於格式查看，不作dataset替代。\n\n## 不得混淆\n三場來源時期2026-09-07。前兩場TRAIN、第三場只檢查資料管線，早已看過結果，不能稱untouched test。保留Target原始觀測份額/價格，未把舊資料改成9/10的30/55模式。OUR30/55與virtual100/two50cash是傳值測試案例，不是同尺度老師。\n\n未知Target原始qty/placement/cancel/HOLD全部mask，不填18/30/55。Target私人state不接進OUR輸入。所有Target標籤只在自己的觀察lane；同場同時不等於有OUR專家答案。Target成交時間不是原始掛單時間，public/bid相對成交時間可用不代表相對原始下單也可用。沒有用此包訓練Maker placement/timing。\n\n公開資料以所有已保存時鐘的保守availability嚴格小於觀測t連接，缺值和age保留。collector端到端時鐘尚未獨立驗證。當下OWN狀態來自實際native回饋，後續state/收據只在label，不放入x。部分取消行動沒有獨立事件，不補假cancel label。\n\n保留63個OUR單、42筆收據、49零成交、3部分終止；不得只學成交成功者。最終order outcomes只用於該已記錄下單與後續維持/取消路徑，不是一般keep-versus-cancel fill probability。PAIR/4slot/<=180s禁新單仍屬舊版限制。Active observed marks保存，學生不支援的Active不重標HOLD。\n\n## 使用\npython minimal_student_dataset_reader_v1.py --dataset . --task observed_fill_marks --split TRAIN\npython minimal_student_dataset_reader_v1.py --dataset . --task own_inventory_delta --split PIPELINE_CHECK\n\n以上是loader dry-run，不訓練模型。新運算繼續優先第二台，max_threads4。下一步只能先限定一個非冒名label的小型預訓練；不因本包通過直接擴大市場或聲稱穩定正收益。真正政策的控制權/teacher/state bridge需另外驗證。\n'''
        (out/'DATA_CARD.md').write_text(card,encoding='utf-8')
        result.update(verdict='SMALL_DATA_PACKAGE_VALIDATED_FOR_SCOPED_AUXILIARY_TASKS',
            totals=dict(total),rows_by_market=meta['rows_by_market'],loader_rows=counts,
            assertions_passed=len(checks),assertions=checks,
            data_file_count=len(meta['data_files']),data_bytes=sum(x['bytes'] for x in meta['data_files']),
            full_behavior_cloning_ready=False,original_target_requested_qty_coverage=0,
            expert_action_labels_on_own_states=0,new_era_same_scale_dataset=False,
            limitations=['Event-conditioned observed-fill marks are not placement/HOLD policy demonstrations.',
                         'Native OWN scale is a fixture, not same-era Target original order quantity.',
                         'PIPELINE_CHECK has been consumed; no performance generalization claim.',
                         'No model fit; model-ready limited auxiliary data only.',
                         'Public collector end-to-end provenance and private placement clocks unverified.'])
        result['artifact_hashes']={p.relative_to(out).as_posix():sha(p) for p in out.rglob('*')
            if p.is_file() and p.name not in ('COMPACT.json','result.json') and not p.name.endswith('.log')}
    except Exception as exc:
        result.update(verdict='DATA_BUILD_ERROR_STOPPED',error=type(exc).__name__+': '+str(exc),
                      traceback=traceback.format_exc(limit=10))
    assert not any(n.startswith(('hftbacktest','torch','sklearn')) for n in sys.modules)
    result['elapsed_seconds']=time.monotonic()-started
    (out/'COMPACT.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k in ('verdict','totals','loader_rows','assertions_passed','elapsed_seconds')},ensure_ascii=False),flush=True)
    if result['verdict']=='DATA_BUILD_ERROR_STOPPED':raise SystemExit(2)


if __name__=='__main__':main()
