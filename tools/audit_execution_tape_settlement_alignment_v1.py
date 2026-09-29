from __future__ import annotations
import argparse,json,math,sqlite3,zlib
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/wallet_maker_book_inference.db'

def dec(b): return json.loads(zlib.decompress(b).decode()) if b else None
def iso_ms(s):
    d=datetime.fromisoformat(str(s).replace('Z','+00:00')); d=d if d.tzinfo else d.replace(tzinfo=timezone.utc); return int(d.timestamp()*1000)
def px(v): return float(int(str(v)))/1e18 if str(v).isdigit() and int(str(v))>10**12 else float(v)
def outcome_name(v):
    if isinstance(v,dict): v=v.get('name')
    x=str(v or '').upper(); return 'YES' if x in {'UP','YES'} else 'NO' if x in {'DOWN','NO'} else x
def sig(outcome,side,price): return (outcome_name(outcome),str(side or '').upper(),round(float(price),8))
def raw_sigs(r):
    out=[]
    for leg in [r.get('taker')]+list(r.get('makers') or []):
        if not isinstance(leg,dict): continue
        try: out.append(sig(leg.get('outcome'),leg.get('quoteType'),px(leg.get('price'))))
        except Exception: pass
    return set(out)
def settled_sig(x):
    if not isinstance(x,dict): return None
    try: return sig(x.get('outcome'),x.get('side'),float(x.get('price')))
    except Exception: return None
def pct(xs,q):
    if not xs:return None
    a=sorted(xs); p=(len(a)-1)*q; lo=int(math.floor(p)); hi=int(math.ceil(p)); return a[lo] if lo==hi else a[lo]*(hi-p)+a[hi]*(p-lo)
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--market-id',type=int,required=True); ap.add_argument('--window-ms',type=int,default=1800); a=ap.parse_args(); m=a.market_id
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    metas=con.execute('select source_timestamp_ms,received_at_ms,last_order_settled_z,settlements_pending_z from maker_execution_orderbook_meta_v1 where market_id=? order by source_timestamp_ms,id',(m,)).fetchall()
    raws=[dec(r[0]) for r in con.execute('select raw_json_z from maker_execution_matches_v1 where market_id=? order by executed_at_ms,match_key',(m,))]
    con.close()
    anchors=[]; last_id=None
    for r in metas:
        x=dec(r['last_order_settled_z']); ident=str((x or {}).get('id') or '')
        if not ident or ident==last_id: continue
        last_id=ident; sg=settled_sig(x)
        if sg: anchors.append({'sourceMs':int(r['source_timestamp_ms']),'receivedMs':int(r['received_at_ms']),'id':ident,'sig':sg,'settled':x})
    aligned=[]; no=[]; amb=[]; offsets=[]
    for i,r in enumerate(raws):
        try: sec=iso_ms(r.get('executedAt'))
        except Exception: no.append(i); continue
        ss=raw_sigs(r)
        cand=[x for x in anchors if x['sig'] in ss and -500<=x['sourceMs']-sec<=a.window_ms]
        if not cand: no.append(i); continue
        cand.sort(key=lambda x:(abs((x['sourceMs']-sec)-500),x['sourceMs']))
        best=cand[0]; off=best['sourceMs']-sec; offsets.append(off)
        aligned.append({'rawIndex':i,'executedAt':r.get('executedAt'),'offsetMs':off,'candidateCount':len(cand),'anchorSourceMs':best['sourceMs'],'anchorId':best['id'],'anchorSig':best['sig'],'rawSigCount':len(ss)})
        if len(cand)>1: amb.append(i)
    report={'version':'EXECUTION_TAPE_SETTLEMENT_ALIGNMENT_V1','marketId':m,'summary':{'metaRows':len(metas),'settlementAnchors':len(anchors),'rawMatches':len(raws),'alignedRawMatches':len(aligned),'alignmentRate':len(aligned)/len(raws) if raws else None,'uniqueAlignmentRate':(len(aligned)-len(amb))/len(raws) if raws else None,'ambiguousRawMatches':len(amb),'unmatchedRawMatches':len(no),'offsetMedianMs':pct(offsets,.5),'offsetP10Ms':pct(offsets,.1),'offsetP90Ms':pct(offsets,.9),'offsetMinMs':min(offsets) if offsets else None,'offsetMaxMs':max(offsets) if offsets else None},'sampleAligned':aligned[:50],'boundary':'Diagnostic only. lastOrderSettled ID transitions are used as millisecond settlement anchors; no HftBacktest timestamp replacement until coverage/ambiguity are validated.'}
    out=ROOT/f'data/research/hftbacktest_execution_shift_v0/execution_tape_settlement_alignment_market{m}_v1.json'; out.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8'); print(json.dumps({'ok':True,'path':str(out),'summary':report['summary']},ensure_ascii=False))
if __name__=='__main__':main()
