from __future__ import annotations
import argparse,json,re,time,zipfile
from pathlib import Path
from typing import Any
import duckdb

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_SCAN_ROOT=ROOT/'data'/'research'
DEFAULT_OUT=ROOT/'data'/'research'/'execution_bundle_market_registry_v1'
VERSION='EXECUTION_BUNDLE_MARKET_REGISTRY_V1'
PAT=re.compile(r'^tapes/(\d+)\.json\.xz$')

def rel(p:Path)->str:
    try:return p.resolve().relative_to(ROOT).as_posix()
    except:return p.resolve().as_posix()

def q(p:Path)->str:return p.resolve().as_posix().replace("'","''")

def scan_bundle(p:Path)->list[dict[str,Any]]:
    out=[]
    with zipfile.ZipFile(p) as z:
        for info in z.infolist():
            m=PAT.match(info.filename)
            if not m:continue
            out.append({'marketId':int(m.group(1)),'member':info.filename,'memberBytes':int(info.file_size),'compressedBytes':int(info.compress_size),'crc32':int(info.CRC)})
    return out

def refresh(scan_root:Path,out_dir:Path)->dict[str,Any]:
    t0=time.perf_counter();out_dir.mkdir(parents=True,exist_ok=True);state_p=out_dir/'registry_state_v1.json';parq=out_dir/'bundle_market_registry_v1.parquet';manifest_p=out_dir/'bundle_market_registry_v1.manifest.json'
    old={}
    if state_p.exists():
        try:old=json.loads(state_p.read_text(encoding='utf-8')).get('bundles') or {}
        except:old={}
    zips=sorted(scan_root.rglob('*.zip'));new={};rescanned=0;reused=0;errors=[]
    for p in zips:
        rp=rel(p);st=p.stat();prev=old.get(rp)
        fp=(int(st.st_size),int(st.st_mtime_ns))
        if prev and int(prev.get('bytes',-1))==fp[0] and int(prev.get('mtimeNs',-1))==fp[1] and isinstance(prev.get('tapes'),list):
            tapes=prev['tapes'];reused+=1
        else:
            try:tapes=scan_bundle(p);rescanned+=1
            except Exception as ex:
                errors.append({'bundle':rp,'error':f'{type(ex).__name__}:{ex}'});tapes=[]
        new[rp]={'bytes':fp[0],'mtimeNs':fp[1],'tapes':tapes}
    state={'version':VERSION,'scanRoot':rel(scan_root),'bundles':new,'updatedAtMs':int(time.time()*1000)}
    tmp=state_p.with_suffix('.json.tmp');tmp.write_text(json.dumps(state,ensure_ascii=False,separators=(',',':')),encoding='utf-8');tmp.replace(state_p)
    rows=[]
    for bp,meta in new.items():
        canonical=int('/p0_provenance_v1/' in '/'+bp.replace('\\','/')+'/' and 'DO_NOT_EXECUTE' not in bp.upper())
        do_not=int('DO_NOT_EXECUTE' in bp.upper())
        for t in meta['tapes']:
            rows.append({'market_id':int(t['marketId']),'bundle_path':bp,'bundle_bytes':int(meta['bytes']),'bundle_mtime_ns':int(meta['mtimeNs']),'member':t['member'],'member_bytes':int(t['memberBytes']),'compressed_bytes':int(t['compressedBytes']),'crc32':int(t['crc32']),'canonical_hint':canonical,'do_not_execute_name':do_not})
    import tempfile
    with tempfile.TemporaryDirectory(prefix='bundle_registry_') as td:
        jp=Path(td)/'rows.jsonl'
        with jp.open('w',encoding='utf-8') as f:
            for r in rows:f.write(json.dumps(r,separators=(',',':'))+'\n')
        con=duckdb.connect(database=':memory:')
        if rows:
            con.execute(f"COPY (SELECT * FROM read_json_auto('{q(jp)}',format='newline_delimited') ORDER BY market_id, canonical_hint DESC, do_not_execute_name ASC, bundle_path) TO '{q(parq)}' (FORMAT PARQUET,COMPRESSION ZSTD)")
        else:
            con.execute(f"COPY (SELECT CAST(NULL AS BIGINT) market_id, CAST(NULL AS VARCHAR) bundle_path WHERE FALSE) TO '{q(parq)}' (FORMAT PARQUET)")
        markets=int(con.execute(f"SELECT count(distinct market_id) FROM read_parquet('{q(parq)}')").fetchone()[0]);dups=int(con.execute(f"SELECT count(*) FROM (SELECT market_id FROM read_parquet('{q(parq)}') GROUP BY market_id HAVING count(*)>1)").fetchone()[0]);con.close()
    man={'version':VERSION,'scanRoot':rel(scan_root),'zipFiles':len(zips),'rows':len(rows),'markets':markets,'marketsWithMultipleBundleCopies':dups,'rescannedBundles':rescanned,'reusedBundles':reused,'errors':errors,'elapsedSeconds':round(time.perf_counter()-t0,4),'boundary':['ZIP central-directory registry only; no tape decompression','canonical_hint is path preference metadata, not execution truth','raw bundle remains canonical source']}
    manifest_p.write_text(json.dumps(man,indent=2,ensure_ascii=False),encoding='utf-8')
    return man

