"""V45: frozen V43/V44 on two availability-selected, newly tested markets."""
import argparse
import ast
from copy import deepcopy
import gzip
import inspect
import json
import shutil
import subprocess
import sys

import btc5m_opening_passive_direction_v1 as opening
from prepare_btc5m_transfer_structural_v1 import ROOT,R,read,sha,once,compiled

STEM=opening.PANEL
SOURCE=R/'market_capsule_v1/source_bundle_new_market_v45_20260913_v1'
INPUT=R/'market_capsule_v1/new_market_v45_public_inputs_20260913_v1'
BASES=dict(V43=opening.V43,V44=opening.V44)
PACKAGES={v:ROOT/f'.lan_worker_v1/frozen_new_market_{v.lower()}_20260913_v1' for v in BASES}
MARKETS=(2127218,2127048)


def dump(tag,x):
    (R/(STEM+'_'+tag+'.json')).write_text(json.dumps(x,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')


def jobs():
    return read(R/(STEM+'_WAVE.json'))['jobs']


def worker(version,selected=None):
    import run_btc5m_transfer_structural_worker_v1 as w
    w.STEM=STEM;w.PACKAGE=PACKAGES[version];w.dump=lambda tag,x:dump(version+'_'+tag,x)
    w.jobs=(lambda:[selected]) if selected else lambda:[j for j in jobs() if j['version']==version]
    return w


def prepare():
    assert not (R/(STEM+'_PROTOCOL.json')).exists() and not any(p.exists() for p in PACKAGES.values())
    protocol=read(R/(opening.STEM+'_PROTOCOL.json'))
    assert tuple(x['quality']['market_id'] for x in protocol['native_selection'])==MARKETS
    assert read(R/(opening.STEM+'_RESULT.json'))['audit']=='PASS'
    parents={v:read(p/'manifest.json') for v,p in BASES.items()}
    for v,p in BASES.items():
        assert sha(p/'manifest.json')==protocol['model_manifest_sha256'][v]
        assert all(sha(p/f)==h for f,h in parents[v]['files'].items())
    dump('PROTOCOL',dict(status='FROZEN_BEFORE_NATIVE',selection_protocol_sha256=sha(R/(opening.STEM+'_PROTOCOL.json')),
        markets=list(MARKETS),arms=['V43_known','V44_known','V44_no_direction'],maximum_native_jobs=6,max_threads=4,
        parent_manifest_sha256=protocol['model_manifest_sha256'],
        hypothesis='Does the unchanged V44 last-third acquisition ablation transfer, relative to unchanged V43 on the same new market? Does its existing first-OWN-fill direction arm retain structure?',
        intervention='No strategy changes or new soft throttle. Only new public inputs; V43 gets the already used observation-only actor_asks field from V44.',
        dedup=protocol['dedup_search'],selection=protocol['selection'],
        source_limit='Canonical public snapshot archive ends before the latest execution tapes; use the most recent common coverage. No interpolation or substitution of missing current public features.',
        no_direction='Frozen asymmetric UP bootstrap plus first confirmed OWN net. This is current end-to-end behavior, not a fair causal test of neutral opening fills and not a new direction predictor.',
        comparisons='Tail effect is paired in known-direction arms. V44 NO_DIRECTION diagnoses overall direction sensitivity; no V43 NO_DIRECTION tail-effect claim.',
        acceptance='Separate execution/accounting PASS from both payoffs, negative-floor area, direction retention, recovery and final residual. Do not call positive maximum branch a success or promote on two adjacent markets.',
        failure='Stop on native/accounting failure, preserve rejected frame and UNKNOWN. Continue a pre-fixed cohort on economic weakness only; no rerun or outcome-based replacement.',
        unchanged=['Passive15','Variable Active, minimum NEW 1','Cash gates OFF, all cash caps null','Work dust and at most five Active','Pending/UNKNOWN reserve through terminal','Theta/qref and native binary','No Target path or winner in actor'],model_fits=0,parameter_search=0,local_native_jobs=0))
    assert not SOURCE.exists() and not INPUT.exists()
    cp=subprocess.run([sys.executable,str(ROOT/'tools/export_market_capsule_source_bundle_v1.py'),'--market-ids',','.join(map(str,MARKETS)),'--output',str(SOURCE)],capture_output=True,text=True,check=True)
    exported=json.loads(cp.stdout);sm=read(SOURCE/'manifest.json');assert tuple(sm['marketIds'])==MARKETS
    from build_market_capsule_v1 import _load_jsonl,_reconstruct_tape
    tree=ast.parse((ROOT/'tools/prepare_v20_consumed_btc5_transfer5_v1.py').read_text(encoding='utf-8'))
    features=ast.literal_eval(next(n.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='FEATURES' for t in n.targets)))
    snapshots=_load_jsonl(SOURCE/'public_snapshots.jsonl');actions=_load_jsonl(SOURCE/'target_actions.jsonl')
    refs={r['market']:r for r in read(R/(opening.STEM+'_RESULT.json'))['rows']}
    INPUT.mkdir();(INPUT/'tapes').mkdir();records=[];labels={};windows=[]
    for market in sm['markets']:
        mid=market['market_id'];tape=SOURCE/f'tapes/{mid}.json.xz'
        tape_meta=next(x for x in sm['tapes'] if x['marketId']==mid);assert sha(tape)==tape_meta['sha256']
        books,info=_reconstruct_tape(tape,mid)
        assert info['resetFailures']==0 and len(books)==market['l2_rows'] and info['matchRows']==market['match_rows']
        bb=[dict(source_ms=b['source_ms'],received_ms=b['received_ms'],best_bid=b['best_bid'],best_ask=b['best_ask'],bids=json.loads(b['top5_bids_json']),asks=json.loads(b['top5_asks_json'])) for b in sorted(books,key=lambda b:(b['received_ms'],b['source_ms']))]
        assert all(b['source_ms']<=b['received_ms'] for b in bb)
        ps=[]
        for row in snapshots:
            if row['market_id']!=mid:continue
            p={k:row[k] for k in ('id','sampled_at_ms','timestamp_ns','archived_at_ms')};s=json.loads(row['snapshot_json'])
            assert int(s['marketId'])==mid
            clocks=[p['sampled_at_ms'],p['timestamp_ns'],p['archived_at_ms'],s.get('sampledAtMs'),s.get('timestampNs')]
            assert all(x is not None and int(x)>0 for x in clocks)
            p['available_ms']=max(int(clocks[0]),(int(clocks[1])+999999)//1000000,int(clocks[2]),int(clocks[3]),(int(clocks[4])+999999)//1000000)
            p['features']={k:s.get(k) for k in features};ps.append(p)
        assert len(ps)==sm['sourceCountsByMarket'][str(mid)]['publicSnapshots']
        aa=[x for x in actions if x['market_id']==mid];ref=refs[mid]
        assert len(aa)==ref['fill_legs'] and all(x['quote_type']=='BID' for x in aa)
        for side in ('UP','DOWN'):assert abs(sum(x['shares'] for x in aa if x['side']==side)-ref['inventory'][side])<1e-7
        assert abs(sum(x['shares']*x['price'] for x in aa)-ref['cost'])<1e-7
        labels[str(mid)]=dict(side=opening.sign(ref['final_net']),source='OFFLINE_FINAL_OBSERVED_TARGET_NET_NOT_WINNER')
        public=dict(market={k:market[k] for k in ('market_id','window_start_ms','window_end_ms','quality_status')},books=bb,public=ps,
            tape=dict(file=f'tapes/{mid}.json.xz',sha256=sha(tape)),actor_input_contract='Current available public frames and canonical OWN only. Tape belongs to simulator; Target labels are offline.')
        (INPUT/f'public_{mid}.json.gz').write_bytes(gzip.compress(json.dumps(public,separators=(',',':'),allow_nan=False).encode(),mtime=0));shutil.copy2(tape,INPUT/f'tapes/{mid}.json.xz')
        windows.append(dict(marketId=mid,window=[market['window_start_ms'],market['window_end_ms']]))
        records.append(dict(market=mid,book_rows=len(bb),public_rows=len(ps),tape_info=info,quality=market['quality_status'],target_legs=len(aa)))
    dump('OFFLINE_LABELS',dict(status='OFFLINE_ONLY',labels=labels));dump('WINDOWS',dict(rows=windows))
    summaries={}
    for version,package in PACKAGES.items():
        base=BASES[version];bm=parents[version];package.mkdir()
        for name in bm['files']:
            dst=package/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(base/name,dst)
        if version=='V43':
            p=package/'renewed_work.py';s=p.read_text(encoding='utf-8')
            s=once(s,"row.update(adaptive=adaptive,cancellable=dict(frame['cancellable']))","row.update(adaptive=adaptive,cancellable=dict(frame['cancellable']),actor_asks=asks)")
            p.write_text(s,encoding='utf-8')
            assert sha(p)==sha(BASES['V44']/'renewed_work.py')
        for p in INPUT.rglob('*'):
            if p.is_file():
                dst=package/'inputs'/p.relative_to(INPUT);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst)
        m=deepcopy(bm);m.update(version=STEM+'_'+version,market=None,paired_markets=list(MARKETS),maximum_native_jobs=6,
            parent_manifest_sha256=sha(base/'manifest.json'),window_source_sha256=sha(R/(STEM+'_WINDOWS.json')),source_manifest_sha256=sha(SOURCE/'manifest.json'))
        m['files']={p.relative_to(package).as_posix():sha(p) for p in package.rglob('*') if p.is_file()}
        changed=[k for k,h in bm['files'].items() if m['files'][k]!=h]
        assert changed==(['renewed_work.py'] if version=='V43' else [])
        (package/'manifest.json').write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
        for mode in ('ORACLE_UP','ORACLE_DOWN','NO_DIRECTION'):compile(compiled(package,mode),'V45_COMPILE_ONLY','exec')
        component=dict(status='PASS',version=version,manifest_sha256=sha(package/'manifest.json'),files=len(m['files']),changed_parent_files=changed,
            policy_change=False,observation_only_change=(version=='V43'),compile_modes=3,source_records=records,local_native_jobs=0)
        dump(version+'_COMPONENT',component);summaries[version]=component
    wave=[]
    for mid in MARKETS:
        for v,direction in [('V43','known'),('V44','known'),('V44','no_direction')]:
            mode='ORACLE_'+labels[str(mid)]['side'] if direction=='known' else 'NO_DIRECTION';arm=v.lower()+'_'+direction
            wave.append(dict(job_id=f'fixed15-core-loop-{mid}-new-market-{arm.replace("_","-")}-20260913-v1',market=mid,arm=arm,version=v,mode=mode,cwd='.',max_threads=4,
                argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{PACKAGES[v].name}/money_runner.py','--market-id',str(mid),'--mode',mode,'--money-mode','PARALLEL_QUANTITY','--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE']))
    dump('WAVE',dict(jobs=wave,sequential=True,max_threads=4));dump('SOURCE',dict(export=exported,records=records))
    dump('PROGRESS',dict(status='PREPARED_NOT_SUBMITTED',submissions=0,pending_jobs=[j['job_id'] for j in wave]))
    return {v:dict(files=s['files'],manifest_sha256=s['manifest_sha256']) for v,s in summaries.items()}


def preflight(version):
    w=worker(version)
    # Existing preflight expects a per-STEM component; bind its read/write stem
    # to this package while exact jobs remain the explicit frozen cohort.
    w.STEM=STEM+'_'+version;w.dump=lambda tag,x:dump(version+'_'+tag,x)
    return w.preflight()


def submit(index):
    j=jobs()[index];w=worker(j['version'],j)
    w.old.identity();w.idle()
    assert read(R/(STEM+'_'+j['version']+'_PREFLIGHT.json'))['status']=='PASS'
    assert not w.artifact(j,'SUBMIT').exists(),'Already attempted: use status'
    if index:
        prev=jobs()[index-1]
        assert read(w.artifact(prev,'AUDIT'))['execution_status']=='PASS'
        assert read(w.artifact(prev,'MECHANISM_AUDIT'))['status']=='PASS'
    assert w.dispatch.cmd_status(w.HOST,j['job_id'])['state']=='missing'
    w.save(j,'SUBMIT',dict(status='ATTEMPT_IN_PROGRESS',job_id=j['job_id'],attempts=1))
    out=w.dispatch.cmd_submit(w.HOST,j['argv'],'.',j['job_id'],4,6,90,auto_collect=False);w.save(j,'SUBMIT',out)
    dump('PROGRESS',dict(status='NATIVE_IN_PROGRESS',current_job=j['job_id'],submissions=index+1))
    return out


def audit(index):
    j=jobs()[index];w=worker(j['version'],j)
    import verify_btc5m_transfer_structural_v1 as v
    v.STEM=STEM;v.PACKAGE=PACKAGES[j['version']];v.jobs=lambda:[None,j]
    tr=read(R/'lan_worker_returns'/j['job_id']/'clock_trace.json.gz')
    code=once(inspect.getsource(v.inspect_job),"len(tr['coordination_submissions'])<=2","len(tr['coordination_submissions'])<=5")
    code=once(code,"same(effective,obs['desired'])","hold=hold_by_index[row['index']];same(effective,hold['input_desired']);effective=hold['effective_desired'];same(effective,obs['desired'])")
    code=once(code,"            else:same(op['price'],ask)","            elif op.get('role')=='ACTIVE_RENEWED_FINITE_CONTINUATION':assert op['price']>=ask-1e-8\n            else:same(op['price'],ask)")
    ns=dict(v.__dict__,hold_by_index={r['index']:r for r in tr['growth_hold_rows']});exec(compile(code,'V45_STRUCTURAL_AUDIT','exec'),ns)
    result=ns['inspect_job'](1)
    if result['execution_status']=='PASS':
        import verify_btc5m_frozen_panel_mechanisms_v1 as mechanisms
        mechanisms.audit(index)
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=('prepare','preflight','submit','status','collect','audit'));ap.add_argument('--version',choices=('V43','V44'));ap.add_argument('--index',type=int,default=0);a=ap.parse_args()
    if a.action=='prepare':out=prepare()
    elif a.action=='preflight':out=preflight(a.version)
    elif a.action in ('submit','audit'):out=globals()[a.action](a.index)
    else:
        j=jobs()[a.index];w=worker(j['version'],j)
        out=w.collect(0) if a.action=='collect' else w.dispatch.cmd_status(w.HOST,j['job_id'])
    print(json.dumps(out,ensure_ascii=False,allow_nan=False))
