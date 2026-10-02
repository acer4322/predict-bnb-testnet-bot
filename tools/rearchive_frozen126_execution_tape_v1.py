from __future__ import annotations
import argparse,csv,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from src.predict_bot.execution_tape_archive_v1 import archive_market_to_xz
CSV=ROOT/'data/research/8784_r2_vs_8786_cap100_fresh_v1_markets.csv'
DB=ROOT/'data/wallet_maker_book_inference.db'
OUT=ROOT/'data/execution_tape_v1/markets'

def frozen126():
    rows=list(csv.DictReader(CSV.open(encoding='utf-8-sig')))
    ids=[]
    for r in rows:
        m=int(r['marketId'])
        if m<=1511912:
            ids.append(m)
    ids=ids[:126]
    assert len(ids)==126 and ids[0]==1506209 and ids[-1]==1511912, (len(ids),ids[:1],ids[-1:] if ids else [])
    return ids

def main():
    p=argparse.ArgumentParser(); p.add_argument('--start',type=int,default=0); p.add_argument('--count',type=int,default=21); a=p.parse_args()
    ids=frozen126()[a.start:a.start+a.count]
    out=[]
    for m in ids:
        r=archive_market_to_xz(DB,m,OUT,overwrite=True)
        out.append({'marketId':m,'l2Rows':r['l2Rows'],'metaRows':r['metaRows'],'matchRows':r['matchRows'],'archiveBytes':r['archiveBytes']})
    print(json.dumps({'ok':True,'start':a.start,'markets':len(out),'first':out[0]['marketId'] if out else None,'last':out[-1]['marketId'] if out else None,'l2Rows':sum(x['l2Rows'] for x in out),'matchRows':sum(x['matchRows'] for x in out),'metaRows':sum(x['metaRows'] for x in out),'archiveBytes':sum(x['archiveBytes'] for x in out)},ensure_ascii=False))
if __name__=='__main__': main()
