"""Frozen V48 dual opening / dynamic inventory integration and bounded worker cohort."""
import argparse
from copy import deepcopy
import inspect
import json
import shutil
import sys
from prepare_btc5m_transfer_structural_v1 import ROOT, R, read, sha, once, load

STEM = 'BTC5M_DYNAMIC_INVENTORY_NATIVE_V1_20260914'
PARENT = ROOT/'.lan_worker_v1/frozen_new_market_v44_20260913_v1'
ORIGINAL_PACKAGE = ROOT/'.lan_worker_v1/dynamic_inventory_v48_20260914_v1'
PACKAGE = ROOT/'.lan_worker_v1/dynamic_inventory_v48_20260914_v2' if (R/(STEM+'_BOOK_ADAPTER_REPAIR.json')).exists() else ORIGINAL_PACKAGE


def dump(tag, value):
    (R/(STEM+'_'+tag+'.json')).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n', encoding='utf-8')


def jobs():
    return read(R/(STEM+'_WAVE.json'))['jobs']


def compile_policy(package, mode='NO_DIRECTION', rule='INVENTORY', context=False):
    sys.path.insert(0, str(package))
    mod = load('dynamic_compile', package/'money_runner.py')
    code = inspect.getsource(mod.main)
    stop = "    assert socket.gethostname().upper() == 'DESKTOP-JIERAGF', 'worker only'"
    code = code[:code.index(stop)] + ('    return locals()\n' if context else '    return source\n')
    ns = dict(mod.__dict__)
    exec(compile(code, 'V48_COMPILE_WITHOUT_NATIVE', 'exec'), ns)
    old = sys.argv
    try:
        sys.argv = ['component','--mode',mode,'--direction-rule',rule,'--money-mode','PARALLEL_QUANTITY','--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE']
        sys.modules['roles_runtime'].roles.configure('KNOWN_FINAL_DIRECTION','UP')
        return ns['main']()
    finally:
        sys.argv = old


