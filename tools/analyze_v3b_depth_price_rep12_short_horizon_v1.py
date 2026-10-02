from __future__ import annotations
import argparse,json,os,sys,tempfile,zipfile
from pathlib import Path
try:
    import tools.run_v3b_depth_vs_price_direction_filled_conflict_replication12 as rep
except ModuleNotFoundError:
    sys.path.insert(0,str(Path.cwd().parent))
    import run_v3b_depth_vs_price_direction_filled_conflict_replication12 as rep

def state_at(prefix,seq,t0,horizon_ms,favored):
    u=float(prefix['inv']['UP']);d=float(prefix['inv']['DOWN']);cost=float(prefix['cost']);n=0;alts=0;last=None
    role_counts={}; side_qty={'UP':0.0,'DOWN':0.0}
    cutoff=int(t0)+int(horizon_ms)
    for f in seq:
        tt=int(f.get('t') or 0)
        if tt<int(t0) or tt>cutoff:continue
        q=float(f.get('incQty') or 0);p=float(f.get('price') or 0);s=str(f.get('side'));role=str(f.get('role'))
        if s=='UP':u+=q
        else:d+=q
        cost+=q*p;n+=1;side_qty[s]+=q;role_counts[role]=role_counts.get(role,0)+1
        if last is not None and s!=last:alts+=1
        last=s
    fav=(u if favored=='UP' else d)-cost;opp=(d if favored=='UP' else u)-cost;floor=min(fav,opp);best=max(fav,opp);gross=u+d;paired=2*min(u,d)/gross if gross>1e-12 else 0.0
    return {'favoredPayoff':fav,'oppositePayoff':opp,'floor':floor,'best':best,'pairedCoverage':paired,'absNet':abs(u-d),'fills':n,'alternations':alts,'sideFillQty':side_qty,'roleFillCounts':role_counts}

def key_stats(seq,key,t0):
    xs=[f for f in seq if str(f.get('key'))==str(key) and int(f.get('t') or 0)>=int(t0)]
    if not xs:return {'fillQty':0.0,'firstFillLatencyMs':None,'fillQty1s':0.0,'fillQty3s':0.0,'fillQty5s':0.0}
    return {'fillQty':sum(float(f.get('incQty') or 0) for f in xs),'firstFillLatencyMs':int(xs[0]['t'])-int(t0),'fillQty1s':sum(float(f.get('incQty') or 0) for f in xs if int(f['t'])<=int(t0)+1000),'fillQty3s':sum(float(f.get('incQty') or 0) for f in xs if int(f['t'])<=int(t0)+3000),'fillQty5s':sum(float(f.get('incQty') or 0) for f in xs if int(f['t'])<=int(t0)+5000)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--prereg',required=True);ap.add_argument('--outcomes',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    pr=json.loads(Path(a.prereg).read_text(encoding='utf-8')); frozen={int(k):v for k,v in pr['frozenTargets'].items()}; markets=[int(x) for x in pr['replicationMarkets']]
    outcomes=json.loads(Path(a.outcomes).read_text(encoding='utf-8')); outmap={int(r['marketId']):r for r in outcomes['rows']}; rows=[]
    with tempfile.TemporaryDirectory(prefix='rep12_short_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for mid in markets:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(markets,1):
            tape=root/'tapes'/f'{mid}.json.xz'; fr=frozen[mid]
            A=rep.FilledConflict(tape,mid,fr,'CONTROL')
            try:ra=A.run_probe()
            finally:A.close()
            target=ra['directionTarget']; B=rep.FilledConflict(tape,mid,fr,'TREATMENT',target)
            try:rb=B.run_probe()
            finally:B.close()
            t0=int(target['t']);fav=str(target['dominant']);ckey=str(target['controlSubmit']['key']);tev=(rb.get('directionIntervention') or {}).get('newSlotEvents') or [];tkey=str(tev[0]['key']) if tev else None
            rec={'marketId':mid,'t':t0,'favored':fav,'dominantMid':float(target['dominantMid']),'controlKey':ckey,'treatmentKey':tkey,'controlKeyStats':key_stats(ra.get('fillSideSequence',[]),ckey,t0),'treatmentKeyStats':key_stats(rb.get('fillSideSequence',[]),tkey,t0) if tkey else None,'terminalDeltaFavored':float(outmap[mid]['deltasTreatmentMinusControl']['fixedFavoredPayoff']),'terminalDeltaFloor':float(outmap[mid]['deltasTreatmentMinusControl']['terminalFloor']),'terminalDeltaFills':int(outmap[mid]['deltasTreatmentMinusControl']['fills'])}
            for h in (1000,3000,5000):
                cs=state_at(target['prefix'],ra.get('fillSideSequence',[]),t0,h,fav);ts=state_at(target['prefix'],rb.get('fillSideSequence',[]),t0,h,fav)
                rec[f'control{h//1000}s']=cs;rec[f'treatment{h//1000}s']=ts;rec[f'delta{h//1000}s']={k:ts[k]-cs[k] for k in ('favoredPayoff','oppositePayoff','floor','best','pairedCoverage','absNet','fills','alternations')}
            rows.append(rec);print(json.dumps({'progress':i,'of':len(markets),'marketId':mid,'terminalFav':rec['terminalDeltaFavored'],'controlKey':rec['controlKeyStats'],'treatmentKey':rec['treatmentKeyStats'],'d5':rec['delta5s']},ensure_ascii=False),flush=True)
    out={'version':'V3B_DEPTH_PRICE_REP12_SHORT_HORIZON_ANATOMY_V1','date':'2026-09-07','researchOnly':True,'actionAuthority':False,'markets':markets,'rows':rows,'boundary':['same frozen replication12 interventions','1/3/5s metrics reconstructed from physical fill sequence','fixed favored side pre-outcome','terminal treatment effect used only as posthoc grouping label','no NEW24-B/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'rows':len(rows)},ensure_ascii=False))
if __name__=='__main__':main()
