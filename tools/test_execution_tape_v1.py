from __future__ import annotations
import json, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from src.predict_bot.predict_wallet_maker_book_inference_collector import MakerBookInferenceCollector, parse_full_book, decode_json

payload={
 'marketId':123,'updateTimestampMs':1736696400123,'orderCount':42,
 'asks':[[0.62,1500.0]],'bids':[[0.61,2000.0]],
 'lastOrderSettled':{'id':'987','price':'0.62','kind':'LIMIT','marketId':123,'side':'Ask','outcome':'Yes'},
 'settlementsPending':{'asks':[[0.62,310.0]],'bids':[[0.60,12.5]]},
}
book=parse_full_book(payload)
assert book and book['lastOrderSettled']['id']=='987'
assert abs(book['settlementsPending']['asks'][0.62]-310.0)<1e-9
with tempfile.TemporaryDirectory() as td:
    db=Path(td)/'x.db'; target=Path(td)/'no.db'
    c=MakerBookInferenceCollector(db,target)
    c.current_market_id=123
    with c.db_lock:
        c.db.execute("update maker_book_inference_meta set excluded_market_id=999 where cohort=?",(c.cohort,)); c.db.commit()
    uid=c.process_book_payload(123,payload,received_ms=1736696400200)
    assert uid
    with c.db_lock:
        row=c.db.execute('select * from maker_execution_orderbook_meta_v1 where market_id=123').fetchone()
        assert row and row['order_count']==42
        assert decode_json(row['last_order_settled_z'])['id']=='987'
        pending=decode_json(row['settlements_pending_z'])
        assert float(pending['asks']['0.62'])==310.0
    c.stop()
print(json.dumps({'ok':True,'orderCount':book['orderCount'],'lastOrderSettled':book['lastOrderSettled'],'settlementsPending':book['settlementsPending']},default=str))
