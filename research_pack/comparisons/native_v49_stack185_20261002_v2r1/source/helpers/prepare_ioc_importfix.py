"""New package after load-only rejected IOC's top-level Python export."""
import difflib,hashlib,json,shutil
from pathlib import Path
P=Path(__file__).resolve().parent
OLD=P/'stage/native_engine_stack_strict185_20261002_v2'
NEW=P/'stage/native_engine_stack_strict185_20261002_v2r1'
assert not NEW.exists()
shutil.copytree(OLD,NEW,ignore=shutil.ignore_patterns('__pycache__','MANIFEST.json'))
patch=[]
for name in ('runtime/tools/open_funding_native_active_adapter_v1.py','stack_replay.py'):
    before=(OLD/name).read_text(encoding='utf-8');after=before
    if name.startswith('runtime/'):
        assert after.count('base.ex.hbt.IOC')==2
        after=after.replace('from collections import Counter','from collections import Counter\nfrom hftbacktest.order import IOC').replace('base.ex.hbt.IOC','IOC')
    else:
        old='    assert hasattr(hftbacktest,"IOC"), "IOC native capability required"'
        assert after.count(old)==1
        after=after.replace(old,'    from hftbacktest.order import IOC\n    assert IOC==3, "IOC ABI must match the existing Rust TimeInForce enum"')
    compile(after,str(NEW/name),'exec');(NEW/name).write_text(after,encoding='utf-8')
    patch.append(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='v2/'+name,tofile='v2r1/'+name)))
shutil.copy2(OLD/'FOLLOW_ON.patch',NEW/'RULE_CONFORMANCE.patch')
shutil.copy2(OLD/'FOLLOW_ON.json',NEW/'RULE_CONFORMANCE.json')
(NEW/'FOLLOW_ON.patch').write_text('\n'.join(patch),encoding='utf-8')
(NEW/'FOLLOW_ON.json').write_text(json.dumps({'parent':OLD.name,'correction':'Import existing IOC=3 from hftbacktest.order, not the top-level package. No wrapper/binary/Rust modifications.','prior_check':'LOAD_ONLY_FAIL_IOC_TOP_LEVEL_ATTRIBUTE_ABSENT','prior_native_executed':0,'prior_submitted':False,'rules':'RULE_CONFORMANCE.json','rules_patch':'RULE_CONFORMANCE.patch','fits':0},indent=2),encoding='utf-8')
(P/'STRICT_LOAD_ONLY_FAILURE.json').write_text(json.dumps({'package':OLD.name,'stage_preserved':True,'status':'LOAD_ONLY_FAIL','error':'IOC is not a top-level hftbacktest export; hftbacktest.order.IOC and Rust TimeInForce::IOC both equal 3','native_executed':0,'submitted':False,'repair_package':NEW.name},indent=2),encoding='utf-8')
files={f.relative_to(NEW).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(NEW.rglob('*')) if f.is_file()}
m=json.loads((OLD/'MANIFEST.json').read_bytes());m['files']=files;m['job_id']='native-engine-stack-strict-four185-20261002-v2r1'
(NEW/'MANIFEST.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
print(json.dumps({'package':NEW.name,'files':len(files),'native_executed':0,'fits':0}))
