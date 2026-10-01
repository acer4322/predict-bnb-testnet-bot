"""Exercise the real sync CLI in an isolated local repository and bare remote."""
import gzip,json,pathlib,subprocess,sys,tempfile
ROOT=pathlib.Path(__file__).resolve().parents[4]
task_dir=pathlib.Path(tempfile.mkdtemp(prefix='deep_sync_merge_test_'))
repo=task_dir/'repo';repo.mkdir();remote=task_dir/'remote.git'
def run(args,cwd=repo):
 r=subprocess.run(args,cwd=cwd,capture_output=True,text=True,encoding='utf-8');assert r.returncode==0,(args,r.stdout,r.stderr);return r.stdout
run(['git','init','--bare',str(remote)]);run(['git','init']);run(['git','config','user.name','Research merge test']);run(['git','config','user.email','local-test@example.invalid'])
(repo/'marker.txt').write_text('current tree marker');run(['git','add','.']);run(['git','commit','-m','local fixture']);run(['git','branch','-M','main']);run(['git','remote','add','origin',str(remote)])
run(['git','checkout','--orphan','research-data']);run(['git','rm','-rf','.'])
d=repo/'research_pack';(d/'labels').mkdir(parents=True);(d/'labels/old.json').write_text('{}');(d/'target').mkdir();(d/'target/keep.json').write_text('{}')
old=dict(paths=1,entries=[dict(path='old/base',files={})],labels=[dict(name='old.json',bytes=2)],target=[dict(name='keep.json')]);(d/'INDEX.json').write_text(json.dumps(old))
run(['git','add','.']);run(['git','commit','-m','old pack']);run(['git','push','origin','research-data']);run(['git','checkout','main'])
before=run(['git','status','--porcelain']);head=run(['git','rev-parse','HEAD'])
returns=task_dir/'returns';arm=returns/'newjob/arms/test_DEEP_1';arm.mkdir(parents=True)
(arm/'result.json').write_text(json.dumps(dict(status='COMPLETE',market_id=1,general_finite_active_rows=[{}])))
(arm/'deep_layer_trace.json').write_text(json.dumps(dict(method='DEEP_LAYER_v1',orders=[])))
(arm/'AUDIT_POST_COLLECTION.json').write_text('{}');public=task_dir/'public';public.mkdir();(public/'public_1.json.gz').write_bytes(gzip.compress(b'{}',mtime=0))
label=task_dir/'new_label.json';label.write_text('{}')
args=[sys.executable,str(ROOT/'tools/sync_research_pack.py'),str(returns),'--out',str(task_dir/'pack'),'--public-root',str(public),'--extra','new.json='+str(label)]
dry=run(args+['--dry-run']);assert 'paths=1' in dry and 'FLAGGED' not in dry
run(args+['--git-push','--branch','research-data']);run(['git','fetch','origin','research-data'])
new=json.loads(run(['git','show','FETCH_HEAD:research_pack/INDEX.json']))
assert len(new['entries'])==2 and {x['name'] for x in new['labels']}=={'old.json','new.json'} and new['target']==old['target']
run(['git','cat-file','-e','FETCH_HEAD:research_pack/target/keep.json']);run(['git','cat-file','-e','FETCH_HEAD:research_pack/newjob/arms/test_DEEP_1/deep_layer_trace.json']);run(['git','cat-file','-e','FETCH_HEAD:research_pack/newjob/arms/test_DEEP_1/AUDIT_POST_COLLECTION.json'])
assert run(['git','status','--porcelain'])==before and run(['git','rev-parse','HEAD'])==head
print(json.dumps(dict(status='PASS',old_labels_preserved=True,target_index_preserved=True,deep_trace_copied=True,current_worktree_unchanged=True,fixture=str(task_dir))))
