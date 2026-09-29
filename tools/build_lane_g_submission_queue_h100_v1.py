from __future__ import annotations
import argparse,bisect,json,lzma,math,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_execution_tape_feed_v1 as feed
from tools import hftbacktest_execution_shift_audit_v0 as ex
EPS=1e-9; GRID=0.01; ENTRY_MS=250

def k(p): return round(float(p),10)
def finite(x,d=0.0):
    try:
        z=float(x); return z if math.isfinite(z) else d
    except Exception:return d

def apply_depth(book,ev,px,qty):
    side='bids' if ev&int(ex.BUY_EVENT) else 'asks'; p=k(px); q=max(0.0,float(qty))
    if q<=EPS: book[side].pop(p,None)
    else: book[side][p]=q

def book_features(book,native_side,native_price):
    bids=book['bids']; asks=book['asks']; bb=max(bids) if bids else math.nan; ba=min(asks) if asks else math.nan
    same=bids if native_side=='BUY' else asks; opp=asks if native_side=='BUY' else bids
    sb=bb if native_side=='BUY' else ba; ob=ba if native_side=='BUY' else bb
    same_top=float(same.get(k(sb),0.0)) if math.isfinite(sb) else 0.0; opp_top=float(opp.get(k(ob),0.0)) if math.isfinite(ob) else 0.0
    if native_side=='BUY':
        dist=(sb-native_price)/GRID if math.isfinite(sb) else math.nan; same_prices=sorted(bids,reverse=True)[:3]; opp_prices=sorted(asks)[:3]
    else:
        dist=(native_price-sb)/GRID if math.isfinite(sb) else math.nan; same_prices=sorted(asks)[:3]; opp_prices=sorted(bids,reverse=True)[:3]
    b3=sum(float(bids[p]) for p in sorted(bids,reverse=True)[:3]); a3=sum(float(asks[p]) for p in sorted(asks)[:3]); den=b3+a3
    own=float(same.get(k(native_price),0.0)); qty=max(EPS,0.0)
    return {
      'sameBestNative':finite(sb,math.nan),'oppositeBestNative':finite(ob,math.nan),
      'distanceFromSameBestTicks':finite(dist,math.nan),'sameTopQty':same_top,'oppositeTopQty':opp_top,
      'sameTop3Qty':sum(float(same[p]) for p in same_prices),'oppositeTop3Qty':sum(float(opp[p]) for p in opp_prices),
      'nativeSpreadTicks':(ba-bb)/GRID if math.isfinite(bb) and math.isfinite(ba) else math.nan,
      'nativeBookImbalance':(b3-a3)/den if den>EPS else 0.0,
      'sameBookLevels':float(len(same)),'oppositeBookLevels':float(len(opp)),'currentOwnLevelQty':own,
      'ownLevelIsBest':float(math.isfinite(sb) and abs(float(sb)-float(native_price))<=1e-9)
    }

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--structural-rows',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    rows=[json.loads(x) for x in Path(a.structural_rows).read_text(encoding='utf-8').splitlines() if x.strip()]
    rows=[r for r in rows if str(r.get('route'))=='PASSIVE' and int(r.get('firstEventObserved') or 0)==1]
    by={}
    for r in rows: by.setdefault(int(r['marketId']),[]).append(r)
    tmp=Path(tempfile.mkdtemp(prefix='lane_g_submitq_')); out=[]; audit=[]
    try:
      with zipfile.ZipFile(a.bundle) as z:
        for m in by: (tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
      for i,(m,mrows) in enumerate(by.items(),1):
        payload=json.loads(lzma.decompress((tmp/f'{m}.json.xz').read_bytes()).decode('utf-8'))
        ups=sorted(payload['updates'],key=lambda u:(int(u[1]),int(u[0]))); uts=[int(u[1]) for u in ups]; ocs=[float(u[2]) for u in ups]
        feed.ARCHIVE_DIR=tmp; events,_,_=feed.build_archive_events(m,trade_offset='mid')
        specs=[]
        for r in mrows:
          submit=int(r['t']); accept=submit+ENTRY_MS; native_side,native_price=ex.native_order(str(r['side']),float(r['price']))
          specs.append({'row':r,'accept':accept,'nativeSide':native_side,'nativePrice':k(native_price)})
        specs.sort(key=lambda s:s['accept']); ai=0; book={'bids':{},'asks':{}}
        def oc_at(t):
          j=bisect.bisect_right(uts,int(t))-1; return ocs[j] if j>=0 else math.nan
        for e in events:
          ts=int(e['local_ts']//1_000_000)
          while ai<len(specs) and specs[ai]['accept']<=ts:
            s=specs[ai]; r=dict(s['row']); bf=book_features(book,s['nativeSide'],s['nativePrice']); q=float(r.get('qty') or 0.0)
            r.update({'exchangeAcceptT':int(s['accept']),'nativeSide':s['nativeSide'],'nativePrice':float(s['nativePrice']),**bf,'queueInitial':float(bf['currentOwnLevelQty']),'queueInitialToOrder':float(bf['currentOwnLevelQty'])/max(q,EPS),'orderCountAtAccept':oc_at(s['accept'])})
            out.append(r); ai+=1
          ev=int(e['ev']);
          if ev&int(ex.DEPTH_EVENT) or ev&int(ex.DEPTH_SNAPSHOT_EVENT): apply_depth(book,ev,float(e['px']),float(e['qty']))
        while ai<len(specs):
          s=specs[ai]; r=dict(s['row']); bf=book_features(book,s['nativeSide'],s['nativePrice']); q=float(r.get('qty') or 0.0)
          r.update({'exchangeAcceptT':int(s['accept']),'nativeSide':s['nativeSide'],'nativePrice':float(s['nativePrice']),**bf,'queueInitial':float(bf['currentOwnLevelQty']),'queueInitialToOrder':float(bf['currentOwnLevelQty'])/max(q,EPS),'orderCountAtAccept':oc_at(s['accept'])})
          out.append(r); ai+=1
        audit.append({'marketId':m,'inputRows':len(mrows),'outputRows':sum(1 for r in out if int(r['marketId'])==m)})
        if i%20==0: print(json.dumps({'progress':i,'of':len(by),'rows':len(out)}),flush=True)
      report={'version':'LANE_G_SUBMISSION_QUEUE_H100_V1_20260907','researchOnly':True,'runtimeAuthority':False,
       'rows':out,'audit':audit,'coverage':{'inputRows':len(rows),'outputRows':len(out),'markets':len(by),'fillFirst':sum(int(r['fillFirst']) for r in out)},
       'invariants':{'all100Markets':len(by)==100,'allRowsCaptured':len(out)==len(rows),'allAcceptanceAfterSubmit':all(int(r['exchangeAcceptT'])>=int(r['t']) for r in out)},
       'boundary':['exchange-acceptance state only','book/queue/depth/orderCount sampled no later than acceptance','no post-accept trajectory features','no fixed decision window','no winner/terminal PnL/Target future','consumed H100 only','no fresh/no 8781']}
      Path(a.output).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
      print(json.dumps({'ok':True,'coverage':report['coverage'],'invariants':report['invariants']},indent=2),flush=True)
    finally:
      import shutil; shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
