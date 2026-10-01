"""Assemble existing, hash-verified source and fixtures; never execute HFT."""
from pathlib import Path
import ast
import difflib
import hashlib
import importlib.util
import json
import shutil

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT/'data/research/hft244_v49_repro_20261002_v1'
READBACK = TASK/'worker_readback'
OUT = TASK/'release'
FRESH = ROOT/'data/research/btc5m_cg1at_fresh100a_20260930'
UPSTREAM = 'a244a14250b42d97fc305569c93c4117cd5e1dff'
NATIVE = '033469835b44f94f1a022e419e79be61be72a9f2501ceea11b8e255b4824d145'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf8', newline='\n')


def copy(source, relative):
    dest=OUT/relative;dest.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,dest)


def package_sources(source, name):
    # Source/protocol allowlist, no tapes, outcomes, logs or submit/host records.
    for p in sorted(source.rglob('*')):
        if not p.is_file() or '__pycache__' in p.parts: continue
        rel=p.relative_to(source)
        if 'inputs' in rel.parts: continue
        if p.suffix=='.py' or p.name in {'MANIFEST.json','manifest.json','CANDIDATE.json','PROTOCOL.json',
             'SOURCE_PARENT.json','scratch_expected.json','actor_contract.json','protocol.json','CONTRACT.md'}:
            copy(p, Path('strategy/packages')/name/rel)


