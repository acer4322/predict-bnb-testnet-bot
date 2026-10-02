"""One requested native research replay, or export a saved native baseline.
Run only on the verified second PC; immutable inputs, no training/live authority.
"""
from __future__ import annotations
import os
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[key]='1'
import argparse,hashlib,json,socket,subprocess,sys,time,traceback
from pathlib import Path
from settings import validate_request,MODEL_PINS,BIAS_FIELDS
import timeline
P=Path(__file__).resolve().parent
read=timeline.read;sha=timeline.sha

def save(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');os.replace(tmp,path)

def inputs(request):
    if socket.gethostname().upper()!='DESKTOP-JIERAGF':raise RuntimeError('Native work requires the verified second PC')
    man=read(P/'MANIFEST.json')
    for f,h in man['files'].items():
        if sha(P/f)!=h:raise ValueError('Adapter source changed: '+f)
    source=read(P/'SOURCES.json');legacy=Path(source['legacy_dir'])
    for f,h in man['legacy_files'].items():
        if sha(legacy/f)!=h:raise ValueError('Legacy source changed: '+f)
    b=request['baseline']
    if b['clock_mode']!='P50' or any(b[k] for k in BIAS_FIELDS):raise ValueError('V1 baseline must be a frozen R77 P50 model with zero preferences')
    mid=request['market'];market=next((m for m in source['markets'] if m['market_id']==mid),None)
    if not market:raise ValueError('Market is not in the consumed source cohort')
    ident='R65_S'+str(b['seed'])+'_'+b['addition_mode']
    row=next(r for r in source['rows'] if r['market']==mid and r['model']==ident)
    base=Path(row['path'])
    for f in ('result.json','clock_trace.json.gz','joint_operator_trace.json.gz','neural_trace.json.gz'):
        hashes=[h for n,h in row['artifacts'].items() if n.endswith('__'+f)]
        if len(hashes)!=1 or sha(base/f)!=hashes[0]:raise ValueError('Historical baseline hash mismatch: '+f)
    paths={str(s):str(Path(source['model_dir'])/m['name']) for s,m in MODEL_PINS.items()}
    for s,m in MODEL_PINS.items():
        if sha(Path(paths[str(s)]))!=m['sha256']:raise ValueError('Model hash mismatch')
    sys.path.insert(0,str(legacy))
    import native_support as n
    n.validate()  # Includes pinned native backend, source/data/actor/model contracts.
    return source,legacy,market,row,paths,n

def execute(request,out,legacy,market,row,model_paths,n):
    path=out/('candidate_'+out.name);path.mkdir(exist_ok=False)
    refs=read(Path(read(P/'SOURCES.json')['baselines_path']))
    ref=next(r for r in refs if r['market']==request['market'])
    prior=Path(ref['path']);cfg=read(Path(ref['config_path']));cfg['local_work_arm']='CONTROL'
    b=request['baseline'];cfg['frozen_micro']={'id':'R65_S'+str(b['seed'])+'_'+b['addition_mode'],
        'seed':b['seed'],'path':model_paths[str(b['seed'])],'sha256':MODEL_PINS[b['seed']]['sha256'],
        'source_model':'R65_S'+str(b['seed']),'clock_mode':b['clock_mode'],'addition_mode':b['addition_mode']}
    cfg['playground']={**request,'market_start_ms':market['window_start_ms'],'market_end_ms':market['window_end_ms'],'model_paths':model_paths}
    save(path/'config.json',cfg)
    argv=read(prior/'launch.json')['argv'];argv[0]=sys.executable;argv[1]=str(P/'run_playground.py')
    argv[argv.index('--system-config')+1]=str(path/'config.json')
    # Retain original known actor, lock settings, input tape and venue semantics.
    actor=Path(argv[argv.index('--actor')+1])
    if sha(actor)!=read(P/'MANIFEST.json')['actor_sha256']:raise ValueError('Actor changed')
    argv+=['--legacy-dir',str(legacy)]
    save(path/'launch.json',{'argv':argv,'manifest_sha256':sha(P/'MANIFEST.json'),'requested':request,'source_baseline':row['path']})
    env=os.environ.copy();env['BTC5M_LAN_RESULT_DIR']=str(path)
    start=time.monotonic()
    with (path/'stdout.log').open('w',encoding='utf-8') as so,(path/'stderr.log').open('w',encoding='utf-8') as se:
        child=subprocess.Popen(argv,cwd='C:/BTC5M-worker',env=env,stdout=so,stderr=se)
        rc=child.wait()
    save(path/'PROCESS.json',{'returncode':rc,'seconds':time.monotonic()-start})
    raw_status=read(path/'result.json')
    if raw_status.get('status')!='COMPLETE':raise RuntimeError('Native runner stopped: '+str(raw_status.get('error',raw_status.get('status'))))
    raw,clock,joint,neural=timeline.load_case(path)
    oldraw,oldclock,oldjoint=read(prior/'result.json'),timeline.zipped(prior/'clock_trace.json.gz'),timeline.zipped(prior/'joint_operator_trace.json.gz')
    audit=n.audit(raw,clock,joint,neural,oldraw,oldclock,oldjoint)
    if not (rc==0 or (rc==2 and audit['raw_failures_preserved']==['active_matches_opportunity'])):raise ValueError('Native process failed; evidence preserved')
    compat=read(path/'ACTIVE_COMPAT_EXECUTION.json')
    if len(compat['installations'])!=1 or not compat['last_receipt_prefix_reconciles']:raise ValueError('Native receipt adapter reconciliation failed')
    requested=[o for o in joint['sent'] if o['route']=='ACTIVE' and o['role'].startswith('FROZEN_MICRO_')]
    actual=compat['neural_active_transports']
    if len(actual)!=len(requested):raise ValueError('Native active transport count mismatch')
    for a,b in zip(requested,actual):
        if not all(a[k]==b[k] for k in ('key','side','qty','t')) or a['price']!=b['limit'] or b['tif']!=3 or b['rc']!=0:
            raise ValueError('Native IOC transport mismatch')
    owners={o['key']:o for o in clock['demand_final']['all_final_carriers']}
    for f in clock['demand_final']['canonical_receipts']:
        if f['qty']>0 and owners[f['key']]['route']=='ACTIVE' and float(f.get('contractPrice',f['price']))>owners[f['key']]['limit']+1e-8:
            raise ValueError('Active fill exceeds its limit')
    t=market['window_start_ms']+request['apply_ms'];whole=request['candidate']==request['baseline']
    prefix=timeline.prefix_compare(row['path'],path,t,whole=whole)
    if not prefix['pass']:raise ValueError('Common-prefix audit failed: '+json.dumps(prefix))
    for r in neural['rows']:
        if r['status']=='NN':
            expected=request['candidate'] if r['t']>=t else request['baseline']
            if r['playground']['settings']!=expected:raise ValueError('Preferences applied at the wrong time')
    audit.update(native_IOC_transports_verified=len(actual),prefix=prefix,settings_time_verified=True)
    save(path/'PLAYGROUND_AUDIT.json',audit)
    return path,audit

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--request',required=True);ap.add_argument('--check-only',action='store_true');args=ap.parse_args()
    packet=read(Path(args.request));request=validate_request(packet['request']);kind=packet['kind']
    if kind not in ('BASELINE','CANDIDATE'):raise ValueError('Invalid work kind')
    source,legacy,market,row,paths,n=inputs(request)
    if args.check_only:
        print(json.dumps({'status':'PASS','worker':socket.gethostname(),'model_pins':2,'baseline':row['model'],'kind':kind,'native_runs':0}));return
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);out.mkdir(parents=True,exist_ok=True)
    save(out/'PROGRESS.json',{'state':'EXPORT_BASELINE','request':request,'kind':kind})
    try:
        baseline=timeline.build(row['path'],request['market'],market['window_start_ms'],market['window_end_ms'],request['baseline'],'REUSED_FROZEN_R77')
        save(out/'baseline.json',baseline)
        candidate=None;audit=None
        if kind=='CANDIDATE':
            save(out/'PROGRESS.json',{'state':'RUNNING_NATIVE','request':request})
            path,audit=execute(request,out,legacy,market,row,paths,n)
            candidate=timeline.build(path,request['market'],market['window_start_ms'],market['window_end_ms'],request['candidate'],'NEW_NATIVE_R65_PREFERENCE_REPLAY',audit)
            save(out/'candidate.json',candidate)
        files={name:sha(out/name) for name in ['baseline.json']+(['candidate.json'] if candidate else [])}
        result={'status':'COMPLETE','kind':kind,'request':request,'native_new':int(candidate is not None),'baseline_reused':True,
            'audit':audit,'files':files,'adapter_manifest_sha256':sha(P/'MANIFEST.json'),'training_updates':0,'live_changes':0,
            'promoted':False,'worker':socket.gethostname(),'final_baseline':baseline['final'],'final_candidate':candidate['final'] if candidate else None}
        save(out/'RESULT.json',result);save(out/'PROGRESS.json',{'state':'COMPLETE','native_new':result['native_new']})
        print(json.dumps({'status':'COMPLETE','native_new':result['native_new'],'audit':audit},ensure_ascii=False),flush=True)
    except Exception as e:
        save(out/'FAILURE.json',{'error':repr(e),'traceback':traceback.format_exc(),'request':request,'no_automatic_retry':True})
        save(out/'PROGRESS.json',{'state':'FAILED_PRESERVED'});raise
if __name__=='__main__':main()