def check(out_dir:Path)->dict[str,Any]:
    t0=time.perf_counter();state_p=out_dir/'registry_state_v1.json';parq=out_dir/'bundle_market_registry_v1.parquet'
    if not state_p.exists() or not parq.exists():
        return {'version':VERSION+'_CHECK','fresh':False,'reason':'registry_missing','elapsedMs':round((time.perf_counter()-t0)*1000,4)}
    state=json.loads(state_p.read_text(encoding='utf-8'));changed=[];missing=[]
    for rp,meta in (state.get('bundles') or {}).items():
        p=Path(rp)
        if not p.is_absolute():p=ROOT/p
        if not p.exists():missing.append(rp);continue
        st=p.stat()
        if int(meta.get('bytes',-1))!=int(st.st_size) or int(meta.get('mtimeNs',-1))!=int(st.st_mtime_ns):changed.append(rp)
    fresh=not changed and not missing
    return {'version':VERSION+'_CHECK','fresh':fresh,'knownBundles':len(state.get('bundles') or {}),'changed':changed,'missing':missing,'newBundleDiscoveryChecked':False,'elapsedMs':round((time.perf_counter()-t0)*1000,4)}

def find(out_dir:Path,mid:int)->dict[str,Any]:
    p=out_dir/'bundle_market_registry_v1.parquet';con=duckdb.connect(database=':memory:');rows=con.execute(f"SELECT market_id,bundle_path,member_bytes,compressed_bytes,crc32,canonical_hint,do_not_execute_name FROM read_parquet('{q(p)}') WHERE market_id=? ORDER BY canonical_hint DESC,do_not_execute_name ASC,bundle_path",[int(mid)]).fetchall();con.close()
    return {'marketId':int(mid),'count':len(rows),'candidates':[{'bundlePath':r[1],'memberBytes':r[2],'compressedBytes':r[3],'crc32':r[4],'canonicalHint':bool(r[5]),'doNotExecuteName':bool(r[6])} for r in rows]}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--scan-root',type=Path,default=DEFAULT_SCAN_ROOT);ap.add_argument('--output-dir',type=Path,default=DEFAULT_OUT);sub=ap.add_subparsers(dest='cmd',required=True);sub.add_parser('refresh');sub.add_parser('check');f=sub.add_parser('find');f.add_argument('--market-id',type=int,required=True);a=ap.parse_args()
    if a.cmd=='refresh':print(json.dumps(refresh(a.scan_root,a.output_dir),indent=2,ensure_ascii=False))
    elif a.cmd=='check':print(json.dumps(check(a.output_dir),indent=2,ensure_ascii=False))
    else:print(json.dumps(find(a.output_dir,a.market_id),indent=2,ensure_ascii=False))
if __name__=='__main__':main()
