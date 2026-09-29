from __future__ import annotations
import argparse,bisect,importlib.util,json,lzma,sqlite3,sys,os
from pathlib import Path
HERE=Path(__file__).resolve(); ROOT=HERE.parents[2] if HERE.parent.name=='staging' else HERE.parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
sp=(HERE.parent/'test_r4_maker_continuous_latecompletion_trace_v18.py') if HERE.parent.name=='staging' else (ROOT/'tools/test_r4_maker_continuous_latecompletion_trace_v18.py')
spec=importlib.util.spec_from_file_location('r4v18',sp);sim=importlib.util.module_from_spec(spec);spec.loader.exec_module(sim)
def pub_rows(mid):
 db=sqlite3.connect(f'file:{ROOT / "data/public_source_snapshot_archive_v2.db"}?mode=ro',uri=True)
 try: rr=db.execute('select sampled_at_ms,snapshot_json from public_source_snapshots_v2 where market_id=? order by sampled_at_ms',(mid,)).fetchall()
 finally: db.close()
 out=[]
 for t,raw in rr:
  try:out.append((int(t),json.loads(raw)))
  except:pass
 return out
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--manifest',required=True);a=ap.parse_args();ids=[int(x) for x in json.loads(Path(a.manifest).read_text(encoding='utf-8'))['ids']];seam={};public={};diag=[];sim.ROOT=ROOT
 if hasattr(sim.tape,'ARCHIVE_DIR'):sim.tape.ARCHIVE_DIR=ROOT/'data/execution_tape_v1/markets'
 for mid in ids:
  p=ROOT/'data/hft_forward_paper_v1/markets'/f'{mid}_r2_hft_closed_loop_v1.json.xz'
  if not p.exists():diag.append({'marketId':mid,'error':'MARKET_FILE_MISSING'});continue
  with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
  r=sim.simulate(d,'CONT_STATE_H2_SUSPEND_LATE_COMPLETE_TOUCH1');tr=r.get('lateActionTrace') or []
  if not tr:diag.append({'marketId':mid,'error':'NO_STRICTPAST_LATE_ACQUISITION'});continue
  e=tr[0];t=int(e['t']);lid=e['logical'];seam[str(mid)]={'t':t,'logical':lid,'side':e.get('side'),'need':e.get('need'),'secondsPastNeed':e.get('secondsPastNeed'),'source':'FROZEN_V18_FIRST_MATERIALIZED_LATE_WEAK_ACQUISITION'}
  rr=pub_rows(mid);ts=[x[0] for x in rr];j=bisect.bisect_right(ts,t)-1
  if j<0:diag.append({'marketId':mid,'error':'NO_PUBLIC_SNAPSHOT_BEFORE_ANCHOR','anchorMs':t});continue
  at,snap=rr[j];public[str(mid)]={'sampledAtMs':at,'anchorMs':t,'ageMs':t-at,'snapshot':snap};diag.append({'marketId':mid,'anchorMs':t,'logical':lid,'publicAtMs':at,'publicAgeMs':t-at})
 rep={'version':'R4_V38_12_FRESH_SEAM_MAP_V1','researchOnly':True,'strictPast':True,'winnerUsed':False,'settlementUsed':False,'futureTargetActionUsed':False,'definition':'first materialized late weak-side acquisition under frozen V18 H2-suspend lifecycle using only state available at event time','events':seam,'count':len(seam),'requested':len(ids),'diagnostics':diag};pp={'version':'R4_V38_12_FRESH_PUBLIC_MAP_V1','researchOnly':True,'strictPast':True,'winnerUsed':False,'rows':public,'count':len(public),'requested':len(ids)}
 rd=Path(os.environ['BTC5M_LAN_RESULT_DIR']);rd.mkdir(parents=True,exist_ok=True);(rd/'result.json').write_text(json.dumps({'seam':rep,'public':pp},indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps({'requested':len(ids),'seams':len(seam),'public':len(public),'errors':sum('error' in x for x in diag)}))
if __name__=='__main__':main()
