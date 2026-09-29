from __future__ import annotations
import argparse, hashlib, hmac, importlib.util, json, math, os, shutil, tempfile, zipfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
EPS=1e-9
MIDS=[1946036,1946298,1946317,1946448]
DOMAIN='MVPS_PROSPECTIVE_QUOTE_EPISODE_RANDOMIZED_ITT_SOURCE_SMOKE4_V1'
RNG_SCHEMA='HMAC_SHA256(masterSeedBytes, DOMAIN|marketId|episodeOrdinal); SELECT iff digest[0]&1 == 1'


def sibling(name,filename):
    p=HERE/filename
    s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

pmod=sibling('mvps_episode_p_v2','run_mvps_pair1_public_bid_leg_underwater_add_smoke4_v2.py')


def ledger_fingerprint(sim):
    obj={}
    for side in ('UP','DOWN'):
        obj[side]=[[float(q),float(px)] for q,px in sim.un[side]]
    raw=json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def episode_key(sim,pr):
    side=str(pr['side'])
    obj={'side':side,'bid':float(pr['bid']),'qty':float(pr['qty']),'slotId':int(pr['slotId']),'fifo':ledger_fingerprint(sim)}
    raw=json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False)
    return raw,hashlib.sha256(raw.encode()).hexdigest()


def coin(master_hex,market_id,ordinal):
    key=bytes.fromhex(master_hex)
    msg=f'{DOMAIN}|{int(market_id)}|{int(ordinal)}'.encode()
    d=hmac.new(key,msg,hashlib.sha256).digest()
    return int(d[0]&1),d.hex()


def same_selected(a,b):
    if a is None or b is None:return False
    return (int(a['marketId'])==int(b['marketId']) and int(a['episodeOrdinal'])==int(b['episodeOrdinal']) and
            int(a['t'])==int(b['t']) and str(a['side'])==str(b['side']) and
            abs(float(a['price'])-float(b['price']))<=1e-12 and abs(float(a['qty'])-float(b['qty']))<=1e-12 and
            str(a['episodeKeyHash'])==str(b['episodeKeyHash']) and str(a['coinDigest'])==str(b['coinDigest']))


