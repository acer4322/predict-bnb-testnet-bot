from __future__ import annotations
import hashlib,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/p0_provenance_v1/MANAGEMENT_MAINLINE_V3B_ROLE_SWITCH_FIRST_ELIGIBLE_H100_V1_20260907.json'
BUNDLE=ROOT/'data/research/r4_v0/p0_provenance_v1/v16_consumed_holdout100_bundle.zip'
RUNNER=ROOT/'tools/run_continuation_protocol_crossover_smoke4_v1.py'
V3B=ROOT/'tools/run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate.py'
ROLE=ROOT/'tools/run_management_mainline_v3b_role_switch_global_event_fork_v2.py'
H3=ROOT/'tools/run_gpt6_h3a_occupancy_suffix_first_divergence_smoke4_v1.py'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/CONTINUATION_PROTOCOL_CROSSOVER_SMOKE4_V1_PREREG_20260908.json'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
d=json.loads(SRC.read_text(encoding='utf-8')); cells={}; candidates=[]
for s in sorted(d['states'],key=lambda x:(int(x['t']),int(x['marketId']))):
    if str(s.get('nativeRole'))!='ECONOMIC_CORE' or bool(s.get('qPendingActive')):continue
    u=float(s['inventory']['UP']); dn=float(s['inventory']['DOWN'])
    inv='UP' if u>dn else ('DOWN' if dn>u else 'TIE')
    wm=0.5*(float(s['book']['weakBid'])+float(s['book']['weakAsk'])); em=0.5*(float(s['book']['expandBid'])+float(s['book']['expandAsk']))
    mids={str(s['weakSide']):wm,str(s['expandSide']):em}; md='UP' if mids['UP']>mids['DOWN'] else ('DOWN' if mids['DOWN']>mids['UP'] else 'TIE')
    if inv=='TIE' or md=='TIE':continue
    free_after=int(s['freeSlots'])-1
    cell=('ALIGNED' if inv==md else 'MISALIGNED')+'_'+('FREE' if free_after>0 else 'NO_FREE')
    z=dict(s);z.update({'researchStratum':cell,'inventoryDirection':inv,'marketMidDirection':md,'marketMidBySide':mids,'freeSlotsAfterNativeSeed':free_after,
                        'selectionChronologyKey':[int(s['t']),int(s['marketId'])]})
    candidates.append(z);cells.setdefault(cell,z)
order=['ALIGNED_FREE','ALIGNED_NO_FREE','MISALIGNED_FREE','MISALIGNED_NO_FREE']; states=[cells[c] for c in order if c in cells]; missing=[c for c in order if c not in cells]
try:head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
except Exception:head=None
out={'version':'CONTINUATION_PROTOCOL_CROSSOVER_SMOKE4_V1_PREREG_20260908','createdBeforeAnyNewFork':True,'researchOnly':True,'runtimeAuthority':False,
'hypothesis':'B1: the terminal economic ranking of the same seed reservation-recognition choice can depend on the persistent recognition protocol applied to future new ECONOMIC_CORE carriers.',
'selection':{'source':'consumed outcome-blind H100 first-eligible catalog','requirements':['nativeRole == ECONOMIC_CORE','qPendingActive == false','strict-past inventory and book only','inventory direction = larger confirmed inventory side','market-mid direction = side with larger current token midpoint','free/no-free measured after one native seed slot is admitted','earliest chronology-qualified market per cell','ties in inventory or midpoint excluded','no winner/future fills/terminal PnL/H3 clean-material label used','missing cell remains missing; no post-result backfill','no fresh/untouched/H3d-e sealed holdout'],
'cellOrder':order,'missingCells':missing,'selectedMarketIds':[int(s['marketId']) for s in states]},
'branches':{'NATIVE':'native recognition throughout','II':'seed IMMEDIATE; future new ECONOMIC_CORE IMMEDIATE','FI':'seed ON_FILL; future IMMEDIATE','IF':'seed IMMEDIATE; future ON_FILL','FF':'seed ON_FILL; future ON_FILL'},
'treatmentBoundary':['management recognition view only','native seed physical submit happens before treatment activation','no exchange slot release/payment/protection/credit/exposure cancellation/physical reservation removal','submitted/live/cancel-pending continue to occupy native physical resources','q_ladder and Active handoff remain native','no fake fill/duplicate reservation/pending-as-paid/new Active authority','no fixed seconds/window','no dream fill','max4/<=180s/venue min/fees/latency/queue unchanged'],
'correctnessGates':['common prefix parity','seed side/role/price/qty parity','seed physical admission parity','exact FIFO / base ledger invariant clean','responsibility/payment identity and confirmed-overflow conservation','no overfill','no false recognition-side accounting mutation','max4','pre-existing carriers not retroactively treated','II == NATIVE full behavior/ledger digest parity','lineage cashflow accounting closure <= 1e-7'],
'exerciseGate':'Both future-ON_FILL branches IF and FF must each suppress recognition of at least two distinct future new ECONOMIC_CORE physical carriers while those carriers remain physically reserved; repeated reads of one carrier do not count.',
'estimand':{'Y':'whole-market realistic-HFT terminal settlement PnL (winner appended posthoc only)','deltaI':'Y(F,I)-Y(I,I) = FI-II','deltaF':'Y(F,F)-Y(I,F) = FF-IF','Gamma':'deltaF-deltaI','primaryWitness':'sign reversal between deltaI and deltaF with identical seed direct fill/payment signature'},
'accountingTolerance':{'behaviorAbsolute':1e-8,'cashflowClosure':1e-7,'meaning':'numerical noise only; never a profit threshold'},
'hashes':{'sourceCatalog':sha(SRC),'bundle':sha(BUNDLE),'runner':sha(RUNNER),'v3bRuntime':sha(V3B),'roleSwitchBase':sha(ROLE),'h3TraceBase':sha(H3),'gitHead':head},
'states':states}
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'output':str(OUT.relative_to(ROOT)),'selected':[(s['researchStratum'],s['marketId'],s['t']) for s in states],'missing':missing,'hashes':out['hashes']},ensure_ascii=False,indent=2))
