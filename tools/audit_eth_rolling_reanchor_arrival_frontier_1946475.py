from __future__ import annotations
import json,lzma,tempfile,zipfile,shutil,sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_dagger60_smoke_v1 as v1
BUNDLE=Path('data/research/r4_v0/p0_provenance_v1/eth_latest_settled24_20260905_bundle.zip')
MID=1946475
SUBMITS=[(1788535281363,0.57,'UP_4'),(1788535282756,0.61,'UP_5'),(1788535285558,0.64,'UP_6'),(1788535287375,0.65,'UP_7'),(1788535288761,0.66,'UP_8')]

def main():
    tmp=Path(tempfile.mkdtemp(prefix='arrival_frontier_1946475_'))
    try:
        zipfile.ZipFile(BUNDLE).extractall(tmp)
        tape=tmp/'tapes'/f'{MID}.json.xz';payload=json.loads(lzma.decompress(tape.read_bytes()).decode('utf-8'))
        ups=sorted(payload['updates'],key=lambda u:(int(u[1]),int(u[0])))
        book={'bids':{},'asks':{}};snaps=[]
        targets=[]
        for ts,px,key in SUBMITS:
            for lag in (0,250,500,1000):targets.append((ts+lag,ts,px,key,lag))
        targets.sort();j=0
        for u in ups:
            t=int(u[1]);v1.apply(book,u);q=v1.quotes(book)
            if not q:continue
            while j<len(targets) and t>=targets[j][0]:
                target,base,px,key,lag=targets[j];up=q['UP'];snaps.append({'key':key,'submitT':base,'sampleLagMs':lag,'sampleT':t,'orderPrice':px,'upBid':float(up['bid']),'upAsk':float(up['ask']),'behindBidTicks':round((float(up['bid'])-px)/0.01,6),'spreadTicks':round((float(up['ask'])-float(up['bid']))/0.01,6),'wouldImproveInsideSpread':bool(float(up['ask'])-float(up['bid'])>0.0100001)});j+=1
        by={}
        for r in snaps:by.setdefault(r['key'],[]).append(r)
        rows=[]
        for ts,px,key in SUBMITS:
            rr=by.get(key,[]);r0=next((x for x in rr if x['sampleLagMs']==0),None);r250=next((x for x in rr if x['sampleLagMs']==250),None)
            rows.append({'key':key,'submitT':ts,'price':px,'samples':rr,'staleAtArrival250ms':bool(r250 and r250['upBid']>px+1e-9),'arrivalBehindTicks':(r250['behindBidTicks'] if r250 else None),'submitSpreadTicks':(r0['spreadTicks'] if r0 else None),'arrivalSpreadTicks':(r250['spreadTicks'] if r250 else None)})
        out={'version':'ROLLING_REANCHOR_ARRIVAL_FRONTIER_AUDIT_1946475_V1','date':'2026-09-05','marketId':MID,'assumedEntryLatencyMs':250,'rows':rows,'aggregate':{'orders':len(rows),'staleAt250ms':sum(bool(r['staleAtArrival250ms']) for r in rows),'medianArrivalBehindTicks':sorted([r['arrivalBehindTicks'] for r in rows if r['arrivalBehindTicks'] is not None])[len(rows)//2] if rows else None},'boundary':['exact execution tape public book only','no strategy mutation','UP-side best bid/ask reconstructed strict-past','250ms equals current HFT entry latency configuration','no Target data']}
        p=Path('data/research/r4_v0/p0_provenance_v1/ROLLING_REANCHOR_ARRIVAL_FRONTIER_AUDIT_1946475_20260905.json');p.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False))
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