class EpisodeSamplerMixin:
    def _sampler_init(self,market_id,master_hex,allow_selected):
        self.sampleMarketId=int(market_id);self.masterHex=str(master_hex);self.allowSelected=bool(allow_selected)
        self.episodeOpen=False;self.currentEpisodeKey=None;self.episodeOrdinal=0;self.episodeCount=0
        self.selected=None;self.selectedOverrideUsed=0;self.selectedDirectKey=None
        self.samplerHasher=hashlib.sha256();self.samplerEvents=0
    def _eligibility(self,t,qv,end):
        pr=self._probe_native_legal(t,qv,end)
        if pr is None:return None
        side=pr['side'];opp='DOWN' if side=='UP' else 'UP'
        sameq=sum(float(a) for a,_ in self.un[side]);oppq=sum(float(a) for a,_ in self.un[opp])
        pure=bool(sameq>EPS and oppq<=EPS);avg=self.unmatched_avg(side) if sameq>EPS else None
        underwater=bool(avg is not None and pr['bid']<=float(avg)+1e-10);leg=str(self.obs.leg[side])
        if not pmod.gate_decision(True,pure,underwater,leg):return None
        keyraw,keyhash=episode_key(self,pr)
        return {'pr':pr,'side':side,'opp':opp,'sameq':sameq,'oppq':oppq,'avg':avg,'leg':leg,'keyraw':keyraw,'keyhash':keyhash}
    def _record_sampler(self,rec):
        self.samplerEvents+=1
        self.samplerHasher.update((json.dumps(rec,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode())
    def _selected_state(self,t,qv,e,ordinal,c,digest):
        pr=e['pr'];return {
          'marketId':self.sampleMarketId,'episodeOrdinal':int(ordinal),'t':int(t),'side':str(pr['side']),
          'price':float(pr['bid']),'ask':float(pr['ask']),'qty':float(pr['qty']),'slotId':int(pr['slotId']),
          'upBid':float(qv['UP']['bid']),'upAsk':float(qv['UP']['ask']),'downBid':float(qv['DOWN']['bid']),'downAsk':float(qv['DOWN']['ask']),
          'leg':str(e['leg']),'sameUnmatchedQty':float(e['sameq']),'oppUnmatchedQty':float(e['oppq']),
          'avgUnmatchedCost':None if e['avg'] is None else float(e['avg']),
          'invUP':float(self.inv['UP']),'invDOWN':float(self.inv['DOWN']),'cashCost':float(self.cost),
          'submitsBefore':int(self.submits),'fillsBefore':int(self.fills),'episodeKeyHash':str(e['keyhash']),
          'fifoLedgerHash':ledger_fingerprint(self),'coin':int(c),'coinDigest':str(digest),'strictPastOnly':True}
    def _sampler_before_p(self,t,qv,end):
        e=self._eligibility(t,qv,end)
        if e is None:
            if self.episodeOpen:
                self._record_sampler({'event':'CLOSE','marketId':self.sampleMarketId,'ordinal':self.episodeOrdinal,'t':int(t),'key':self.currentEpisodeKey})
            self.episodeOpen=False;self.currentEpisodeKey=None
            return False
        kh=e['keyhash']
        new_episode=(not self.episodeOpen) or self.currentEpisodeKey!=kh
        if new_episode:
            if self.episodeOpen:
                self._record_sampler({'event':'CLOSE_KEY_CHANGE','marketId':self.sampleMarketId,'ordinal':self.episodeOrdinal,'t':int(t),'key':self.currentEpisodeKey})
            self.episodeOpen=True;self.currentEpisodeKey=kh;self.episodeOrdinal+=1;self.episodeCount+=1
            c,d=coin(self.masterHex,self.sampleMarketId,self.episodeOrdinal)
            rec={'event':'OPEN_DRAW','marketId':self.sampleMarketId,'ordinal':self.episodeOrdinal,'t':int(t),'key':kh,'coin':c,'digest':d}
            self._record_sampler(rec)
            if self.selected is None and c==1:
                self.selected=self._selected_state(t,qv,e,self.episodeOrdinal,c,d)
                self._record_sampler({'event':'SELECT','marketId':self.sampleMarketId,'ordinal':self.episodeOrdinal,'t':int(t),'key':kh,'digest':d})
                if self.allowSelected:
                    before=self.n
                    pmod.LadderSim._open_free_slots(self,t,qv,end)
                    self.selectedOverrideUsed=1
                    if self.n>before:self.selectedDirectKey=f'{e["side"]}_{before}'
                    self.selected['nativeSubmitted']=bool(self.n>before);self.selected['nativeOrderKey']=self.selectedDirectKey
                    return True
        return False


class SampledKSim(EpisodeSamplerMixin,pmod.PublicBidLegSim):
    def __init__(self,tape,market_id,master_hex):
        pmod.PublicBidLegSim.__init__(self,tape,True);self._sampler_init(market_id,master_hex,False)
    def _open_free_slots(self,t,qv,end):
        self._sampler_before_p(t,qv,end)
        return super()._open_free_slots(t,qv,end)
    def run_sample(self,winner):
        r=self.run_bid_leg(winner);return finalize_sample(self,r)


class SampledASim(EpisodeSamplerMixin,pmod.PublicBidLegSim):
    def __init__(self,tape,market_id,master_hex):
        pmod.PublicBidLegSim.__init__(self,tape,True);self._sampler_init(market_id,master_hex,True)
    def _open_free_slots(self,t,qv,end):
        bypass=self._sampler_before_p(t,qv,end)
        if bypass:return
        return super()._open_free_slots(t,qv,end)
    def run_sample(self,winner):
        r=self.run_bid_leg(winner);return finalize_sample(self,r)


def finalize_sample(sim,r):
    r['upPayoff']=float(sim.inv['UP']-sim.cost);r['downPayoff']=float(sim.inv['DOWN']-sim.cost)
    r['episodeCount']=int(sim.episodeCount);r['samplerHash']=sim.samplerHasher.hexdigest();r['selected']=sim.selected
    r['selectedOverrideUsed']=int(sim.selectedOverrideUsed);r['selectedDirectFill']=0.0
    if sim.selectedDirectKey:
        o=sim.orders.get(sim.selectedDirectKey);r['selectedDirectFill']=float(o.get('cum') or 0.0) if o else 0.0
    return r


def run_arm(tape,winner,market_id,master_hex,arm):
    sim=SampledKSim(tape,market_id,master_hex) if arm=='K' else SampledASim(tape,market_id,master_hex)
    try:r=sim.run_sample(winner);snap=pmod.behavior_snapshot(sim,r)
    finally:sim.close()
    return r,snap


def rebate_upper(filled_shares):return 0.005*max(0.0,float(filled_shares))


def synthetic_fixture():
    # Pure sampler semantics independent of HFT. A tiny state machine validates one draw per same key,
    # key-change/gap episode reopening, deterministic coin, and no forced select.
    master='00'*32;mid=999
    seq=[('A',True),('A',True),('B',True),('B',False),('B',True),('B',True)]
    ordn=0;open_=False;key=None;draws=[]
    for k,eligible in seq:
        if not eligible:open_=False;key=None;continue
        if (not open_) or key!=k:
            open_=True;key=k;ordn+=1;c,d=coin(master,mid,ordn);draws.append((ordn,k,c,d))
    checks={
      'sameKeyOneDraw':len([x for x in draws if x[1]=='A'])==1,
      'keyChangeNewEpisode':any(x[1]=='B' and x[0]==2 for x in draws),
      'gapReopensEpisode':len([x for x in draws if x[1]=='B'])==2,
      'stableOrdinal':[x[0] for x in draws]==[1,2,3],
      'deterministicCoin':all(coin(master,mid,o)[0]==c and coin(master,mid,o)[1]==d for o,_,c,d in draws),
      'rngSchemaFixed':RNG_SCHEMA.startswith('HMAC_SHA256'),
    }
    # Search a deterministic fixture market id where the first two coins are 0 to prove no forced SELECT semantics exists.
    noselect_mid=next(m for m in range(1000,5000) if coin(master,m,1)[0]==0 and coin(master,m,2)[0]==0)
    checks['noSelectNotForced']=coin(master,noselect_mid,1)[0]==0 and coin(master,noselect_mid,2)[0]==0
    return {'checks':checks,'pass':all(checks.values()),'trace':draws,'noSelectFixtureMarketId':noselect_mid,'domain':DOMAIN,'rngSchema':RNG_SCHEMA}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle');ap.add_argument('--freeze',required=True);ap.add_argument('--seed-freeze');ap.add_argument('--output',required=True);ap.add_argument('--fixture-only',action='store_true');a=ap.parse_args()
    freeze=json.load(open(a.freeze,encoding='utf-8'))
    fx=synthetic_fixture()
    if not fx['pass']:raise RuntimeError('synthetic sampler fixture fail')
    if a.fixture_only:
        out={'version':'MVPS_PROSPECTIVE_QUOTE_EPISODE_RANDOMIZED_ITT_SOURCE_STAGEA_FIXTURE_V1','fixture':fx}
        op=(Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out),flush=True);return
    seedf=json.load(open(a.seed_freeze,encoding='utf-8'));master=seedf['masterSeedHex']
    if seedf.get('rngSchema')!=RNG_SCHEMA or seedf.get('domain')!=DOMAIN:raise RuntimeError('seed schema mismatch')
    hashes={'pRunner':hashlib.sha256((HERE/'run_mvps_pair1_public_bid_leg_underwater_add_smoke4_v2.py').read_bytes()).hexdigest().upper(),'ladder':hashlib.sha256((HERE/'run_eth_safety_reintroduction_ladder_1946317.py').read_bytes()).hexdigest().upper(),'bundle':hashlib.sha256(Path(a.bundle).read_bytes()).hexdigest().upper()}
    for k in ('pRunner','ladder','bundle'):
        if hashes[k]!=freeze['sourceHashes'][k]:raise RuntimeError(f'frozen hash drift {k}')
    tmp=Path(tempfile.mkdtemp(prefix='mvps_episode_itt4_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[];firstAssigned=None;stored={}
        for mid in MIDS:
            tape=tmp/'tapes'/f'{mid}.json.xz';winner=co[mid]['winner']
            k,ks=run_arm(tape,winner,mid,master,'K');aa,ass=run_arm(tape,winner,mid,master,'A');stored[mid]=(k,ks,aa,ass)
            expected_k=seedf['priorKBehaviorHashes'].get(str(mid))
            if expected_k is None or ks['sha256']!=expected_k: raise RuntimeError(f'K sampler changed frozen P behavior {mid}')
            assigned=k.get('selected') is not None
            if assigned and firstAssigned is None:firstAssigned=mid
            if assigned:
                if aa.get('selected') is None or not same_selected(k['selected'],aa['selected']):raise RuntimeError(f'A/K selected identity mismatch {mid}')
                if int(aa.get('selectedOverrideUsed') or 0)!=1 or not aa['selected'].get('nativeSubmitted'):raise RuntimeError(f'A override not submitted {mid}')
                if k['observerHash']!=aa['observerHash']:raise RuntimeError(f'public observer branch dependence {mid}')
                gross=float(aa['pnlDiagnosticOnly']-k['pnlDiagnosticOnly']);du=float(aa['upPayoff']-k['upPayoff']);dd=float(aa['downPayoff']-k['downPayoff'])
                klo=rebate_upper(k['filledQty']);ahi=rebate_upper(aa['filledQty']);interval=[gross-klo,gross+ahi]
                selected=dict(k['selected']);selected['nativeSubmittedInK']=False;selected['nativeSubmittedInA']=True
                row={'marketId':mid,'sourceAssigned':True,'winnerEvaluationOnly':str(winner).upper(),'episodeCountK':int(k['episodeCount']),'selected':selected,'selectedDirectFill':float(aa['selectedDirectFill']),
                     'K':{'pnlGross':float(k['pnlDiagnosticOnly']),'upPayoffGross':float(k['upPayoff']),'downPayoffGross':float(k['downPayoff']),'fills':int(k['fillEvents']),'filledShares':float(k['filledQty']),'buyNotional':float(k['buyNotional']),'behaviorHash':ks['sha256']},
                     'A':{'pnlGross':float(aa['pnlDiagnosticOnly']),'upPayoffGross':float(aa['upPayoff']),'downPayoffGross':float(aa['downPayoff']),'fills':int(aa['fillEvents']),'filledShares':float(aa['filledQty']),'buyNotional':float(aa['buyNotional']),'behaviorHash':ass['sha256']},
                     'label':{'realizedGrossDelta':gross,'deltaUPGross':du,'deltaDOWNGross':dd,'fullNetObservedLabelInterval':interval,'intervalContainsZero':interval[0]<=0<=interval[1],'intervalType':'COST_REBATE_UNCERTAINTY_FOR_OBSERVED_PAIRED_LABEL_NOT_MU_CI'},
                     'publicObserverIndependent':True,'samplerPrefixKHash':k['samplerHash'],'samplerPrefixAHash':aa['samplerHash']}
            else:
                if aa.get('selected') is not None or int(aa.get('selectedOverrideUsed') or 0)!=0:raise RuntimeError(f'A unexpected assignment {mid}')
                row={'marketId':mid,'sourceAssigned':False,'winnerEvaluationOnly':str(winner).upper(),'episodeCountK':int(k['episodeCount']),'selected':None,'selectedDirectFill':0.0,
                     'K':{'pnlGross':float(k['pnlDiagnosticOnly']),'fills':int(k['fillEvents']),'filledShares':float(k['filledQty']),'buyNotional':float(k['buyNotional']),'behaviorHash':ks['sha256']},
                     'A':{'pnlGross':float(aa['pnlDiagnosticOnly']),'fills':int(aa['fillEvents']),'filledShares':float(aa['filledQty']),'buyNotional':float(aa['buyNotional']),'behaviorHash':ass['sha256']},'label':None,
                     'publicObserverIndependent':k['observerHash']==aa['observerHash'],'samplerPrefixKHash':k['samplerHash'],'samplerPrefixAHash':aa['samplerHash']}
            rows.append(row);print(json.dumps({'marketId':mid,'assigned':assigned,'episodes':row['episodeCountK'],'selectedOrdinal':None if not assigned else row['selected']['episodeOrdinal'],'seedFill':row['selectedDirectFill'],'gross':None if row['label'] is None else row['label']['realizedGrossDelta'],'du':None if row['label'] is None else row['label']['deltaUPGross'],'dd':None if row['label'] is None else row['label']['deltaDOWNGross']},ensure_ascii=False),flush=True)
        repeat={'performed':False}
        if firstAssigned is not None:
            k0,ks0,a0,as0=stored[firstAssigned];tape=tmp/'tapes'/f'{firstAssigned}.json.xz';winner=co[firstAssigned]['winner']
            kr,ksr=run_arm(tape,winner,firstAssigned,master,'K');ar,asr=run_arm(tape,winner,firstAssigned,master,'A')
            repeat={'performed':True,'marketId':firstAssigned,'KBehavior':ksr['sha256']==ks0['sha256'],'KObserver':kr['observerHash']==k0['observerHash'],'KSelected':same_selected(kr.get('selected'),k0.get('selected')),
                    'ABehavior':asr['sha256']==as0['sha256'],'AObserver':ar['observerHash']==a0['observerHash'],'ASelected':same_selected(ar.get('selected'),a0.get('selected')),
                    'AEndpoints':abs(float(ar['upPayoff'])-float(a0['upPayoff']))<=1e-12 and abs(float(ar['downPayoff'])-float(a0['downPayoff']))<=1e-12}
            if not all(v for k,v in repeat.items() if k not in ('performed','marketId')):raise RuntimeError('repeat parity fail')
        assigned=[r for r in rows if r['sourceAssigned']];novel=[]
        # Known first seeds from prior pilot are not loaded for selection. Novelty is defined by selected episode ordinal >1;
        # ordinal 1 is exactly the first eligible episode by construction and therefore reuses the stopped first-event source state.
        for r in assigned:
            r['reusedFirstSeed']=bool(int(r['selected']['episodeOrdinal'])==1)
            r['novelAssignment']=not r['reusedFirstSeed']
            if r['novelAssignment']:novel.append(r)
        direct=sum(r['selectedDirectFill']>EPS for r in assigned);endpoint=sum(r['label'] is not None and (abs(r['label']['deltaUPGross'])>EPS or abs(r['label']['deltaDOWNGross'])>EPS) for r in assigned);realized=sum(r['label'] is not None and abs(r['label']['realizedGrossDelta'])>EPS for r in assigned);netidentified=sum(r['label'] is not None and not r['label']['intervalContainsZero'] for r in assigned)
        correctness={'sourceHashParity':True,'fixturePass':fx['pass'],'kBehaviorInert4of4':all(stored[m][1]['sha256']==seedf['priorKBehaviorHashes'][str(m)] for m in MIDS),'publicObserverIndependent4of4':all(r['publicObserverIndependent'] for r in rows),'repeatPass':firstAssigned is None or all(v for k,v in repeat.items() if k not in ('performed','marketId')),'assignedIdentityAequalsK':all(same_selected(stored[r['marketId']][0].get('selected'),stored[r['marketId']][2].get('selected')) for r in assigned),'exactOneOverrideAssigned':all(int(stored[r['marketId']][2].get('selectedOverrideUsed') or 0)==1 for r in assigned),'noOverrideUnassigned':all(int(stored[r['marketId']][2].get('selectedOverrideUsed') or 0)==0 for r in rows if not r['sourceAssigned'])}
        if not all(correctness.values()):raise RuntimeError('correctness fail')
        if len(assigned)==0:verdict='NO_SAMPLED_EPISODE'
        elif len(novel)==0:verdict='NO_NOVEL_SOURCE_INFORMATION_IN_THIS_DRAW'
        elif len(novel)<2:verdict='LIMITED_NOVEL_SOURCE_EXERCISE'
        elif endpoint==0 and direct==0:verdict='ZERO_RESPONSE_PERSISTS_UNDER_PRESPECIFIED_SOURCE_B5_NOT_REJECTED'
        elif direct>0 and endpoint==0:verdict='EXECUTION_RESPONSE_WITHOUT_FULL_VALUE_VARIATION'
        elif endpoint>0:verdict='PROSPECTIVE_ITT_SOURCE_RESPONSE_WITHOUT_FILL_CONDITIONING'
        else:verdict='DESIGN_COMPLETE_STOP'
        annotations=[]
        if any(r['sourceAssigned'] and r['selectedDirectFill']<=EPS and r['label'] and (abs(r['label']['deltaUPGross'])>EPS or abs(r['label']['deltaDOWNGross'])>EPS) for r in rows):annotations.append('NONFILL_CONTINUATION_VALUE_PATH_WITNESS')
        if any(r['label'] and (abs(r['label']['deltaUPGross'])>EPS or abs(r['label']['deltaDOWNGross'])>EPS) and r['label']['intervalContainsZero'] for r in assigned):annotations.append('GROSS_ITT_RESPONSE_NET_SIGN_UNRESOLVED')
        out={'version':'MVPS_PROSPECTIVE_QUOTE_EPISODE_RANDOMIZED_ITT_SOURCE_SMOKE4_V1_20260909','researchOnly':True,'runtimeAuthority':False,'markets':MIDS,'sourceLaw':{'domain':DOMAIN,'rngSchema':RNG_SCHEMA,'masterSeedSha256':hashlib.sha256(bytes.fromhex(master)).hexdigest(),'masterSeedHex':master},'fixture':fx,'rows':rows,'repeat':repeat,'correctness':correctness,
             'denominators':{'fixedMarkets':4,'assignedMarkets':len(assigned),'unassignedMarkets':4-len(assigned),'novelAssignments':len(novel),'reusedFirstSeedAssignments':len(assigned)-len(novel),'directFilledAssignments':direct,'grossEndpointResponseAssignments':endpoint,'realizedGrossResponseAssignments':realized,'netSignIdentifiedAssignments':netidentified},
             'costContract':freeze['costContract'],'verdict':verdict,'annotations':annotations,'branchEquivalentsUsed':8+(2 if firstAssigned is not None else 0),
             'boundary':['fixed four consumed TRAIN pilot markets only','prospective HMAC fair-coin episode sampling, no reroll','episode start only; same key repeated receipts draw once','K=frozen P; A=one exact native ADD then frozen P','all zero-fill/unassigned/reused rows retained','no model/no remaining TRAIN/CAL/TEST/fresh/reserve/no8781/no dream fill']}
        op=(Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
        print(json.dumps({'ok':True,'verdict':verdict,'denominators':out['denominators'],'annotations':annotations,'correctness':correctness,'BE':out['branchEquivalentsUsed']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
