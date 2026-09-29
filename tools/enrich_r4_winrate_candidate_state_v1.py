from __future__ import annotations
import argparse,json,os,sys
from pathlib import Path
ROOT=Path(os.environ.get('BTC5M_WORKER_ROOT',r'C:\BTC5M-worker')).resolve()
if not (ROOT/'tools').is_dir(): ROOT=Path.cwd().resolve()
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_context_control_v0 as r3ctl

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--manifest',required=True);ap.add_argument('--strategy-db',required=True);ap.add_argument('--tape-dir',required=True);ap.add_argument('--book-db',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
 base.STRATEGY_DB=Path(a.strategy_db).resolve();base.tape_v1.ARCHIVE_DIR=Path(a.tape_dir).resolve();base.ex.BOOK_DB=Path(a.book_db).resolve();base.mod.BOOK_DB=Path(a.book_db).resolve()
 man=json.loads(Path(a.manifest).read_text(encoding='utf-8'));rows=[]
 for src in man.get('rows',[]):
  mid=int(src['marketId']);at=int(src['decisionMs']);rec={'marketId':mid,'decisionMs':at,'candidate':None,'error':None}
  try:
   rep=r3ctl.run_market(mid,True);match=[d for d in (rep.get('decisionRows') or []) if int(d.get('decisionMs') or -1)==at]
   if match:rec['candidate']=match[0]
   else:rec['error']='DECISION_TIMESTAMP_NOT_FOUND'
  except Exception as e:rec['error']=f'{type(e).__name__}:{e}'
  rows.append(rec);print(json.dumps({'marketId':mid,'found':rec['candidate'] is not None,'error':rec['error']}),flush=True)
 out={'version':'R4_ADAPTIVE_WINRATE_CANDIDATE_STATE_ENRICHMENT_V1','researchOnly':True,'actionAuthority':False,'rows':rows};p=Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'markets':len(rows),'found':sum(r['candidate'] is not None for r in rows),'out':str(p)}))
if __name__=='__main__':main()
