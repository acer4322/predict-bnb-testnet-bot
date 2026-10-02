from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.predict_bot.predict_wallet_maker_book_inference_collector import native_level_for_target, TARGET_WALLET
from src.predict_bot.predict_wallet_maker_book_inference_collector_v2_1_impl import MakerBookConsumableLifecycleCollector

OUT = ROOT / 'data' / 'research' / 'execution_aware_fill_lifecycle_v0'
SPLIT = OUT / 'r2_pending_management_fresh_split_v1.json'
BOOK_DB = ROOT / 'data' / 'wallet_maker_book_inference.db'
TARGET_DB = ROOT / 'data' / 'target_wallet_official_v1.db'
REPORT = OUT / 'r2_fresh_dev_v21_lifecycle_backfill_v1.json'


def main() -> int:
    split=json.loads(SPLIT.read_text(encoding='utf-8'))
    ids=[int(x) for x in split['developmentMarkets']]
    idset=set(ids)

    book=sqlite3.connect(BOOK_DB); book.row_factory=sqlite3.Row
    target=sqlite3.connect(f'file:{TARGET_DB.resolve().as_posix()}?mode=ro',uri=True); target.row_factory=sqlite3.Row
    try:
        precision={int(r['market_id']):int(r['decimal_precision']) for r in book.execute(
            f"select market_id,decimal_precision from maker_book_inference_markets where market_id in ({','.join('?' for _ in ids)})",ids)}
        missing_precision=sorted(idset-set(precision))
        if missing_precision:
            raise RuntimeError(f'missing maker_book_inference_markets precision for {missing_precision}')

        grouped=defaultdict(lambda:{'rowid':0,'orderHash':None,'observedMs':None,'shares':0.0})
        for st in range(0,len(ids),100):
            batch=ids[st:st+100]; qs=','.join('?' for _ in batch)
            sql=f'''select rowid target_rowid,leg_id,wallet,market_id,order_hash,event_ms,observed_at_ms,
                           role,quote_type,side,price,shares
                      from wallet_shadow_target_events
                     where asset='BTC' and market_id in ({qs}) and wallet=?
                       and role='MAKER' and quote_type='BID' and side in ('UP','DOWN')
                     order by market_id,event_ms,rowid'''
            for r in target.execute(sql,(*batch,TARGET_WALLET)):
                mid=int(r['market_id']); side=str(r['side']).upper(); price=float(r['price']); event_ms=int(r['event_ms'])
                parent=str(r['order_hash'] or r['leg_id']); key=(mid,parent,event_ms,side,price)
                g=grouped[key]; g['rowid']=max(int(g['rowid']),int(r['target_rowid'])); g['orderHash']=str(r['order_hash'] or '') or None
                obs=r['observed_at_ms']
                if obs is not None: g['observedMs']=max(int(g['observedMs'] or 0),int(obs))
                g['shares']+=float(r['shares'] or 0.0)

        inserts=[]
        for (mid,parent,event_ms,side,price),g in grouped.items():
            native_side,native_price=native_level_for_target(side,price,precision[mid])
            event_id=f'{parent}:{event_ms}:{side}:{price:.12g}'
            inserts.append((event_id,int(g['rowid']),TARGET_WALLET,mid,g['orderHash'],event_ms,g['observedMs'],side,price,float(g['shares']),native_side,native_price,'PENDING'))

        qs=','.join('?' for _ in ids)
        # Idempotent DEV-only rebuild. These tables are research inference; R2 runtime reads public updates only.
        book.execute(f'delete from maker_book_inference_v21_allocations where market_id in ({qs})',ids)
        book.execute(f'delete from maker_book_inference_v21_parent_lifecycles where market_id in ({qs})',ids)
        book.execute(f'delete from maker_book_inference_v21_cancel_candidates where market_id in ({qs})',ids)
        book.execute(f'delete from maker_book_inference_v21_market_meta where market_id in ({qs})',ids)
        book.execute(f'delete from maker_book_inference_target_events where market_id in ({qs})',ids)
        book.executemany('''insert into maker_book_inference_target_events(
            leg_id,target_rowid,wallet,market_id,order_hash,target_event_ms,target_observed_ms,
            side,target_price,target_shares,native_book_side,native_price,status)
            values (?,?,?,?,?,?,?,?,?,?,?,?,?)''',inserts)
        book.commit()
    finally:
        book.close(); target.close()

    collector=MakerBookConsumableLifecycleCollector(BOOK_DB,TARGET_DB)
    try:
        before_pending=int(collector.db.execute(
            f"select count(*) from maker_book_inference_target_events where market_id in ({','.join('?' for _ in ids)}) and status='PENDING'",ids).fetchone()[0])
        loops=0
        while True:
            pending=int(collector.db.execute(
                f"select count(*) from maker_book_inference_target_events where market_id in ({','.join('?' for _ in ids)}) and status='PENDING'",ids).fetchone()[0])
            if pending<=0: break
            collector._match_pending_events(); loops+=1
            if loops>100: raise RuntimeError(f'matching did not converge; pending={pending}')
        reconcile=[]
        for i,mid in enumerate(ids,1):
            ok=bool(collector._reconcile_market(mid))
            n=int(collector.db.execute('select count(*) from maker_book_inference_v21_parent_lifecycles where market_id=?',(mid,)).fetchone()[0])
            hc=int(collector.db.execute('''select count(*) from maker_book_inference_v21_parent_lifecycles where market_id=?
                and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75
                and placement_first_ms is not null and last_target_ms is not null''',(mid,)).fetchone()[0])
            reconcile.append({'marketId':mid,'ok':ok,'lifecycles':n,'highConfidence':hc})
            if i%5==0: print(json.dumps({'progress':i,'marketId':mid,'lifecycles':n,'highConfidence':hc}),flush=True)
        status={str(r[0]):int(r[1]) for r in collector.db.execute(
            f"select status,count(*) from maker_book_inference_target_events where market_id in ({','.join('?' for _ in ids)}) group by status",ids)}
        payload={
            'version':'R2_FRESH_DEV_V21_LIFECYCLE_BACKFILL_V1','researchOnly':True,'liveTradingChanges':False,
            'developmentMarkets':ids,'targetMakerBidGroupsInserted':len(inserts),'pendingBeforeMatch':before_pending,'matchLoops':loops,
            'targetEventStatus':status,'marketsWithLifecycle':sum(r['lifecycles']>0 for r in reconcile),
            'marketsWithHighConfidence':sum(r['highConfidence']>0 for r in reconcile),
            'totalLifecycles':sum(r['lifecycles'] for r in reconcile),'totalHighConfidence':sum(r['highConfidence'] for r in reconcile),
            'marketRows':reconcile,
            'guard':'Canonical V2.1 ingestion/matching/reconciliation semantics reused. DEV-only inference tables rebuilt. R2 runtime targetDataRead remains false; SEALED holdout untouched.'
        }
        REPORT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'report':str(REPORT),'summary':{k:payload[k] for k in ['targetMakerBidGroupsInserted','targetEventStatus','marketsWithLifecycle','marketsWithHighConfidence','totalLifecycles','totalHighConfidence']}},ensure_ascii=False))
    finally:
        try: collector.stop()
        except Exception:
            try: collector.db.close()
            except Exception: pass
    return 0

if __name__=='__main__': raise SystemExit(main())
