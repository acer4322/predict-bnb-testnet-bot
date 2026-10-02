"""Exact frozen-package reuse plus two named consumed tapes; no HFT."""
from pathlib import Path
import hashlib
import json
import shutil
import duckdb

ROOT=Path(__file__).resolve().parents[1]


def main():
    frozen=ROOT/'.lan_worker_v1/root_btc5m_source_smoke1_20260910_v2'
    out=ROOT/'.lan_worker_v1/root_dual_legal_smoke3_20260910_v1'
    assert not out.exists()
    m=json.loads((frozen/'MANIFEST.json').read_text())
    out.mkdir()
    files={}
    for rel,spec in m['files'].items():
        if not rel.endswith('.py') and rel!='tapes/2022527.json.xz':
            continue
        source=frozen/rel
        assert hashlib.sha256(source.read_bytes()).hexdigest()==spec['sha256']
        dest=out/rel; dest.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(source,dest)
        files[rel]=spec['sha256']
    for mid in [2022538,2022602]:
        rel=f'tapes/{mid}.json.xz'
        src=ROOT/'data/research/market_capsule_v1/source_bundle_50_v1'/rel
        assert src.stat().st_size<=1024**2
        shutil.copy2(src,out/rel); files[rel]=hashlib.sha256(src.read_bytes()).hexdigest()
    for rel in ['tools/run_root_dual_legal_label_smoke_v1.py']:
        shutil.copy2(ROOT/rel,out/rel); files[rel]=hashlib.sha256((out/rel).read_bytes()).hexdigest()
    con=duckdb.connect(config={'threads':'2','memory_limit':'256MB'})
    capsule=ROOT/'data/research/market_capsule_v1/benchmark_50_v1'
    rows=con.execute('SELECT market_id FROM read_parquet(?) ORDER BY window_start_ms,market_id LIMIT 3',[str(capsule/'markets.parquet')]).fetchall()
    assert [r[0] for r in rows]==[2022527,2022538,2022602]
    cols=json.loads((frozen/'MANIFEST.json').read_text())['publicFeatureKeys']
    public={}
    for mid in [2022527,2022538,2022602]:
        rows=con.execute('SELECT id,sampled_at_ms,timestamp_ns,archived_at_ms,snapshot_json FROM read_parquet(?) WHERE market_id=? ORDER BY sampled_at_ms,id',[str(capsule/'public_snapshots.parquet'),mid]).fetchall()
        assert 0<len(rows)<=5000
        result=[]
        for rid,t,ns,arc,payload in rows:
            s=json.loads(payload); assert int(s['marketId'])==mid
            avail=max(int(t),(int(ns)+999999)//1000000,int(arc),int(s['sampledAtMs']),(int(s['timestampNs'])+999999)//1000000)
            result.append({'id':rid,'availableMs':avail,'sampledMs':t,'timestampNs':ns,'archivedMs':arc,'features':{k:s.get(k) for k in cols}})
        result.sort(key=lambda x:(x['availableMs'],x['id']))
        assert len({r['availableMs'] for r in result})==len(result)
        public[str(mid)]=result
    blob=json.dumps(public,separators=(',',':'),allow_nan=False).encode()
    assert len(blob)<=3*1024**2
    (out/'dual_public.json').write_bytes(blob); files['dual_public.json']=hashlib.sha256(blob).hexdigest()
    src=ROOT/'data/research/r4_v0/p0_provenance_v1/ROOT_DUAL_LEGAL_CONTINUATION_LABEL_SMOKE3_PREREG_V1_20260910.md'
    shutil.copy2(src,out/'PREREG.md'); files['PREREG.md']=hashlib.sha256(src.read_bytes()).hexdigest()
    manifest={'files':files,'markets':[2022527,2022538,2022602],'maxBE':12,'frozenParentManifestSha256':hashlib.sha256((frozen/'MANIFEST.json').read_bytes()).hexdigest()}
    (out/'DUAL_MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({'package':str(out),'bytes':sum((out/p).stat().st_size for p in files),'publicRows':{m:len(r) for m,r in public.items()},'manifestSha256':hashlib.sha256((out/'DUAL_MANIFEST.json').read_bytes()).hexdigest()}))


if __name__=='__main__':main()
