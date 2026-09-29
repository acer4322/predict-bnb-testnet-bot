"""Bounded consumed-source staging; never stage outcomes or Target features."""
import hashlib
import json
from pathlib import Path
import shutil
import duckdb

ROOT=Path(__file__).resolve().parents[1]
MIDS=[2022796,2023438,2023478,2023597,2023609,2024020,2024030,2024133,2024786,2025063,2025070,2025201]


def main():
    frozen=ROOT/'.lan_worker_v1/root_native_composite_handback3_20260910_v1'
    out=ROOT/'.lan_worker_v1/root_family_support_stagea12_20260910_v1'
    assert not out.exists(); out.mkdir(); files={}
    def add_bytes(rel,blob):
        p=out/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(blob)
        files[rel]=hashlib.sha256(blob).hexdigest()
    m=json.loads((frozen/'HANDBACK_MANIFEST.json').read_text())
    for rel,sha in m['files'].items():
        if not rel.endswith('.py'): continue
        p=frozen/rel; assert p.stat().st_size<=5*1024**2
        blob=p.read_bytes(); assert hashlib.sha256(blob).hexdigest()==sha
        add_bytes(rel,blob)
    for rel in ['tools/run_root_native_composite_handback_v1.py','tools/run_root_family_support_stagea12_v1.py']:
        add_bytes(rel,(ROOT/rel).read_bytes())
    prereg='data/research/r4_v0/p0_provenance_v1/ROOT_FAMILY_LABEL_SUPPORT_STAGEA12_PREREG_V1_20260910.md'
    add_bytes('PREREG.md',(ROOT/prereg).read_bytes())
    prior=ROOT/'data/research/lan_worker_returns/root-native-composite-handback3-20260910-v1/COMPACT.json'
    assert hashlib.sha256(prior.read_bytes()).hexdigest()=='f2e28782d271c92c44681e24c494e5ff4ae31c91eb2f336beb9743ae88b6d197'
    golden={r['branch']:r['signature'] for r in json.loads(prior.read_text())['rows'] if r['marketId']==2022538}
    add_bytes('golden.json',json.dumps(golden).encode())
    capsule=ROOT/'data/research/market_capsule_v1/benchmark_50_v1'
    source=ROOT/'data/research/market_capsule_v1/source_bundle_50_v1'
    assert hashlib.sha256((capsule/'markets.parquet').read_bytes()).hexdigest()=='4a37e205233ea30ea982c8acbf530be8ba8090f914b3351ce5d75a8df64d0e31'
    assert (capsule/'public_snapshots.parquet').stat().st_size<50*1024**2
    source_sha=hashlib.sha256((source/'manifest.json').read_bytes()).hexdigest()
    assert source_sha=='f55b618bd9eaae077fb66682fd3e776808b53b3c4a9bd1eb6a85a8e9b45a6dd5'
    tapes={r['marketId']:r for r in json.loads((source/'manifest.json').read_text())['tapes']}
    con=duckdb.connect(config={'threads':'2','memory_limit':'256MB'})
    selected=con.execute('SELECT market_id FROM read_parquet(?) ORDER BY window_start_ms,market_id LIMIT 12 OFFSET 3',[str(capsule/'markets.parquet')]).fetchall()
    assert [r[0] for r in selected]==MIDS
    cols=json.loads((ROOT/'.lan_worker_v1/root_btc5m_source_smoke1_20260910_v2/MANIFEST.json').read_text())['publicFeatureKeys']
    counts={}
    for mid in [2022538]+MIDS:
        p=source/tapes[mid]['file']; assert p.stat().st_size<=2*1024**2
        blob=p.read_bytes(); assert hashlib.sha256(blob).hexdigest()==tapes[mid]['sha256']
        add_bytes(f'tapes/{mid}.json.xz',blob)
        raw=con.execute('SELECT id,sampled_at_ms,timestamp_ns,archived_at_ms,snapshot_json FROM read_parquet(?) WHERE market_id=? ORDER BY sampled_at_ms,id LIMIT 5001',[str(capsule/'public_snapshots.parquet'),mid]).fetchall()
        assert 0<len(raw)<=5000
        rows=[]
        for rid,t,ns,arc,payload in raw:
            s=json.loads(payload); assert int(s['marketId'])==mid
            avail=max(int(t),(int(ns)+999999)//1000000,int(arc),int(s['sampledAtMs']),(int(s['timestampNs'])+999999)//1000000)
            rows.append(dict(id=rid,availableMs=avail,sampledMs=t,timestampNs=ns,archivedMs=arc,features={k:s.get(k) for k in cols}))
        rows.sort(key=lambda r:(r['availableMs'],r['id'])); assert len({r['availableMs'] for r in rows})==len(rows)
        blob=json.dumps(rows,separators=(',',':'),allow_nan=False).encode(); assert len(blob)<=2*1024**2
        add_bytes(f'public/{mid}.json',blob); counts[mid]=len(rows)
    con.close()
    manifest=dict(files=files,fixture=2022538,markets=MIDS,maxBE=39,parentSourceManifest=source_sha,
        externalNativeSha256='74af885fe5bab4873e4672422befb0c3c5acf513ec8da0e3f80562a0c56505c6')
    add_bytes('STAGE_MANIFEST.json',json.dumps(manifest,indent=2).encode())
    print(json.dumps(dict(package=str(out),bytes=sum((out/p).stat().st_size for p in files),publicRows=counts,
        manifestSha256=hashlib.sha256((out/'STAGE_MANIFEST.json').read_bytes()).hexdigest())))


if __name__=='__main__': main()
