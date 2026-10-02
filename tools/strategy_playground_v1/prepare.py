"""Build immutable, bounded source index; never execute native HFT on the host."""
from pathlib import Path
import hashlib,json,shutil
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
SOURCE=ROOT/'data/research/v49_fixed_up_native24_schema_resume_20260920_r77'
STORE=ROOT/'data/research/strategy_playground_v1_20260921'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x):
    p.parent.mkdir(parents=True,exist_ok=True)
    text=json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)
    if p.exists():
        if json.loads(p.read_text(encoding='utf-8'))!=x:raise RuntimeError('Existing immutable artifact differs: '+str(p))
    else:p.write_text(text,encoding='utf-8')
def main():
    a=json.loads((SOURCE/'ANALYSIS.json').read_text(encoding='utf-8'))
    c=json.loads((SOURCE/'COHORT.json').read_text(encoding='utf-8'))['markets']
    markets={m['market_id']:m for m in c}
    rows=[]
    for r in a['rows']:
        rows.append({key:r[key] for key in ('market','model','path','artifacts')})
    if len(rows)!=96 or len(markets)!=24:raise ValueError('Expected frozen R77 24 x 4 source index')
    sources={'source':'R77 consumed historical native diagnostic','markets':c,'rows':rows,
             'legacy_dir':'C:/BTC5M-worker/.lan_worker_v1/staging/v49_fixed_up_native24_schema_resume_20260920_r77',
             'baselines_path':'C:/BTC5M-worker/.lan_worker_v1/results/v49-fixed-up-native24-20260920-r77/BASELINES.json',
             'model_dir':'C:/BTC5M-worker/.lan_worker_v1/results/v49-onpolicy-repair-state-micro-training-20260920-r65'}
    write(HERE/'SOURCES.json',sources)
    write(STORE/'catalog.json',{'markets':[{'market':m['market_id'],'start_ms':m['window_start_ms'],'end_ms':m['window_end_ms']} for m in c]})
    legacy_files=json.loads((SOURCE/'MANIFEST.json').read_text(encoding='utf-8'))['files']
    for name,digest in legacy_files.items():
        if sha(SOURCE/name)!=digest:raise ValueError('Pinned local legacy source differs: '+name)
    files={name:sha(HERE/name) for name in ('settings.py','playground_operator.py','run_playground.py','timeline.py','worker.py')}
    files['SOURCES.json']=sha(HERE/'SOURCES.json')
    write(HERE/'MANIFEST.json',{'version':'PLAYGROUND_V1_NATIVE_ADAPTER','legacy_files':legacy_files,'files':files,
          'actor_sha256':'1644d01f6a94963eb21daa059fc9cc26fbd5556a602c15f9941902139872f4fc',
          'limits':{'max_threads':4,'max_simultaneous_heavy_jobs':1},'live_authority':False,'training':False})
    print(json.dumps({'status':'PREPARED','markets':24,'native_source_paths':96,'legacy_files':len(legacy_files),'adapter_files':len(files),'native_runs':0}))
if __name__=='__main__':main()