def prepare():
    assert not PACKAGE.exists() and not (R/(STEM+'_PROTOCOL.json')).exists()
    parent = read(PARENT/'manifest.json')
    assert sha(PARENT/'manifest.json') == '7085735ed3039daacb491672b047670c6e645f121124433e6e74b1769e2eb6be'
    assert all(sha(PARENT/n) == h for n,h in parent['files'].items())
    dump('PROTOCOL', dict(status='FROZEN_BEFORE_NATIVE', parent_manifest_sha256=sha(PARENT/'manifest.json'),
        hypothesis='Can a neutral Passive15 opening feed a confirmed-inventory dynamic acquisition side while repair memory and pending lifecycle stay physical? Does a one-ticket repair-only overtake grace reduce mechanical side reversals?',
        maximum_native_jobs=5, max_threads=4, markets=[1977248,2127218],
        arms=['LEGACY_ENGINEERING_PARITY_1977248','INVENTORY','REPAIR_GRACE'],
        grace='Only repair-born confirmed fills can start grace. Retain previous side while opposite lead <=15 shares; switch on independent candidate-side fill or larger lead. Ties retain previous. No duration or price tuning.',
        controls='INVENTORY and REPAIR_GRACE share dual neutral opening, per-physical-side banks, V44 theta/qref, first-OWN-held amplitude, late acquisition rule, canonical ledger and global service ceilings.',
        service_caps=dict(total_active=5, opportunity=1, coordination_nonrenewed=2, renewed=2),
        dedup=dict(V45='Completed asymmetric bootstrap / fixed direction transfer; not a dual opening dynamic actor.', V46='Deferred fixed amplitude isolation; never dispatch.', V47='Completed pure components only; no gateway or native dynamic test.',
            search='Current V45/V46/V47 handoffs, dynamic/opening/inventory/repair-reversal reports and tools inspected. New boundary is complete canonical integration plus immutable birth-source grace.'),
        limitations=['Two previously consumed diagnostic markets; not out of sample.', 'Repair birth provenance is OWN model metadata, not inferred Target intent.', 'No claim to have discovered Target direction belief.', 'Five Active ceiling is a frozen research restriction.'],
        acceptance='Execution validity requires full raw receipt conservation, terminal owners, reserved cash zero, public source availability, dynamic role and physical repair audits. Judge economic structure separately.',
        failure='Preserve partial trace and UNKNOWN and stop this frozen wave on native/accounting failure. Diagnose a pinned repair before any new experiment.',
        local_native_jobs=0, model_fits=0, parameter_sweep=0))
    PACKAGE.mkdir()
    for name in parent['files']:
        dest=PACKAGE/name;dest.parent.mkdir(parents=True, exist_ok=True);shutil.copy2(PARENT/name,dest)
    shutil.copy2(ROOT/'tools/btc5m_inventory_direction_bridge_v1.py',PACKAGE/'direction_bridge.py')
    shutil.copy2(ROOT/'tools/btc5m_dual_opening_inventory_roles_v1.py',PACKAGE/'dual_opening.py')
    path = PACKAGE/'money_runner.py';s=path.read_text(encoding='utf-8')
    s=once(s,"    ap.add_argument('--check-only', action='store_true');", "    ap.add_argument('--direction-rule', choices=('LEGACY','INVENTORY','REPAIR_GRACE'), default='LEGACY')\n    ap.add_argument('--check-only', action='store_true');")
    s=once(s,"    source=tail.instrument(source,replace)", "    source=tail.instrument(source,replace)\n    bridge=load('dynamic_direction_bridge',package/'direction_bridge.py')\n    assert args.direction_rule=='LEGACY' or args.mode=='NO_DIRECTION'\n    source=bridge.instrument(source,replace)")
    s=once(s,'namespace = dict(_TailNewStop=', 'namespace = dict(_DirectionBridge=bridge.DirectionBridge, _DIRECTION_RULE=args.direction_rule, _TailNewStop=')
    s=once(s,"        raw = json.dumps(payload", "        payload.update(producer.bridge.trace_payload())\n        raw = json.dumps(payload")
    s=once(s,"return dict(mode='FIXED_TRAIN_EVENTS', intent_mode=", "return dict(direction_rule=args.direction_rule, direction_switches=sum(r.get('previous') is not None and r['side']!=r.get('previous') for r in producer.bridge.decisions), repair_grace_frames=sum(r['guard'] is not None for r in producer.bridge.decisions), mode='FIXED_TRAIN_EVENTS', intent_mode=")
    s=once(s,"No test Target path in actor process.'", "Dynamic arms use dual neutral opening and current confirmed OWN inventory; physical repair memory. No test Target path in actor process.'")
    path.write_text(s,encoding='utf-8')
    manifest=deepcopy(parent);manifest.update(version=STEM, parent_manifest_sha256=sha(PARENT/'manifest.json'), maximum_native_jobs=5)
    manifest['files']={p.relative_to(PACKAGE).as_posix():sha(p) for p in PACKAGE.rglob('*') if p.is_file()}
    changed=[n for n,h in parent['files'].items() if manifest['files'][n]!=h]
    assert changed==['money_runner.py']
    (PACKAGE/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    wave=[]
    for mid,rule,mode in [(1977248,'LEGACY','ORACLE_DOWN')]+[(mid,rule,'NO_DIRECTION') for mid in (1977248,2127218) for rule in ('INVENTORY','REPAIR_GRACE')]:
        arm=rule.lower();wave.append(dict(job_id=f'fixed15-core-loop-{mid}-dynamic-v48-{arm.replace("_","-")}-20260914-v1',market=mid,arm=arm,mode=mode,rule=rule,cwd='.',max_threads=4,
            argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py','--market-id',str(mid),'--mode',mode,'--direction-rule',rule,'--money-mode','PARALLEL_QUANTITY','--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE']))
    dump('WAVE',dict(jobs=wave,sequential=True))
    return component()


def component():
    import ast
    from types import SimpleNamespace
    contexts=[]
    for mode,rule in [('ORACLE_UP','LEGACY'),('ORACLE_DOWN','LEGACY'),('NO_DIRECTION','LEGACY'),('NO_DIRECTION','INVENTORY'),('NO_DIRECTION','REPAIR_GRACE')]:
        ctx=compile_policy(PACKAGE,mode,rule,True);compile(ctx['source'],'V48_SYNTAX_ONLY','exec');contexts.append(ctx)
    ctx=contexts[-1];bridge=ctx['bridge'];roles=sys.modules['roles_runtime'].roles
    choices=0
    for side in ('UP','DOWN'):
        other='DOWN' if side=='UP' else 'UP'
        for gap in (0.01,7.687001502661877,15.,15.01,30.):
            inv={side:100.,other:100.+gap}
            for purpose in ('REPAIR','ADD','NEUTRAL'):
                inc=[dict(side=other,qty=gap,purpose=purpose)]
                assert bridge.choose_direction(inv,side,'INVENTORY',inc,None)[0]==other
                selected,guard,_=bridge.choose_direction(inv,side,'REPAIR_GRACE',inc,None)
                assert selected==(side if purpose=='REPAIR' and gap<=15. else other)
                if guard:
                    assert bridge.choose_direction(inv,side,'REPAIR_GRACE',[],guard)[0]==side
                    assert bridge.choose_direction(inv,side,'REPAIR_GRACE',[dict(side=other,qty=.01,purpose='ADD')],guard)[0]==other
                choices+=1
        assert bridge.choose_direction({side:100.,other:100.},side,'REPAIR_GRACE',[],other)==(side,None,'RETAIN')
    snap=ctx['gate'].snapshot
    p=SimpleNamespace(demand=ctx['demand'].SingleRepairDemand(ctx['manifest']['demand_selection'],'AUTO_REPAIR',snap),
        addition_growth=ctx['addition_growth'].AdditionGrowth(), growth_hold=ctx['growth_hold'].GrowthHold(),
        opportunity=ctx['opportunity'].ActiveOpportunity('ONE_ACTIVE',snap),
        commitment_repair=ctx['maintenance_scope'].make_probe(ctx['commitment_repair'])(snap), coordination=ctx['coordination'].CoordinationProbe(snap))
    p.commitment_repair.coordinator=p.coordination
    director=bridge.DirectionBridge('INVENTORY',snap);director.initialize(p)
    assert all(director.banks['UP'][k] is not director.banks['DOWN'][k] for k in bridge.PARTS)
    state=dict(inv=dict(UP=100.,DOWN=40.),payoff=dict(UP=20.,DOWN=-40.),pending_qty=dict(UP=0.,DOWN=10.),pending_cash=dict(UP=0.,DOWN=3.))
    roles.side='DOWN'
    with director.scope(p,'UP'):goal=ctx['demand'].old.FiniteGoal(state,15.)
    assert roles.side=='DOWN' and goal.target==65.
    state.update(inv=dict(UP=100.,DOWN=110.),pending_qty=dict(UP=15.,DOWN=10.),pending_cash=dict(UP=9.,DOWN=3.))
    with director.scope(p,'UP'):progress=goal.update(state,2,300)
    assert progress['acquired_since_birth']==70. and progress['pending_down']==10. and roles.side=='DOWN'
    # Test global ceilings through the actual service wrapper, without invoking native.
    director.births={'x':dict(route='ACTIVE',role='ACTIVE_OPPORTUNITY_REPAIR')}
    p.opportunity=SimpleNamespace(apply=lambda *a: (_ for _ in ()).throw(AssertionError('cap should prevent callback')))
    assert director.service('opportunity',dict(t=1,index=1),p,[],None,None)==[]
    # Canonical opening uses the real pure ledger, not a fake release model.
    sys.path.insert(0,str(ROOT))
    from tools.open_funding_recovery_runtime_v3 import FastOpenFundingLedger
    from tools.minimal_student_open_funding_v1 import OpenFundingProfile
    from tools.pair_core_economic_grant_ledger_v1 import Grant
    from tools.hft244_pair_route_legality_v1 import crossing_owners
    from tools.pair_core_asset_route_sizing_v2 import validate_size
    from btc5m_fixed15_research_condition_v1 import make_validator
    from dataclasses import asdict
    profile=OpenFundingProfile(max_live_owners=4096)
    ledger=FastOpenFundingLedger(profile)
    for pid,side in ((1,'UP'),(2,'DOWN')):
        ledger.issue(Grant(pid,'COMPONENT',side,0.,0.,0.,'EXPLICIT_RESEARCH_TEST'))
    p.theta=ctx['manifest']['theta'];p.passive_births=0;p.initial_new_sent=False;p.bidirectional_plans=0;p.multi_new_plans=0;p.new_eligible_frames=0
    frame=dict(t=1,index=0,start=0,end=300,ledger=ledger,own_view=dict(inv=dict(UP=0.,DOWN=0.),cost=0.,n=0),
        world_profile=asdict(profile),book=dict(bids={.48:100.},asks={.50:100.}),quotes=dict(UP=dict(ask=.50),DOWN=dict(ask=.52)),cancellable={})
    roles.side=None;director=bridge.DirectionBridge('INVENTORY',snap)
    ops=director.opening(frame,p,make_validator(validate_size),crossing_owners)
    assert len(ops)==2 and len(ledger.carriers)==0 and all(x['qty']==15. for x in ops)
    for o in ops:ledger.reserve(o['key'],o['parent_id'],o['route'],o['qty'],o['price'],0.,now_ms=1,market_end_ms=300)
    frame['cancellable']={o['key']:True for o in ops};frame['own_view']['n']=2
    for o in ops:ledger.request_cancel(o['key'])
    before={s:ledger.account(pid)['reserved_cash'] for pid,s in ((1,'UP'),(2,'DOWN'))}
    assert director.opening(frame,p,make_validator(validate_size),crossing_owners)==[]
    assert before=={s:ledger.account(pid)['reserved_cash'] for pid,s in ((1,'UP'),(2,'DOWN'))}
    ledger.carriers[ops[0]['key']].state='UNKNOWN'
    assert director.opening(frame,p,make_validator(validate_size),crossing_owners)==[]
    for o in ops:ledger.confirm_terminal(o['key'],filled=0.,payment=0.)
    assert len(director.opening(frame,p,make_validator(validate_size),crossing_owners))==2
    result=dict(status='PASS', compile_modes=5, direction_case_pairs=choices, physical_bank_parts=len(bridge.PARTS),
        monotone_old_repair_progress=progress, global_opportunity_cap='PASS', neutral_canonical_draft_and_pending_unknown='PASS', manifest_sha256=sha(PACKAGE/'manifest.json'),
        files=len(read(PACKAGE/'manifest.json')['files']), local_native_jobs=0, full_native_pending=True)
    dump('COMPONENT',result)
    return result


def worker(job=None):
    import run_btc5m_transfer_structural_worker_v1 as w
    w.STEM=STEM;w.PACKAGE=ORIGINAL_PACKAGE if job and job['rule']=='LEGACY' else PACKAGE;w.jobs=jobs;w.dump=dump
    return w


def book_adapter_repair():
    global PACKAGE
    assert PACKAGE==ORIGINAL_PACKAGE and read(R/(STEM+'_PARITY.json'))['status']=='PASS'
    assert not any(worker().artifact(j,'SUBMIT').exists() for j in jobs()[1:])
    previous=read(PACKAGE/'manifest.json');new=ROOT/'.lan_worker_v1/dynamic_inventory_v48_20260914_v2'
    assert not new.exists()
    for tag in ('COMPONENT','PREFLIGHT','DYNAMIC_LOAD_ONLY'):
        shutil.copy2(R/(STEM+'_'+tag+'.json'),R/(STEM+'_V1_'+tag+'.json'))
    new.mkdir()
    for name,h in previous['files'].items():
        assert sha(PACKAGE/name)==h
        dest=new/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(PACKAGE/name,dest)
    shutil.copy2(ROOT/'tools/btc5m_inventory_direction_bridge_v1.py',new/'direction_bridge.py')
    m=deepcopy(previous);m.update(parent_manifest_sha256=sha(PACKAGE/'manifest.json'),version=STEM+'_BOOK_ADAPTER_V2',maximum_native_jobs=4)
    m['files']={k:sha(new/k) for k in previous['files']}
    assert [k for k,h in previous['files'].items() if m['files'][k]!=h]==['direction_bridge.py']
    before=(PACKAGE/'direction_bridge.py').read_text(encoding='utf-8');after=(new/'direction_bridge.py').read_text(encoding='utf-8')
    assert once(before,"        prices = passive_prices(frame['book'], producer.theta[9], tick)","        book = frame['book']\n        prices = passive_prices(dict(best_bid=max(book['bids']), best_ask=min(book['asks'])), producer.theta[9], tick)")==after
    (new/'manifest.json').write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
    old_source=compile_policy(PACKAGE,'ORACLE_DOWN','LEGACY');new_source=compile_policy(new,'ORACLE_DOWN','LEGACY')
    assert old_source.replace(str(PACKAGE).replace('\\','\\\\'),'PACKAGE').replace(PACKAGE.as_posix(),'PACKAGE')==new_source.replace(str(new).replace('\\','\\\\'),'PACKAGE').replace(new.as_posix(),'PACKAGE')
    wave=read(R/(STEM+'_WAVE.json'));shutil.copy2(R/(STEM+'_WAVE.json'),R/(STEM+'_V1_WAVE.json'))
    for j in wave['jobs'][1:]:
        j['argv'][1]=f'.lan_worker_v1/staging/{new.name}/money_runner.py';j['job_id']=j['job_id'][:-2]+'v2'
    dump('WAVE',wave)
    dump('BOOK_ADAPTER_REPAIR',dict(status='PINNED_BEFORE_DYNAMIC_NATIVE',reason='Native frame book has bids/asks only; derive best prices from current book instead of archive-summary keys.',
        changed_files=['direction_bridge.py'],parent_manifest_sha256=sha(PACKAGE/'manifest.json'),manifest_sha256=sha(new/'manifest.json'),
        legacy_generated_source_parity=True,native_failures=0,previous_legacy_native_audit='PASS',additional_native_parity_required=False,total_native_budget_unchanged=5))
    PACKAGE=new
    return component()


def preflight():
    w=worker()
    selected=jobs()[1:] if PACKAGE!=ORIGINAL_PACKAGE else jobs()
    checked=deepcopy(selected)
    for j in checked:j['argv'][j['argv'].index('--direction-rule')+1]='LEGACY'
    w.jobs=lambda:checked
    return w.preflight()


def submit(index):
    w=worker();j=jobs()[index]
    w.old.identity();w.idle()
    assert read(R/(STEM+'_PREFLIGHT.json'))['status']=='PASS'
    assert not w.artifact(j,'SUBMIT').exists(),'Already attempted: status only'
    if index:
        assert read(w.artifact(jobs()[index-1],'AUDIT'))['execution_status']=='PASS'
        assert read(R/(STEM+'_PARITY.json'))['status']=='PASS'
    assert w.dispatch.cmd_status(w.HOST,j['job_id'])['state']=='missing'
    w.save(j,'SUBMIT',dict(status='ATTEMPT_IN_PROGRESS',job_id=j['job_id'],attempts=1))
    out=w.dispatch.cmd_submit(w.HOST,j['argv'],'.',j['job_id'],4,6,90,auto_collect=False);w.save(j,'SUBMIT',out)
    dump('PROGRESS',dict(status='NATIVE_IN_PROGRESS',current_job=j['job_id'],submissions=index+1))
    return out


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=('prepare','component','book_adapter_repair','preflight','submit','status','collect','audit'));ap.add_argument('--index',type=int,default=0);a=ap.parse_args()
    if a.action in ('prepare','component','book_adapter_repair','preflight'):out=globals()[a.action]()
    elif a.action=='submit':out=submit(a.index)
    elif a.action=='audit':
        from verify_btc5m_dynamic_inventory_native_v1 import audit
        out=audit(a.index)
    else:
        w=worker(jobs()[a.index])
        out=w.collect(a.index) if a.action=='collect' else w.dispatch.cmd_status(w.HOST,jobs()[a.index]['job_id'])
    print(json.dumps(out,ensure_ascii=False,allow_nan=False))
