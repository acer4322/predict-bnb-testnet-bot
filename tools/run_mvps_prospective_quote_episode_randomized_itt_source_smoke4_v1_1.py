from __future__ import annotations
import argparse, hashlib, importlib.util, json, os, tempfile, shutil, zipfile
from pathlib import Path
HERE=Path(__file__).resolve().parent

def load(name,fn):
    p=HERE/fn; s=importlib.util.spec_from_file_location(name,p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
src=load('src_v1','run_mvps_prospective_quote_episode_randomized_itt_source_smoke4_v1.py')
MIDS=src.MIDS; EPS=src.EPS

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--stage0-freeze',required=True); ap.add_argument('--schema-freeze',required=True); ap.add_argument('--seed-freeze',required=True); ap.add_argument('--prior-result',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    st0=json.load(open(a.stage0_freeze,encoding='utf-8')); sch=json.load(open(a.schema_freeze,encoding='utf-8')); seed=json.load(open(a.seed_freeze,encoding='utf-8')); prior=json.load(open(a.prior_result,encoding='utf-8'))
    if st0['pilotTrainMarkets']!=MIDS or sch['pilotTrainMarkets']!=MIDS: raise RuntimeError('market freeze mismatch')
    sampler_sha=hashlib.sha256((HERE/'run_mvps_prospective_quote_episode_randomized_itt_source_smoke4_v1.py').read_bytes()).hexdigest().upper()
    if sampler_sha!=sch['sourceHashes']['runner']: raise RuntimeError('frozen sampler drift')
    if hashlib.sha256(Path(a.prior_result).read_bytes()).hexdigest().upper()!=sch['sourceHashes']['priorFirstEventResult']: raise RuntimeError('prior result drift')
    if seed['domain']!=sch['domain'] or seed['rngSchema']!=sch['rngSchema']: raise RuntimeError('seed/schema mismatch')
    prior_by={int(r['marketId']):r for r in prior['rows']}; master=seed['masterSeedHex']
    def first_select_ordinal(mid):
        j=1
        while j<=256:
            if src.coin(master,mid,j)[0]==1:return j
            j+=1
        return None
    tmp=Path(tempfile.mkdtemp(prefix='mvps_episode_itt_v11_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[]; fresh={}; fresh_be=0; first_assigned=None
        for mid in MIDS:
            ord0=first_select_ordinal(mid); pr=prior_by[mid]
            if ord0==1:
                if not pr['eligibleTarget']: raise RuntimeError(f'cached ordinal1 missing first target {mid}')
                sd=dict(pr['strictPastState']); c,d=src.coin(master,mid,1); sd.update({'marketId':mid,'episodeOrdinal':1,'episodeKeyHash':'REUSED_FIRST_EVENT_SOURCE','coin':c,'coinDigest':d,'nativeSubmittedInK':False,'nativeSubmittedInA':True})
                row={'marketId':mid,'sourceAssigned':True,'plannedOrdinal':1,'sourceResolution':'CACHED_EXACT_FIRST_EVENT_PAIR','reusedFirstSeed':True,'novelAssignment':False,'selected':sd,'selectedDirectFill':float(pr['seedDirectFill']),
                     'K':{'pnlGross':float(pr['K']['realizedPnlGross']),'upPayoffGross':float(pr['K']['upPayoffGross']),'downPayoffGross':float(pr['K']['downPayoffGross']),'fills':int(pr['K']['fills']),'filledShares':float(pr['K']['filledShares']),'buyNotional':float(pr['K']['buyNotional']),'behaviorHash':pr['K']['behaviorHash']},
                     'A':{'pnlGross':float(pr['A']['realizedPnlGross']),'upPayoffGross':float(pr['A']['upPayoffGross']),'downPayoffGross':float(pr['A']['downPayoffGross']),'fills':int(pr['A']['fills']),'filledShares':float(pr['A']['filledShares']),'buyNotional':float(pr['A']['buyNotional']),'behaviorHash':pr['A']['behaviorHash']},
                     'label':pr['label'],'publicObserverIndependent':bool(pr['observerPolicyIndependent']),'freshBE':0}
                rows.append(row); first_assigned = first_assigned or mid
                print(json.dumps({'marketId':mid,'assigned':True,'plannedOrdinal':1,'cached':True,'seedFill':row['selectedDirectFill'],'gross':row['label']['realizedGrossDelta']},ensure_ascii=False),flush=True); continue
            tape=tmp/'tapes'/f'{mid}.json.xz'; winner=co[mid]['winner']
            k,ks=src.run_arm(tape,winner,mid,master,'K'); fresh_be+=1
            if ks['sha256']!=sch['priorKBehaviorHashes'][str(mid)]: raise RuntimeError(f'K behavior changed {mid}')
            assigned=k.get('selected') is not None
            if assigned:
                aa,ass=src.run_arm(tape,winner,mid,master,'A'); fresh_be+=1; fresh[mid]=(k,ks,aa,ass)
                if not src.same_selected(k['selected'],aa['selected']): raise RuntimeError(f'A/K selected mismatch {mid}')
                if k['observerHash']!=aa['observerHash']: raise RuntimeError(f'public observer mismatch {mid}')
                gross=float(aa['pnlDiagnosticOnly']-k['pnlDiagnosticOnly']); du=float(aa['upPayoff']-k['upPayoff']); dd=float(aa['downPayoff']-k['downPayoff']); interval=[gross-src.rebate_upper(k['filledQty']),gross+src.rebate_upper(aa['filledQty'])]
                row={'marketId':mid,'sourceAssigned':True,'plannedOrdinal':ord0,'sourceResolution':'FRESH_ONLINE_EPISODE_SAMPLER','reusedFirstSeed':False,'novelAssignment':True,'episodeCountK':int(k['episodeCount']),'selected':k['selected'],'selectedDirectFill':float(aa['selectedDirectFill']),
                     'K':{'pnlGross':float(k['pnlDiagnosticOnly']),'upPayoffGross':float(k['upPayoff']),'downPayoffGross':float(k['downPayoff']),'fills':int(k['fillEvents']),'filledShares':float(k['filledQty']),'buyNotional':float(k['buyNotional']),'behaviorHash':ks['sha256']},
                     'A':{'pnlGross':float(aa['pnlDiagnosticOnly']),'upPayoffGross':float(aa['upPayoff']),'downPayoffGross':float(aa['downPayoff']),'fills':int(aa['fillEvents']),'filledShares':float(aa['filledQty']),'buyNotional':float(aa['buyNotional']),'behaviorHash':ass['sha256']},
                     'label':{'realizedGrossDelta':gross,'deltaUPGross':du,'deltaDOWNGross':dd,'fullNetObservedLabelInterval':interval,'intervalContainsZero':interval[0]<=0<=interval[1],'intervalType':'COST_REBATE_UNCERTAINTY_FOR_OBSERVED_PAIRED_LABEL_NOT_MU_CI'},'publicObserverIndependent':True,'freshBE':2}
                first_assigned=first_assigned or mid
            else:
                row={'marketId':mid,'sourceAssigned':False,'plannedOrdinal':ord0,'sourceResolution':'NO_SAMPLED_EPISODE_BEFORE_HORIZON','reusedFirstSeed':False,'novelAssignment':False,'episodeCountK':int(k['episodeCount']),'selected':None,'selectedDirectFill':0.0,
                     'K':{'pnlGross':float(k['pnlDiagnosticOnly']),'fills':int(k['fillEvents']),'filledShares':float(k['filledQty']),'buyNotional':float(k['buyNotional']),'behaviorHash':ks['sha256']},'A':None,'label':None,'publicObserverIndependent':True,'freshBE':1}
            rows.append(row); print(json.dumps({'marketId':mid,'assigned':assigned,'plannedOrdinal':ord0,'cached':False,'episodes':row.get('episodeCountK'),'seedFill':row['selectedDirectFill'],'gross':None if row['label'] is None else row['label']['realizedGrossDelta']},ensure_ascii=False),flush=True)
        def selected_match(base, cur):
            if base is None or cur is None: return False
            if base.get('episodeKeyHash')=='REUSED_FIRST_EVENT_SOURCE':
                return int(base['episodeOrdinal'])==int(cur['episodeOrdinal']) and int(base['t'])==int(cur['t']) and str(base['side'])==str(cur['side']) and abs(float(base['price'])-float(cur['price']))<=1e-12 and abs(float(base['qty'])-float(cur['qty']))<=1e-12 and str(base['coinDigest'])==str(cur['coinDigest'])
            return src.same_selected(base,cur)
        repeat={'performed':False}
        if first_assigned is not None:
            if 2+fresh_be+2>10: raise RuntimeError('repeat would exceed cumulative BE ceiling')
            tape=tmp/'tapes'/f'{first_assigned}.json.xz'; winner=co[first_assigned]['winner']; kr,ksr=src.run_arm(tape,winner,first_assigned,master,'K'); ar,asr=src.run_arm(tape,winner,first_assigned,master,'A'); fresh_be+=2
            base=next(r for r in rows if r['marketId']==first_assigned)
            repeat={'performed':True,'marketId':first_assigned,'KBehavior':ksr['sha256']==base['K']['behaviorHash'],'KSelected':base['sourceAssigned'] and selected_match(base.get('selected'),kr.get('selected')),
                    'ABehavior':base['A'] is not None and asr['sha256']==base['A']['behaviorHash'],'ASelected':base['sourceAssigned'] and selected_match(base.get('selected'),ar.get('selected')),
                    'AEndpoints':base['A'] is not None and abs(float(ar['upPayoff'])-float(base['A']['upPayoffGross']))<=1e-12 and abs(float(ar['downPayoff'])-float(base['A']['downPayoffGross']))<=1e-12}
            if not all(v for k,v in repeat.items() if k not in ('performed','marketId')): raise RuntimeError('repeat fail')
        cumulative=2+fresh_be
        if cumulative>10: raise RuntimeError('cumulative BE exceeded')
        assigned=[r for r in rows if r['sourceAssigned']]; novel=[r for r in assigned if r['novelAssignment']]; direct=sum(r['selectedDirectFill']>EPS for r in assigned); endpoint=sum(r['label'] and (abs(r['label']['deltaUPGross'])>EPS or abs(r['label']['deltaDOWNGross'])>EPS) for r in assigned); realized=sum(r['label'] and abs(r['label']['realizedGrossDelta'])>EPS for r in assigned); netid=sum(r['label'] and not r['label']['intervalContainsZero'] for r in assigned)
        if len(assigned)==0: verdict='NO_SAMPLED_EPISODE'
        elif len(novel)==0: verdict='NO_NOVEL_SOURCE_INFORMATION_IN_THIS_DRAW'
        elif len(novel)<2: verdict='LIMITED_NOVEL_SOURCE_EXERCISE'
        elif direct==0 and endpoint==0: verdict='ZERO_RESPONSE_PERSISTS_UNDER_PRESPECIFIED_SOURCE_B5_NOT_REJECTED'
        elif direct>0 and endpoint==0: verdict='EXECUTION_RESPONSE_WITHOUT_FULL_VALUE_VARIATION'
        else: verdict='PROSPECTIVE_ITT_SOURCE_RESPONSE_WITHOUT_FILL_CONDITIONING'
        ann=[]
        if any(r['sourceAssigned'] and r['selectedDirectFill']<=EPS and r['label'] and (abs(r['label']['deltaUPGross'])>EPS or abs(r['label']['deltaDOWNGross'])>EPS) for r in rows): ann.append('NONFILL_CONTINUATION_VALUE_PATH_WITNESS')
        if any(r['label'] and (abs(r['label']['deltaUPGross'])>EPS or abs(r['label']['deltaDOWNGross'])>EPS) and r['label']['intervalContainsZero'] for r in assigned): ann.append('GROSS_ITT_RESPONSE_NET_SIGN_UNRESOLVED')
        correctness={'samplerHashFrozen':True,'kBehaviorInertAllFresh':all((not r['novelAssignment']) or r['K']['behaviorHash']==sch['priorKBehaviorHashes'][str(r['marketId'])] for r in rows),'publicObserverIndependent4of4':all(r['publicObserverIndependent'] for r in rows),'repeatPass':repeat['performed'] and all(v for k,v in repeat.items() if k not in ('performed','marketId'))}
        out={'version':'MVPS_PROSPECTIVE_QUOTE_EPISODE_RANDOMIZED_ITT_SOURCE_SMOKE4_V1_1_20260909','researchOnly':True,'runtimeAuthority':False,'rows':rows,'repeat':repeat,'correctness':correctness,'denominators':{'fixedMarkets':4,'assignedMarkets':len(assigned),'unassignedMarkets':4-len(assigned),'novelAssignments':len(novel),'reusedFirstSeedAssignments':len(assigned)-len(novel),'directFilledAssignments':direct,'grossEndpointResponseAssignments':endpoint,'realizedGrossResponseAssignments':realized,'netSignIdentifiedAssignments':netid},'verdict':verdict,'annotations':ann,'failedAttemptConsumedBE':2,'freshBEThisCorrectedRun':fresh_be,'cumulativeBE':cumulative,'hardCeiling':10,'seedSha256':seed['masterSeedSha256'],'noReroll':True,'modelBudget':0}
        op=(Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json') if a.output.upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8'); print(json.dumps({'ok':True,'verdict':verdict,'denominators':out['denominators'],'annotations':ann,'BE':{'failed':2,'fresh':fresh_be,'cumulative':cumulative}},ensure_ascii=False),flush=True)
    finally: shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