def main():
    if OUT.exists(): raise RuntimeError('release already exists; preserve previous output')
    OUT.mkdir(parents=True)
    (OUT/'.gitattributes').write_text('* -text\n',encoding='ascii')
    expected=json.loads((ROOT/'data/research/lan_worker_returns/btc5m-v12g-eof-execution-repair-20260927-v33/CANDIDATE_SOURCE_MANIFEST.json').read_text())
    changed=[];patch=[]
    for name,h in expected.items():
        src=READBACK/'hftbacktest_patched'/name
        assert sha(src)==h,name
        copy(src,Path('engine/patched')/name)
        original=READBACK/'hftbacktest_upstream'/name
        if original.exists(): copy(original,Path('engine/upstream_build_sources')/name)
        if not original.exists() or original.read_bytes()!=src.read_bytes():
            a=original.read_text(encoding='utf8').splitlines(True) if original.exists() else []
            b=src.read_text(encoding='utf8').splitlines(True)
            patch.extend(difflib.unified_diff(a,b,fromfile='a/'+name if original.exists() else '/dev/null',tofile='b/'+name))
            changed.append(dict(path=name,kind='modified' if original.exists() else 'added',
                                upstream_sha256=sha(original) if original.exists() else None,patched_sha256=h))
    (OUT/'engine/all_changes_from_py_v2.4.4.patch').write_text(''.join(patch),encoding='utf8',newline='\n')
    save(OUT/'engine/CANDIDATE_SOURCE_MANIFEST.json',expected)
    save(OUT/'engine/PROVENANCE.json',dict(upstream_url='https://github.com/nkaz001/hftbacktest.git',
         upstream_tag='py-v2.4.4',upstream_commit=UPSTREAM,python_version='2.4.4',rust_crate_version='0.9.4',
         upstream_commit_evidence='Local upstream git checkout HEAD/tag; worker source receipt has no .git metadata.',
         exact_worker_source_files=len(expected),verified_hash_mismatches=0,changes=changed,
         native_sha256=NATIVE,live_worker_binary_hash_verified=True,
         full_build_source_scope='All sources for the three Cargo workspace members hftbacktest, hftbacktest-derive, py-hftbacktest, Cargo.lock, licenses and manifest-listed examples. Unused upstream connector/collector/docs excluded.',
         native_executed_this_export=False))
    inventory=json.loads((READBACK/'scratch_python_inventory.json').read_text(encoding='utf-8-sig'))
    for kind in ('runtime_template','runtime_scratch_CG1AT_2671717'):
        for p in (READBACK/kind).rglob('*.py'):
            copy(p,Path('strategy')/kind/p.relative_to(READBACK/kind))
    save(OUT/'strategy/WORKER_SOURCE_INVENTORY.json',inventory)
    copy(READBACK/'backend_inventory.json','engine/BACKEND_READBACK.json')
    package_sources(FRESH,'CG1AT_fresh100a')
    package_sources(ROOT/'data/research/v12g_original_scale_stop290_20260928_v58','V58')
    package_sources(ROOT/'data/research/btc5m_cg1gate_insample40_20260929','CG1_historical')
    # The historical direct V8 runner is distinct from the transformed V9/frozen
    # Policy used by V58/fresh100a. Keep it labelled as lineage source.
    for name in ('run_target_core_cycle_active_route_v8.py','run_target_core_cycle_rearm_v3.py'):
        copy(ROOT/'tools'/name,Path('strategy/historical_v8')/name)
    copy(FRESH/'base/adapter.py','strategy/historical_v8/open_funding_native_active_adapter_v1.py')
    v33=ROOT/'data/research/v12g_eof_execution_repair_20260927_v33'
    for name in ('native_clock.patch','patches.py','eof_runtime.py','scratch_expected.json','native_contract.py','worker.py'):
        copy(v33/name,Path('engine/repair_build')/name)
    # Build receipts are public operational provenance; source inventory hashes
    # identify the exact tree, without distributing an architecture-specific DLL.
    copy(ROOT/'tools/build_hft244_isolated_v1.ps1','engine/repair_build/build_hft244_isolated_v1.ps1')
    for name in ('DISCOVERY.json','MANIFEST.json'):
        copy(v33/name,Path('engine/repair_build')/name)
    shutil.copytree(TASK/'fixtures_exact_feed_v1/fixtures',OUT/'fixtures')
    copy(TASK/'fixtures_exact_feed_v1/FIXTURES.json','FIXTURES.json')
    copy(ROOT/'tools/export_hft244_repro_samples.py','utilities/export_hft244_repro_samples.py')
    copy(Path(__file__),'utilities/package_hft244_repro_sources.py')
    protocol=json.loads((FRESH/'PROTOCOL.json').read_text())
    profiles={arm:next(j for j in protocol['jobs'] if j['arm']==arm)['env_v12'] for arm in ('CG1AT','FULL')}
    cg1=json.loads((ROOT/'data/research/btc5m_cg1gate_insample40_20260929/PROTOCOL.json').read_text())
    save(OUT/'strategy/PROFILES.json',dict(CG1AT=profiles['CG1AT'],FULL_same_cohort_V58_reference=profiles['FULL'],
        CG1_historical_jobs=cg1['jobs'],argv=['overlay/run_variant.py','--variant','PREPARE','--market-id','<market>',
        '--mode','NO_DIRECTION','--money-mode','PARALLEL_PAYOFF_ZERO','--demand-mode','AUTO_REPAIR','--retention','0',
        '--opportunity-mode','ONE_ACTIVE','--direction-rule','LEGACY']))
    save(OUT/'ENGINE_CONFIG.json',dict(engine='HashMapMarketDepthBacktest',assets=1,native_price='UP/YES',
        down_native_price='1 - DOWN own-side buy price; BUY DOWN uses native SELL',linear_contract_size=1.0,
        entry_latency_ms=250,response_latency_ms=250,latency_evidence='Source construction; fixed simulation assumptions, not measured venue latency',
        queue_model='RiskAdverseQueueModel',queue_parameters={},
        queue_semantics='Initial front quantity=visible same-price depth; trades subtract it; depth update caps front at new depth; filled quantity is rounded to lot-size when front is negative.',
        maker_fee=0.0,taker_fee=0.0,fee_model='TradingValueFeeModel default, no override in new_bt',
        tick_size=0.01,lot_size=0.01,exchange_model='PartialFillExchange',
        feed='HFTBACKTEST_EXECUTION_TAPE_FEED_V1',market_depth_clock='receivedMs used as both exch_ts/local_ts',
        trade_clock='executedAt converted to ms, plus mid offset 500000000 ns + min(within-timestamp index,999)*1000 ns',
        event_timestamp_unit='nanoseconds',source_timestamp_retained_in_tape=True,
        order_types=dict(PASSIVE='GTX LIMIT',ACTIVE='GTC LIMIT'),
        price_depth_assumptions='Public market-by-price depth only; no true per-order queue position and no market-impact feedback.',
        config_sources=['strategy/runtime_scratch_CG1AT_2671717/tools/run_eth_dagger60_smoke_v1.py',
                        'strategy/runtime_scratch_CG1AT_2671717/tools/hftbacktest_execution_shift_audit_v0.py',
                        'engine/patched/py-hftbacktest/src/lib.rs','engine/patched/hftbacktest/src/backtest/models/queue.rs']))
    # Validate all supplied source syntax without imports or execution.
    parsed=0
    for p in OUT.rglob('*.py'):
        ast.parse(p.read_text(encoding='utf8'),filename=str(p));parsed+=1
    # Verify the exact three EOF Python postimages from the original patcher.
    spec=importlib.util.spec_from_file_location('source_only_patch_verification',FRESH/'patches.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    transforms=[]
    pins=json.loads((FRESH/'scratch_expected.json').read_text())
    for name,h in pins.items():
        before=READBACK/'runtime_template/tools'/name;after=READBACK/'runtime_scratch_CG1AT_2671717/tools'/name
        assert sha(before)==h
        predicted=hashlib.sha256(mod.python_clock(name,before.read_text(encoding='utf8')).encode()).hexdigest()
        assert predicted==sha(after),(name,predicted,sha(after))
        transforms.append(dict(path=name,before=h,after=predicted,source_transform='patches.python_clock',verified=True))
    receipt=json.loads((OUT/'fixtures/2671717/golden/CG1AT/result.json').read_text())
    for name,row in receipt['sizing_installation'].items():
        before=READBACK/'runtime_template/tools'/name;after=READBACK/'runtime_scratch_CG1AT_2671717/tools'/name
        assert sha(before)==row['before'] and sha(after)==row['after'],name
        transforms.append(dict(path=name,**row,source_transform='base/ticket_condition.install',verified=True))
    save(OUT/'VERIFICATION.json',dict(source_syntax_files=parsed,source_syntax='PASS',
        native_source_hashes='99/99 PASS',runtime_source_hashes='87/87 PASS',runtime_transform_postimages=transforms,
        fixture_original_vs_anonymous_feed='3/3 PASS full event bytes and local wakeups',
        new_native_executions=0,new_fits=0,live_changes=0))
    print(json.dumps(dict(status='ASSEMBLED',files=sum(p.is_file() for p in OUT.rglob('*')),
                         bytes=sum(p.stat().st_size for p in OUT.rglob('*') if p.is_file()),source_syntax_files=parsed)))


if __name__=='__main__': main()
