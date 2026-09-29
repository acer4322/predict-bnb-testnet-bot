from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_39_overflow_responsibility_handoff as r239
from tools.run_lane_g_r239_confirmed_repair_role_payment_binding_smoke1_v1 import RepairRoleBinding,term,near
EPS=1e-9
MARKETS={
  1945869:{
    'ownerId':1,'expectedOutstanding':0.16174796329858765,
    'receipts':[
      (1788532867144,'DOWN_20','SATELLITE_REPAIR',1.2048192771084338),
      (1788532867755,'DOWN_22','SATELLITE_REPAIR',1.2048192771084338),
      (1788532874726,'DOWN_24','SATELLITE_REPAIR',1.1627906976744187),
    ]},
  1946872:{
    'ownerId':1,'expectedOutstanding':0.06837330749145343,
    'receipts':[(1788538900096,'UP_10','SATELLITE_REPAIR',1.1494252873563218)]},
}

def run(tape,winner,cand):
    s=RepairRoleBinding(tape) if cand else r239.OverflowResponsibilityHandoffSim(tape,1,4)
    try:
        raw=s.run_r239(winner)
        return {'branch':'CONFIRMED_REPAIR_ROLE_BINDING' if cand else 'CURRENT_R239','terminal':term(raw),
                'obligations':[dict(x) for x in s.obligations],'events':[dict(x) for x in s.r239events],
                'bindEvents':[dict(x) for x in getattr(s,'bindEvents',[])],
                'unauthorizedOverflowQty':float(raw.get('unauthorizedOverflowQty') or 0.0),
                'repairQuotaExcessMax':float(raw.get('repairQuotaExcessMax') or 0.0)}
    finally:s.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    tmp=Path(tempfile.mkdtemp(prefix='lane_g_r239_sat_smoke2_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in MARKETS:
                (tmp/f'{mid}.json.xz').write_bytes(z.read(f'tapes/{mid}.json.xz'))
        rows=[];all_receipts=[];market_gates=[]
        for mid,spec in MARKETS.items():
            tape=tmp/f'{mid}.json.xz';b=run(tape,co[mid]['winner'],False);c=run(tape,co[mid]['winner'],True)
            d={k:float(c['terminal'][k])-float(b['terminal'][k]) for k in ('submits','fills','pnl','floor','best','maxSlots')}
            checks=[]
            for t,key,role,exp in spec['receipts']:
                hits=[e for e in c['bindEvents'] if int(e.get('t') or -1)==t and str(e.get('key'))==key and str(e.get('role'))==role]
                paid=sum(float(e.get('liabilityPaidQty') or 0.0) for e in hits)
                checks.append({'marketId':mid,'t':t,'key':key,'role':role,'expected':exp,'observed':paid,'hitCount':len(hits),'pass':near(paid,exp)})
            owner=next((x for x in c['obligations'] if int(x.get('id') or -1)==spec['ownerId']),None)
            dedicated={str(e.get('key')) for e in c['events'] if e.get('event')=='R239_HANDOFF_REPAIR_FILL'}
            g={'requiredReceiptsExact':all(x['pass'] and x['hitCount']==1 for x in checks),
               'ownerOutstandingExact':bool(owner and near(owner.get('outstanding',0),spec['expectedOutstanding'])),
               'noDedicatedDoublePay':all(str(e.get('key')) not in dedicated for e in c['bindEvents']),
               'noPhysicalDelta':all(abs(float(v))<=1e-12 for v in d.values()),
               'noUnauthorizedOverflow':c['unauthorizedOverflowQty']<=EPS,
               'noRepairQuotaExcess':c['repairQuotaExcessMax']<=EPS,
               'max4Preserved':c['terminal']['maxSlots']<=4}
            g['correctnessAllPass']=all(g.values())
            rows.append({'marketId':mid,'baseline':b,'candidate':c,'receiptChecks':checks,'physicalDelta':d,'owner':owner,'gates':g})
            all_receipts.extend(checks);market_gates.append(g)
        gates={'allMarketsPass':all(g['correctnessAllPass'] for g in market_gates),
               'allRequiredReceiptsExact':all(x['pass'] and x['hitCount']==1 for x in all_receipts),
               'allPhysicalParity':all(r['gates']['noPhysicalDelta'] for r in rows),
               'noDoublePay':all(r['gates']['noDedicatedDoublePay'] for r in rows),
               'noUnauthorizedOverflow':all(r['gates']['noUnauthorizedOverflow'] for r in rows),
               'noRepairQuotaExcess':all(r['gates']['noRepairQuotaExcess'] for r in rows)}
        gates['correctnessAllPass']=all(gates.values())
        out={'version':'LANE_G_R239_CONFIRMED_REPAIR_ROLE_PAYMENT_BINDING_SATELLITE_SMOKE2_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,
             'cohort':sorted(MARKETS),'rows':rows,'gates':gates,
             'verdict':'PASS_SATELLITE_REPAIR_PAYMENT_BINDING_SMOKE2' if gates['correctnessAllPass'] else 'FAIL_SATELLITE_REPAIR_PAYMENT_BINDING_SMOKE2',
             'boundary':['consumed 1945869/1946872 only','bookkeeping attribution only','dedicated handoff first and excluded from generic binding','only confirmed overflow births successor','no new authority/credit/capacity','max4/<=180s inherited','no fresh','no dream fill','no 8781','no time/window/rank-age gate']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output)
        op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'verdict':out['verdict'],'gates':gates,'receiptChecks':all_receipts,
                          'ownerOutstanding':{str(r['marketId']):float(r['owner'].get('outstanding') or 0.0) if r['owner'] else None for r in rows},
                          'physicalDelta':{str(r['marketId']):r['physicalDelta'] for r in rows}},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
