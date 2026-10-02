"""Fresh rule-conformance follow-on: IOC, confirmed-holding cap, exact initial F."""
import difflib,hashlib,json,shutil
from pathlib import Path
P=Path(__file__).resolve().parent
PARENT=P/'stage/native_engine_stack_variants185_20261002_v1'
DEST=P/'stage/native_engine_stack_strict185_20261002_v2'
assert not DEST.exists()
shutil.copytree(PARENT,DEST,ignore=shutil.ignore_patterns('__pycache__','MANIFEST.json','FOLLOW_ON.json','FOLLOW_ON.patch'))
patches=[]
def change(name,pairs):
    source=PARENT/name;before=source.read_text(encoding='utf-8');after=before
    for old,new in pairs:
        assert after.count(old)==1,(name,old,after.count(old))
        after=after.replace(old,new)
    compile(after,str(DEST/name),'exec')
    (DEST/name).write_text(after,encoding='utf-8')
    patches.append(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='GTC_parent/'+name,tofile='STRICT_child/'+name)))
change('comparison_policy.py',[
    ('self.frozen = False','self.frozen = False\n        self.initial_context_invalid = False'),
    ("if age >= 12000 and self.fav is None:\n            self.fav = 'UP' if mid >= .5 else 'DOWN'", "if age == 12000 and self.fav is None:\n            self.fav = 'UP' if mid >= .5 else 'DOWN'\n        if self.effective == 'FAV_TAKER' and age > 12000 and self.fav is None:\n            self.initial_context_invalid = True"),
    ('owned = sum(inventory.values()) + sum(pending.values())','owned = sum(inventory.values())  # Frozen rule: confirmed holdings. Ledger keeps pending reservations.'),
    ("if self.frozen: reason='FAV_PERMANENT_STOP'", "if self.initial_context_invalid: reason='INVALID_MISSING_12S_INITIAL_F'\n            elif self.frozen: reason='FAV_PERMANENT_STOP'"),
    ("assert p.intent(**frame(12000,.79,.81,inventory={'UP':290.,'DOWN':0.},pending={'UP':10.,'DOWN':0.})) is None", "assert p.intent(**frame(12000,.79,.81,inventory={'UP':290.,'DOWN':0.},pending={'UP':10.,'DOWN':0.}))['qty']==15\n    assert p.intent(**frame(14000,.79,.81,inventory={'UP':300.,'DOWN':0.})) is None\n    p=ComparisonPolicy('FAV_TAKER')\n    assert p.intent(**frame(20000)) is None and p.initial_context_invalid and p.fav is None\n    assert p.intent(**frame(22000)) is None"),
    ("'pending_in_owned_cap'", "'confirmed_holdings_cap_pending_retained_by_ledger','missing_exact_12s_F_invalid'")
])
change('runtime/tools/open_funding_native_active_adapter_v1.py',[
    ('ACTIVE is a GTC LIMIT','ACTIVE is an IOC LIMIT (comparison follow-on only)'),
    ("if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,float(op['qty']),base.ex.hbt.GTC,base.ex.hbt.LIMIT,False))", "if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,float(op['qty']),base.ex.hbt.IOC,base.ex.hbt.LIMIT,False))"),
    ("else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,float(op['qty']),base.ex.hbt.GTC,base.ex.hbt.LIMIT,False))", "else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,float(op['qty']),base.ex.hbt.IOC,base.ex.hbt.LIMIT,False))")
])
change('stack_replay.py',[
    ('    import hftbacktest\n','    import hftbacktest\n    assert hasattr(hftbacktest,"IOC"), "IOC native capability required"\n'),
    ("'owner_status':'RIGHT_CENSORED_EOF' if unresolved else 'ALL_TERMINAL'", "'owner_status':'RIGHT_CENSORED_EOF' if unresolved else 'ALL_TERMINAL','policy_validity':'INVALID_MISSING_12S_INITIAL_F' if producer.initial_context_invalid else 'VALID_OBSERVED_CONTEXT','effective_strategy':producer.effective,'time_in_force':'IOC'"),
    ("rows={s:[] for s in ('V1_SWITCH','V2_THROTTLE')}", "rows={s:[] for s in ('FAV_TAKER','UNDER_TAKER','V1_SWITCH','V2_THROTTLE')}"),
    ("'status':'COMPLETE_STACK_SWITCH_THROTTLE185'", "'status':'COMPLETE_STACK_STRICT_FOUR_ARM185'"),
    ("'rows':370", "'rows':740"),
    ("print('COMPLETE_STACK_SWITCH_THROTTLE185',flush=True)", "print('COMPLETE_STACK_STRICT_FOUR_ARM185',flush=True)")
])
(DEST/'FOLLOW_ON.patch').write_text('\n'.join(patches),encoding='utf-8')
meta={'parent_package':PARENT.name,'status':'FROZEN_RULE_CONFORMANCE_CORRECTION','changes':['ACTIVE GTC to IOC; no resting/maker fills','strategy holdings predicate uses confirmed inventory only; owner reservations unchanged','FAV exact 12s initial F required; absent context invalid with no new orders'],'original_runtime_files_modified_only_in_new_package':['tools/open_funding_native_active_adapter_v1.py'],'source_parent_preserved':True,'criteria_changed':False,'RV_threshold':3.1834e-05,'fits':0,'live_changes':0}
(DEST/'FOLLOW_ON.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
files={f.relative_to(DEST).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(DEST.rglob('*')) if f.is_file()}
(DEST/'MANIFEST.json').write_text(json.dumps({'files':files,'job_id':'native-engine-stack-strict-four185-20261002-v2','markets':185,'max_threads':4,'parallel_paths':1,'fixtures_remote':r'C:\BTC5M-worker\.lan_worker_v1\staging\native_engine_platform185_20261002_v1\fixtures','strategies':['FAV_TAKER','UNDER_TAKER','V1_SWITCH','V2_THROTTLE'],'rv_threshold':3.1834e-05,'model_fits':0,'parent_runtime_unchanged':False,'runtime_changes_are_scoped_follow_on_only':True},indent=2),encoding='utf-8')
print(json.dumps({'package':DEST.name,'files':len(files),'bytes':sum(f.stat().st_size for f in DEST.rglob('*') if f.is_file()),'native_executed':0,'fits':0}))
