"""Bind anonymous DEEP rows to canonical receipt sequences, retaining duplicates."""
import json,gzip
from pathlib import Path
P=Path(__file__).resolve().parents[1];ROOT=P.parents[2]
def read(p):
 b=p.read_bytes();return json.loads(gzip.decompress(b) if p.suffix=='.gz' else b)
summary=read(P/'SUMMARY.json');rows=[]
for row in summary['rows']:
 mid=row['market'];reused=row['reused'];arm=ROOT/'data/research/lan_worker_returns'/('btc5m-deep-layer-stage1-20261002-v1' if reused else summary['resume_job'])/'arms'/f"{'deep1' if reused else 'deep1r9'}_DEEP_{mid}"
 tr=read(arm/'clock_trace.json.gz');clock=read(arm/'execution_clock.json');d=read(arm/'deep_layer_trace.json')
 news=[(p['t'],o) for p in tr['plans'] for o in p['operations'] if o['kind']=='NEW' and o.get('role')=='DEEP_LAYER']
 full={r['sequence']:r for r in tr['demand_final']['full_raw_receipts']};assert len(full)==len(clock['receipts'])
 for r in clock['receipts']:
  extra=full[r['sequence']];assert all(extra[k]==v for k,v in r.items())
  assert extra['order_id']==int(extra['key'].rsplit('_',1)[1])
 assert len(news)==len(d['orders']);matched=0
 for seq,((t,new),order) in enumerate(zip(news,d['orders']),1):
  assert order['order_seq']==seq and order['place_t']==t and order['price']==new['price'] and order['side']==new['side'] and order['qty']==new['qty']
  canonical=[r for r in clock['receipts'] if r['order_id']==int(new['key'].rsplit('_',1)[1]) and r['qty']>0]
  assert len(canonical)==len(order['fills'])
  for r,f in zip(canonical,order['fills']):
   assert full[r['sequence']]['key']==new['key']
   assert f['exchange_ns']==r['exchange_ts'] and f['receive_ns']==r['receive_ts'] and f['qty']==r['qty'] and f['maker']==r['maker']
   assert f['price']==(r['price'] if order['side']=='UP' else 1-r['price']);matched+=1
  assert abs(sum(r['qty'] for r in canonical)-order['filled_qty'])<1e-8
 rows.append(dict(market=mid,status='PASS',orders=len(news),canonical_fills_matched=matched,sequence_and_order_binding=True))
report=dict(status='PASS',paths=10,orders=sum(r['orders'] for r in rows),canonical_fills_matched=sum(r['canonical_fills_matched'] for r in rows),duplicates_preserved=True,rows=rows)
(P/'RECEIPT_READBACK.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report))
