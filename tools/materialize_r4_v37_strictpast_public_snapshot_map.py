from pathlib import Path
import json,sqlite3
ROOT=Path(__file__).resolve().parents[1]
SEAMS=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_exact_first_late_event_map_v25.json'
DB=ROOT/'data/public_source_snapshot_archive_v2.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_v37_strictpast_public_snapshot_map.json'
em=json.loads(SEAMS.read_text(encoding='utf-8')).get('events') or {}
rows={};errs=[]
with sqlite3.connect(f'file:{DB}?mode=ro',uri=True) as db:
    for mid,e in em.items():
        t=int(e['t'])
        r=db.execute('select sampled_at_ms,snapshot_json from public_source_snapshots_v2 where market_id=? and sampled_at_ms<? order by sampled_at_ms desc limit 1',(int(mid),t)).fetchone()
        if not r:
            errs.append({'marketId':int(mid),'reason':'NO_STRICT_PAST_SNAPSHOT'});continue
        ts,raw=r
        try:z=json.loads(raw)
        except Exception as ex:
            errs.append({'marketId':int(mid),'reason':f'JSON:{ex}'});continue
        rows[str(mid)]={'sampledAtMs':int(ts),'ageMs':t-int(ts),'snapshot':z}
rep={'version':'R4_V37_STRICTPAST_PUBLIC_SNAPSHOT_MAP','researchOnly':True,'strictPast':True,'winnerUsed':False,'settlementUsed':False,'futureTargetActionUsed':False,'eligibilityUsed':False,'sourceDb':'data/public_source_snapshot_archive_v2.db','seamCount':len(em),'snapshotCount':len(rows),'errors':errs,'rows':rows}
OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
print(json.dumps({'artifact':str(OUT),'seams':len(em),'snapshots':len(rows),'errors':len(errs),'maxAgeMs':max([x['ageMs'] for x in rows.values()] or [-1])},ensure_ascii=False))
