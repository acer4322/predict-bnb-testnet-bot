"""Bounded, read-only strategy/source audit. No policy imports or HFT execution.
Output is new evidence; historical artifacts are never overwritten.
"""
from pathlib import Path
from collections import Counter
import ast
import datetime
import hashlib
import json
import statistics

BASE = Path('data/research/r4_v0/p0_provenance_v1')
CODE = [
 'tools/run_eth_role_separated_minimal_pair_safety_smoke.py',
 'tools/run_eth_role_separated_multislot_v3_smoke.py',
 'tools/run_eth_target_grounded_distinct_multislot_v2_smoke.py',
 'tools/run_eth_dagger60_smoke_v1.py',
 'tools/hft244_pair_paid_probe_v1.py',
 'tools/hft244_pair_confirmed_handoff_v1.py',
 'tools/hft244_pair_active_pool_v1.py',
 'tools/pair_core_full_horizon_v1.py',
 'tools/hftbacktest_execution_shift_audit_v0.py',
 'tools/hftbacktest_execution_tape_feed_v1.py',
 'tools/hft244_research_owner_accounting_v1.py',
 'tools/hft244_receipt_adapter_v1.py',
 'tools/hft244_minimal_pair_accounting_v1.py',
 'src/predict_bot/__init__.py',
]
DOCS = [
 'PAIR_CORE_TARGET_POST180_CADENCE_RETURN_V1_20260910.md',
 'PAIR_CORE_FULL_HORIZON3_RETURN_20260910.md',
 'PAIR_CORE_FULL_HORIZON3_PREREG_20260910.md',
 'PAIR_CORE_180S_BOUNDARY_DIAGNOSIS_20260910.md',
 'PAIR_CORE_CROSSASSET10_RETURN_V1_20260910.md',
 'PAIR_CORE_CROSSASSET10_TARGET_ALIGNMENT_RETURN_V1_20260910.md',
 'PAIR_CORE_CONFIRMED_HANDOFF_V2_RETURN_20260910.md',
 'PAIR_CORE_ACTIVE_SEPARATE_POOL_RETURN_20260910.md',
 'PAIR_CORE_POST_PAID_FIRST_DIVERGENCE_RETURN_V2_20260910.md',
 'PAIR_CORE_DECISION_LINEAGE_RETURN_20260910.md',
 'PAIR_CORE_TARGET_ALIGNMENT_MILESTONE_CONTRACT_V1_20260910.md',
 'PAIR_CORE_USER_MVPS_10_TO_100_GATE_20260910.md',
 'TARGET_CROSS_TIMEFRAME_SYSTEM_ARCHITECTURE_SYNTHESIS_V21_20260903.json',
 'MODULAR_CONTROLLER_RESEARCH_CONTRACT_V3_20260905.md',
]
FUNCTIONS = {'_role_decision','_direction','_pair_ok','_open_one_option',
 '_candidate_from_levels','_live_price_levels','_submit_role','_reanchor_stale',
 '_risk_contract_if_needed','_core_for_side','_weak_side','_refresh_slots',
 'candidate','_probe_select','_probe_submit','_probe_prefix','ready_reason',
 'cancel_expired','submit','new_bt','native_order','submit_native','run_v3',
 'install','strict_advance_to','physical_process','build_archive_events','__init__','quotes'}
NATIVE = '7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'


def bounded(path, cap=1024*1024):
    p=Path(path)
    if not p.is_file() or p.stat().st_size>cap:
        raise ValueError('missing or oversized audit input: '+str(p))
    b=p.read_bytes()
    return b, {'path':p.as_posix(),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}


