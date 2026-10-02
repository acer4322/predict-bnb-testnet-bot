from __future__ import annotations
import argparse,csv,hashlib,json,math,sqlite3,sys,zlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from src.predict_bot.execution_tape_archive_v1 import load_archive
CSV=ROOT/'data/research/8784_r2_vs_8786_cap100_fresh_v1_markets.csv'
DB=ROOT/'data/wallet_maker_book_inference.db'
ARC=ROOT/'data/execution_tape_v1/markets'
OUT=ROOT/'data/research/hftbacktest_execution_shift_v0/execution_tape_frozen126_integrity_v1.json'
REQ={'amountFilled','executedAt','makers','market','priceExecuted','settlementId','taker','transactionHash'}

def frozen126():
    rows=list(csv.DictReader(CSV.open(encoding='utf-8-sig'))); ids=[int(r['marketId']) for r in rows if int(r['marketId'])<=1511912][:126]
    assert len(ids)==126 and ids[0]==1506209 and ids[-1]==1511912
    return ids

def canon(x): return json.dumps(x,separators=(',',':'),sort_keys=True,ensure_ascii=False)
def h(xs):
    d=hashlib.sha256()
    for x in xs: d.update(canon(x).encode()); d.update(b'\n')
    return d.hexdigest()
def dec(b): return json.loads(zlib.decompress(b).decode('utf-8'))
def levels(v): return {round(float(k),12):float(q) for k,q in (v or {}).items() if float(q)>1e-12}
def same_book(a,b,tol=1e-7):
    if set(a)!=set(b): return False
    return all(abs(a[k]-b[k])<=tol for k in a)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--start',type=int,default=0); ap.add_argument('--count',type=int,default=126); a=ap.parse_args()
    all_ids=frozen126(); ids=all_ids[a.start:a.start+a.count]; con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    failures=[]; totals={'l2Rows':0,'matchRows':0,'metaRows':0,'archiveBytes':0,'checkpoints':0,'checkedChanges':0}; required_missing=0; bad_maker_legs=0; bad_taker_legs=0
    details=[]
    for m in ids:
        path=ARC/f'{m}.json.xz'
        try: p=load_archive(path)
        except Exception as e: failures.append({'marketId':m,'kind':'DECOMPRESS','error':str(e)}); continue
        dbu=con.execute('select count(*) n,min(source_timestamp_ms) mi,max(source_timestamp_ms) ma from maker_book_inference_updates where market_id=?',(m,)).fetchone()
        dbm=con.execute('select raw_json_z from maker_execution_matches_v1 where market_id=? order by executed_at_ms,match_key',(m,)).fetchall()
        tables={str(r[0]) for r in con.execute("select name from sqlite_master where type='table'")}
        meta_n=con.execute('select count(*) from maker_execution_orderbook_meta_v1 where market_id=?',(m,)).fetchone()[0] if 'maker_execution_orderbook_meta_v1' in tables else 0
        man=con.execute('select * from maker_execution_archive_manifest_v1 where market_id=?',(m,)).fetchone()
        u=p.get('updates') or []; matches=p.get('matches') or []; meta=p.get('executionMeta') or []
        errs=[]
        if p.get('version')!='PREDICT_EXECUTION_TAPE_ARCHIVE_V1': errs.append('version')
        if int(p.get('marketId') or 0)!=m: errs.append('marketId')
        if len(u)!=int(dbu['n']): errs.append(f'l2Count:{len(u)}!={dbu["n"]}')
        if len(matches)!=len(dbm): errs.append(f'matchCount:{len(matches)}!={len(dbm)}')
        if len(meta)!=int(meta_n): errs.append(f'metaCount:{len(meta)}!={meta_n}')
        if u and (int(u[0][0])!=int(dbu['mi']) or int(u[-1][0])!=int(dbu['ma'])): errs.append('l2TimeBoundary')
        rawdb=[dec(r[0]) for r in dbm]
        if h(matches)!=h(rawdb): errs.append('rawMatchDigest')
        for x in matches:
            miss=REQ-set(x)
            if miss: required_missing+=1
            t=x.get('taker') if isinstance(x.get('taker'),dict) else {}
            if not {'hash','signer','fee','amount','price','quoteType','outcome'}.issubset(t): bad_taker_legs+=1
            for mk in x.get('makers') or []:
                if not isinstance(mk,dict) or not {'hash','signer','fee','amount','price','quoteType','outcome'}.issubset(mk): bad_maker_legs+=1
        # L2 reconstruction integrity: checkpoint becomes canonical, then every delta must agree with current before/after.
        bids={}; asks={}; initialized=False; cp=0; ch=0
        for row in u:
            _src,_recv,_oc,is_cp,cb,ca,changes=row
            if not initialized:
                if not is_cp or cb is None or ca is None: errs.append('firstRowNotCheckpoint'); break
                bids=levels(cb); asks=levels(ca); initialized=True; cp+=1; continue
            for side,book in [('bids',bids),('asks',asks)]:
                for px,before,after,delta in (changes or {}).get(side,[]):
                    px=round(float(px),12); before=float(before); after=float(after); delta=float(delta); ch+=1
                    cur=float(book.get(px,0.0))
                    if abs((after-before)-delta)>1e-7: errs.append('deltaArithmetic'); break
                    if abs(cur-before)>1e-6: errs.append('deltaBeforeMismatch'); break
                    if after>1e-12: book[px]=after
                    else: book.pop(px,None)
            if is_cp:
                cp+=1
                if cb is None or ca is None or not same_book(bids,levels(cb)) or not same_book(asks,levels(ca)): errs.append('checkpointMismatch')
                # reset to checkpoint canonical values after verification
                bids=levels(cb); asks=levels(ca)
        totals['l2Rows']+=len(u); totals['matchRows']+=len(matches); totals['metaRows']+=len(meta); totals['archiveBytes']+=path.stat().st_size; totals['checkpoints']+=cp; totals['checkedChanges']+=ch
        if man is None: errs.append('manifestMissing')
        else:
            if int(man['l2_rows'])!=len(u) or int(man['match_rows'])!=len(matches) or int(man['meta_rows'])!=len(meta): errs.append('manifestCountMismatch')
            if int(man['archive_bytes'])!=path.stat().st_size: errs.append('manifestBytesMismatch')
        if errs: failures.append({'marketId':m,'kind':'INTEGRITY','errors':sorted(set(errs))})
        details.append({'marketId':m,'l2Rows':len(u),'matchRows':len(matches),'metaRows':len(meta),'archiveBytes':path.stat().st_size,'errors':sorted(set(errs))})
    con.close()
    report={'version':'EXECUTION_TAPE_FROZEN126_INTEGRITY_V1','cohort':{'markets':len(ids),'first':ids[0] if ids else None,'last':ids[-1] if ids else None,'startIndex':a.start},'summary':{**totals,'failureMarkets':len(failures),'requiredMatchPayloadRowsMissingKeys':required_missing,'badTakerLegs':bad_taker_legs,'badMakerLegs':bad_maker_legs,'ok':not failures and required_missing==0 and bad_taker_legs==0 and bad_maker_legs==0},'failures':failures,'markets':details,'boundary':'Historical frozen126 has no forward-only lastOrderSettled/settlementsPending metadata; metaRows=0 is expected. Raw /orders/matches plus historical L2 remain fully archived.'}
    out_path=OUT if a.start==0 and a.count>=126 else OUT.with_name(f'execution_tape_frozen126_integrity_i{a.start}_n{len(ids)}_v1.json')
    out_path.parent.mkdir(parents=True,exist_ok=True); out_path.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'ok':report['summary']['ok'],'path':str(out_path),'summary':report['summary'],'failures':failures[:5]},ensure_ascii=False))
if __name__=='__main__': main()
