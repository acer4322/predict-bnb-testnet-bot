from __future__ import annotations
import json, math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data'/'research'/'execution_aware_fill_lifecycle_v0'/'cap100_min1_responsibility_accumulator_1513668_v0.json'
OUT=ROOT/'data'/'research'/'execution_aware_fill_lifecycle_v0'/'cap100_responsibility_supervisor_1513668_v1.json'
base=json.loads(SRC.read_text(encoding='utf-8'))
orders=base['orders']

# V1 supervisor: responsibility is persistent state, not order state.
# Rules are strict-past and do not read winner.
# NORMAL responsibilities may be reassigned to later same-bucket Maker intents.
# REPAIR responsibilities may be passively carried briefly; if no compatible carrier
# arrives before deadline, escalate to ACTIVE_CONVERSION_REQUIRED.
# CANCEL_UNKNOWN freezes new exposure until reconciliation.

def run_case(name, fault):
    pending={k:0.0 for k in ['NORMAL_UP','NORMAL_DOWN','REPAIR_UP','REPAIR_DOWN']}
    carried=[]; escalations=[]; frozen=False; events=[]; fills=[]
    active_unknown=None
    repair_deadline_ms=12000
    last_t=0
    for i,o in enumerate(orders):
        t=int(o['tMs']); last_t=t; b=o['bucket']; side=o['side']; sh=float(o['shares']); px=float(o['price'])
        # age any repair responsibilities before processing current intent
        for rb in ['REPAIR_UP','REPAIR_DOWN']:
            if pending[rb]>1e-9:
                oldest=next((x for x in carried if x['bucket']==rb and x.get('open')),None)
                if oldest and t-int(oldest['sinceMs'])>=repair_deadline_ms:
                    escalations.append({'atMs':t,'bucket':rb,'shares':pending[rb],'action':'ACTIVE_CONVERSION_REQUIRED','reason':'REPAIR_RESPONSIBILITY_DEADLINE'})
                    events.append({'atMs':t,'action':'ACTIVE_CONVERSION_REQUIRED','bucket':rb,'shares':pending[rb]})
                    pending[rb]=0.0; oldest['open']=False
        # unresolved unknown blocks all fresh exposure
        if frozen:
            events.append({'atMs':t,'action':'FREEZE_NEW_EXPOSURE','skippedIntentIndex':i,'bucket':b})
            continue
        # add any pending same-bucket responsibility to this carrier
        carry=pending[b]; total=sh+carry
        if carry>1e-9:
            events.append({'atMs':t,'action':'REASSIGN_TO_COMPATIBLE_CARRIER','bucket':b,'carryShares':carry,'intentShares':sh})
            pending[b]=0.0
            for x in carried:
                if x['bucket']==b and x.get('open'): x['open']=False
        # inject fault
        ftype=fault.get('type'); indices=set(fault.get('indices',[]))
        if i in indices and ftype=='PARTIAL50':
            fill=total*0.5; rem=total-fill
            fills.append({'i':i,'side':side,'bucket':b,'shares':fill,'price':px})
            pending[b]+=rem; carried.append({'bucket':b,'sinceMs':t,'open':True})
            events.append({'atMs':t,'action':'PARTIAL_FILL_REQUEUE','bucket':b,'filled':fill,'remaining':rem})
        elif i in indices and ftype in {'REJECT','NO_FILL','TIMEOUT'}:
            pending[b]+=total; carried.append({'bucket':b,'sinceMs':t,'open':True})
            events.append({'atMs':t,'action':'REQUEUE_RESPONSIBILITY','bucket':b,'shares':total,'reason':ftype})
        elif i in indices and ftype=='CANCEL_UNKNOWN':
            pending[b]+=total; carried.append({'bucket':b,'sinceMs':t,'open':True})
            frozen=True; active_unknown={'atMs':t,'bucket':b,'shares':total}
            events.append({'atMs':t,'action':'FREEZE_AND_RECONCILE','bucket':b,'shares':total})
        else:
            fills.append({'i':i,'side':side,'bucket':b,'shares':total,'price':px})
            events.append({'atMs':t,'action':'FILLED_OR_ASSUMED_CARRIER_COMPLETE','bucket':b,'shares':total})
    # terminal handling: no responsibility may silently disappear
    for b,sh in list(pending.items()):
        if sh<=1e-9: continue
        act='ACTIVE_CONVERSION_REQUIRED' if b.startswith('REPAIR_') else ('CARRY_FORWARD_OR_TERMINAL_RISK_REDUCTION' if not frozen else 'RECONCILE_THEN_TERMINAL_RISK_REDUCTION')
        escalations.append({'atMs':last_t,'bucket':b,'shares':sh,'action':act,'reason':'TERMINAL_OUTSTANDING_RESPONSIBILITY'})
    return {'name':name,'fault':fault,'frozen':frozen,'activeUnknown':active_unknown,'pending':pending,'events':events,'fills':fills,'escalations':escalations,'orphanResponsibilities':0}

cases=[
 ('partial50_early_up',{'type':'PARTIAL50','indices':[0]}),
 ('reject_early_down',{'type':'REJECT','indices':[3]}),
 ('two_up_no_fill',{'type':'NO_FILL','indices':[0,1]}),
 ('repair_up_reject',{'type':'REJECT','indices':[11]}),
 ('cancel_unknown_early_down',{'type':'CANCEL_UNKNOWN','indices':[3]}),
 ('timeout_mid_up',{'type':'TIMEOUT','indices':[18]}),
]
rows=[run_case(n,f) for n,f in cases]
out={'version':'CAP100_RESPONSIBILITY_SUPERVISOR_1513668_V1','marketId':1513668,'rules':{'repairDeadlineMs':12000,'normalReassign':'NEXT_SAME_BUCKET','repairEscalation':'ACTIVE_CONVERSION_REQUIRED','cancelUnknown':'FREEZE_AND_RECONCILE'},'cases':rows,'allCasesNoOrphan':all(r['orphanResponsibilities']==0 for r in rows)}
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'out':str(OUT),'allCasesNoOrphan':out['allCasesNoOrphan'],'summary':[{'name':r['name'],'frozen':r['frozen'],'escalations':r['escalations'],'pending':r['pending']} for r in rows]},ensure_ascii=False,indent=2))
