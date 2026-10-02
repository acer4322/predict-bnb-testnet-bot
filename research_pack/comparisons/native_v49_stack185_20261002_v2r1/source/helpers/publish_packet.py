"""Publish one new immutable comparison packet, preserving the current worktree."""
import argparse,hashlib,importlib.util,json,shutil,subprocess,sys
from pathlib import Path

P=Path(__file__).resolve().parent;ROOT=P.parents[2]
s=importlib.util.spec_from_file_location('release_helpers',ROOT/'data/research/hft244_fresh85_after2807162_20261002_v1/stage_release.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def git(w,*args):return subprocess.check_output(['git','-C',str(w),*args])

def main():
    ap=argparse.ArgumentParser();ap.add_argument('packet',type=Path);ap.add_argument('--dry-run',action='store_true');ap.add_argument('--worktree',type=Path)
    a=ap.parse_args();packet=a.packet.resolve();index=json.loads((packet/'INDEX.json').read_bytes())
    files={p.relative_to(packet).as_posix():p for p in packet.rglob('*') if p.is_file()}
    sums=json.loads((packet/'SHA256SUMS.json').read_bytes())['files']
    assert set(files)==set(sums)|{'SHA256SUMS.json'}
    for n,h in sums.items():assert sha(files[n])==h,n
    privacy=json.loads((packet/'PRIVATE_DATA_CHECK.json').read_bytes())
    assert privacy['private_identifiers']==0
    assert privacy['status']=='PASS' and privacy['FLAGGED']==0 or privacy['status']=='PASS_MANUALLY_REVIEWED' and len(privacy['reviewed_flags'])==privacy['FLAGGED']
    for review in privacy.get('reviewed_flags',[]):assert sha(packet/review['file'])==review['sha256']
    size=sum(p.stat().st_size for p in files.values());assert size<50_000_000
    print(json.dumps({'dry_run':a.dry_run,'packet':index['id'],'files':len(files),'bytes':size,'max_bytes':50_000_000,'FLAGGED':privacy['FLAGGED'],'manually_reviewed':len(privacy.get('reviewed_flags',[]))}),flush=True)
    if a.dry_run:return
    assert a.worktree;w=a.worktree.resolve()
    assert not git(w,'status','--porcelain').strip(), 'Preserve existing changes.'
    head=git(w,'rev-parse','HEAD').decode().strip()
    assert head==git(ROOT,'rev-parse','origin/research-data').decode().strip(), 'Update/recheck remote parent first.'
    prefix='research_pack/comparisons/'+index['id'];dest=(w/prefix).resolve()
    assert dest.is_relative_to(w) and not dest.exists(), 'Immutable packet already exists.'
    raw=(w/'research_pack/INDEX.json').read_bytes().decode('utf-8');old=json.loads(raw)
    entry={'id':index['id'],'path':'comparisons/'+index['id'],'markets':index['markets'],'status':index['status'],'files':len(files),'bytes':size,'index_sha256':sha(packet/'INDEX.json'),'sha256sums_sha256':sha(packet/'SHA256SUMS.json')}
    if 'comparisons' in old:
        assert not any(x['id']==entry['id'] for x in old['comparisons'])
        updated=m.append_json_list(raw,'comparisons',[entry])
    else:
        pos=raw.rfind('}');assert pos>=0
        updated=raw[:pos].rstrip()+',\n  "comparisons": '+json.dumps([entry],indent=2)+ '\n'+raw[pos:]
    new=json.loads(updated)
    for k,v in old.items():assert (new[k][:len(v)]==v if k=='comparisons' else new[k]==v),k
    shutil.copytree(packet,dest)
    (w/'research_pack/INDEX.json').write_bytes(updated.encode('utf-8'))
    expected={prefix+'/'+n:sha(p) for n,p in files.items()}
    expected['research_pack/INDEX.json']=sha(w/'research_pack/INDEX.json')
    git(w,'add','--force','--',prefix);git(w,'add','--','research_pack/INDEX.json')
    changed=set(git(w,'diff','--cached','--name-only','-z').decode().split('\0'))-{''};assert changed==set(expected)
    verified=m.verify_blobs(w,'',expected)
    receipt={'parent':head,'packet':index['id'],'files':len(files),'bytes':size,'expected':expected,'git_blobs_sha_verified':verified,'previous_data_preserved':True}
    (P/(index['id']+'_STAGED.json')).write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in receipt.items() if k!='expected'}),flush=True)

if __name__=='__main__':main()
