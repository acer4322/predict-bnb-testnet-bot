from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
src=ROOT/'tools/test_r4_maker_continuous_exact_first_late_v25.py'
out=ROOT/'tools/test_r4_carrier_pathstate_v32_2.py'
s=src.read_text(encoding='utf-8')

old="    action_memory={}\n    suspended_context={}\n"
new="    action_memory={}\n    suspended_context={}\n    seam_instrumentation=[]\n"
if old not in s: raise SystemExit('anchor1 missing')
s=s.replace(old,new,1)

old2="            submit(o,rem,submit_px,submit_kind);counts['queueGateAcquisitions']+=1\n            if late_candidate: counts['lateWeakAcquisitions']+=1\n"
new2="""            if late_candidate:\n                hist=[]\n                for hh,zz in sorted(hids.items()):\n                    if str(zz.get('logical'))!=str(lid) or int(zz.get('submittedAt') or 0)>int(cur_t):\n                        continue\n                    ss=ex.order_snapshot(bt,hh); st=str(ss.get('status') or '')\n                    hist.append({'hid':int(hh),'submittedAt':int(zz.get('submittedAt') or 0),'ageMs':max(0,int(cur_t)-int(zz.get('submittedAt') or 0)),'px':float(zz.get('px') or 0.0),'kind':str(zz.get('kind') or ''),'status':st,'submittedQty':float(zz.get('submittedQty') or 0.0),'cumExecQty':float(ss.get('cumExecQty') or 0.0),'leavesQty':float(ss.get('leavesQty') or 0.0),'cancelRequested':bool(hh in cancel_req),'pendingCancelIntent':bool(hh in pending_cancel_intent)})\n                fills=[x for x in trace if str(x.get('logical'))==str(lid) and x.get('role')=='MAKER' and int(x.get('t') or 0)<=int(cur_t)]\n                last_submit=max((int(x['submittedAt']) for x in hist),default=None)\n                last_fill=max((int(x.get('t') or 0) for x in fills),default=None)\n                pxs=[float(x['px']) for x in hist]\n                seam_instrumentation.append({'t':int(cur_t),'marketId':int(mid),'logical':str(lid),'side':str(o['side']),'need':int(need),'secondsPastNeed':(int(cur_t)-int(need))/1000.0,'plannedSubmitPx':float(submit_px),'originalPx':float(o['px']),'remainingQty':float(rem),'logicalFilledQty':float(logical_filled[lid]),'priorSubmitCount':len(hist),'priorRepriceSubmitCount':sum(1 for x in hist if x['kind']=='REPRICE'),'priorOptionSubmitCount':sum(1 for x in hist if x['kind']=='OPTION'),'priorMainSubmitCount':sum(1 for x in hist if x['kind']=='MAIN'),'priorDistinctPriceCount':len(set(round(x,8) for x in pxs)),'priceMigrationTicks':None if len(pxs)<2 else (pxs[-1]-pxs[0])/0.01,'timeSinceLastSubmitMs':None if last_submit is None else int(cur_t)-last_submit,'priorFillEventCount':len(fills),'priorFillShares':sum(float(x.get('q') or 0.0) for x in fills),'timeSinceLastFillMs':None if last_fill is None else int(cur_t)-last_fill,'history':hist})\n            submit(o,rem,submit_px,submit_kind);counts['queueGateAcquisitions']+=1\n            if late_candidate: counts['lateWeakAcquisitions']+=1\n"""
if old2 not in s: raise SystemExit('anchor2 missing')
s=s.replace(old2,new2,1)

old3="'continuousReducer':dict(reducer),'continuousStateTrace':state_trace,**ps}"
new3="'continuousReducer':dict(reducer),'continuousStateTrace':state_trace,'seamInstrumentation':seam_instrumentation,**ps}"
if old3 not in s: raise SystemExit('anchor3 missing')
s=s.replace(old3,new3,1)

s=s.replace("POLICIES=('CONT_STATE_H2_SUSPEND_EXACT_FIRST_LATE_TOUCH1',)","POLICIES=('CONT_STATE_H2_SUSPEND_EXACT_FIRST_LATE_TOUCH1',)\n# V32.2 research-only instrumentation; policy logic unchanged.",1)
out.write_text(s,encoding='utf-8')
print(out)
