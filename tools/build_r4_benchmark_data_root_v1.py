from __future__ import annotations
import argparse,json,shutil,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STRAT=ROOT/'data/strategy_target_compare_v1.db';BOOK=ROOT/'data/wallet_maker_book_inference.db';SETTLE=ROOT/'data/target_wallet_official_v1.db';TAPES=ROOT/'data/execution_tape_v1/markets'

def clone_table(src:Path,dst:Path,table:str,ids:list[int],where_extra:str='',params_extra=()):
    s=sqlite3.connect(src); d=sqlite3.connect(dst)
    try:
        exists=d.execute("select 1 from sqlite_master where type='table' and name=?",(table,)).fetchone()
        if not exists:
            sql=s.execute("select sql from sqlite_master where type='table' and name=?",(table,)).fetchone()[0]; d.execute(sql)
        cols=[r[1] for r in s.execute(f'pragma table_info({table})')]; c=','.join(cols); ph=','.join('?'*len(cols)); q=','.join('?'*len(ids))
        rows=s.execute(f'select {c} from {table} where market_id in ({q}) {where_extra}',ids+list(params_extra)).fetchall()
        if rows:d.executemany(f'insert or replace into {table} ({c}) values ({ph})',rows)
        d.commit();return len(rows)
    finally:s.close();d.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();ids=[int(x) for x in a.ids.split(',') if x.strip()];out=ROOT/a.out
    if out.exists():shutil.rmtree(out)
    out.mkdir(parents=True); counts={}
    sd=out/'strategy_target_compare_v1.db'
    for t in ['our_decisions','our_orders','our_fills']: counts[t]=clone_table(STRAT,sd,t,ids)
    counts['maker_book_inference_updates']=clone_table(BOOK,out/'wallet_maker_book_inference.db','maker_book_inference_updates',ids)
    counts['target_market_results']=clone_table(SETTLE,out/'target_wallet_official_v1.db','target_market_results',ids)
    td=out/'execution_tape_v1'/'markets';td.mkdir(parents=True);missing=[]
    for mid in ids:
        s=TAPES/f'{mid}.json.xz'
        if s.exists():shutil.copy2(s,td/s.name)
        else:missing.append(mid)
    rep={'version':'R4_BENCHMARK_DATA_ROOT_V1','marketIds':ids,'counts':counts,'missingTapes':missing}
    (out/'manifest.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