def main():
    out=BASE/'PAIR_CORE_TARGET_RULE_AUDIT_EVIDENCE_V1_20260910.json'
    if out.exists():raise FileExistsError(str(out))
    code=[];docs=[]
    for name in CODE:
        b,meta=bounded(name,200000);text=b.decode('utf-8-sig');lines=text.splitlines()
        tree=ast.parse(text);snippets=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.FunctionDef) and node.name in FUNCTIONS:
                snippets.append({'function':node.name,'firstLine':node.lineno,'lastLine':node.end_lineno,
                                 'source':'\n'.join(lines[node.lineno-1:node.end_lineno])})
        meta.update(classes=[{'name':n.name,'bases':[ast.unparse(v) for v in n.bases]}
                              for n in ast.walk(tree) if isinstance(n,ast.ClassDef)],functions=snippets)
        code.append(meta)
    for name in DOCS:
        _,meta=bounded(BASE/name,100000);docs.append(meta)
    refs=[]
    for name in [
        'data/research/lan_worker_returns/hft244-pair-crossasset10-btc-20260910-v1/COMPACT.json',
        'data/research/lan_worker_returns/pair-core-full-horizon3-20260910-v1/COMPACT.json']:
        raw,meta=bounded(name);d=json.loads(raw);checks=[]
        for mod,wanted in d['baseSourceHashes'].items():
            p=Path(*mod.split('.')).with_suffix('.py')
            if not p.is_file():p=Path(*mod.split('.'))/'__init__.py'
            _,actual=bounded(p,200000)
            checks.append({'module':mod,'localPath':p.as_posix(),'recordedFrozenSha256':wanted,
                           'currentSourceSha256':actual['sha256'],'matches':actual['sha256']==wanted})
        meta.update(recordedNativeSha256=d['nativeSha256'],recordedVerdict=d['verdict'],
                    currentVsFrozenSources=checks)
        if d['nativeSha256']!=NATIVE:raise ValueError('unexpected recorded backend')
        refs.append(meta)
    p=BASE/'PAIR_CORE_TARGET_POST180_EVENTS_COMPACT_V1_20260910.jsonl'
    if p.stat().st_size>20*1024**2:raise ValueError('event extraction size cap')
    digest=hashlib.sha256();seen=set();legs=Counter();small=Counter();orders={};witness={}
    with p.open('rb') as f:
        for raw in f:
            digest.update(raw);x=json.loads(raw)
            if x['leg'] in seen:raise ValueError('duplicate target leg')
            seen.add(x['leg']);a=x['asset'];legs[a]+=1
            key=(a,x['marketId'],x['role'],x['side'],x['quote'],x['order'])
            o=orders.setdefault(key,{'cash':0.,'qty':0.});o['cash']+=x['p']*x['q'];o['qty']+=x['q']
            if x['p']<1/12:
                small[a]+=1;witness.setdefault(a,{k:x[k] for k in ('marketId','role','side','p','q','t')})
    if digest.hexdigest()!='a105a6fa22274f24132c7fa2ef8552fdd035694d0ce7b6cedafea0e61d2d4090':
        raise ValueError('event source changed')
    target={}
    for a in ('BTC','ETH'):
        keys=[k for k in orders if k[0]==a];markets=sorted({k[1] for k in keys})
        taker=Counter(k[1] for k in keys if k[2]=='TAKER');values=[orders[k]['cash'] for k in keys]
        target[a]={'markets':len(markets),'fillLegs':legs[a],'filledOrderIdentities':len(keys),
                   'marketsMultipleTakerOrderIdentities':sum(taker[m]>=2 for m in markets),
                   'takerFilledOrderMedian':statistics.median(taker[m] for m in markets),
                   'takerFilledOrderRange':[min(taker[m] for m in markets),max(taker[m] for m in markets)],
                   'lowPriceFillLegsBelow1over12':small[a],'lowPriceWitness':witness.get(a),
                   'filledOrderCashRange':[min(values),max(values)],'filledOrderCashMedian':statistics.median(values)}
    evidence={'version':'PAIR_CORE_TARGET_RULE_AUDIT_EVIDENCE_V1','hostTimestamp':datetime.datetime.now().isoformat(),
      'mode':'READ_ONLY_STATIC_CODE_AND_EXISTING_ARCHIVE_AUDIT','newHFT':0,'newTraining':0,'policyChanged':False,
      'scope':'A/C/T/X restricted Pair-Core; F full-window passive only; not a live8781 audit',
      'code':code,'documents':docs,'executedReferences':refs,
      'targetSource':{'path':p.as_posix(),'bytes':p.stat().st_size,'sha256':digest.hexdigest(),'rows':len(seen)},
      'targetObserved':target,
      'workerCurrentVerification':{'attempt':'SSH exact binary/package metadata only','result':'SSH_TIMEOUT',
          'claim':'recorded successful jobs are pinned to V4; this audit did not independently recheck currently installed worker binary'},
      'interpretationLimits':['Target private placement/cancel/urgency/risk rules are not known from fills.',
          'Target filled-order counts are not submissions or explicit repair counts.',
          'Absolute order sizes are not portable policy constants.',
          'Source difference is not proof previously frozen successful jobs loaded changed code.',
          'A source hash or metadata gate is not runtime semantic validation.']}
    out.write_text(json.dumps(evidence,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'output':out.as_posix(),'bytes':out.stat().st_size,'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),
       'sourceFiles':len(code),'documents':len(docs),'targetObserved':target,
       'sourceDrift':[c for c in refs[0]['currentVsFrozenSources'] if not c['matches']],
       'newHFT':0,'newTraining':0},ensure_ascii=False))


if __name__=='__main__':main()
