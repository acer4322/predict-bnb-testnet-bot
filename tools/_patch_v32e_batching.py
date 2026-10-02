from pathlib import Path
p=Path('tools/audit_target_eth_repair_taker_placement_failure_escalation_v1.py')
s=p.read_text(encoding='utf-8')
s=s.replace('import argparse,json,math,sqlite3,zlib,joblib','import argparse,json,math,sqlite3,zlib,joblib,os')
s=s.replace('    scored=[]\n    for mi,(mid,rows) in enumerate(sorted(bymid.items()),1):',"    scored=[]\n    all_items=sorted(bymid.items())\n    batch_start=int(os.environ.get('BTC5M_BATCH_START','0'))\n    batch_count=int(os.environ.get('BTC5M_BATCH_COUNT','0'))\n    batch_items=all_items[batch_start:(batch_start+batch_count) if batch_count>0 else None]\n    for mi,(mid,rows) in enumerate(batch_items,1):")
s=s.replace("print(json.dumps({'progressMarkets':mi,'of':len(bymid),'scored':len(scored)}),flush=True)","print(json.dumps({'progressMarkets':mi,'of':len(batch_items),'batchStart':batch_start,'totalMarkets':len(all_items),'scored':len(scored)}),flush=True)")
old="    OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'oldTestAuc':rep['hazard']['test']['rocAuc'],'portableUsable':rep['usable'],'freshRows':len(scored),'markets':out['freshCoverage']['markets'],'directLifecycle':direct,'unpaid5s_m1Hazard':comparison['unpaid5s'].get('m1s_placementHazard'),'paid5s_m1Hazard':comparison['paid5s'].get('m1s_placementHazard'),'output':str(OUT)},ensure_ascii=False),flush=True)"
new="    suffix=os.environ.get('BTC5M_BATCH_SUFFIX','').strip(); out_path=OUT if not suffix else OUT.with_name(OUT.stem+'_'+suffix+OUT.suffix); out_path.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'oldTestAuc':rep['hazard']['test']['rocAuc'],'portableUsable':rep['usable'],'freshRows':len(scored),'markets':out['freshCoverage']['markets'],'directLifecycle':direct,'unpaid5s_m1Hazard':comparison['unpaid5s'].get('m1s_placementHazard'),'paid5s_m1Hazard':comparison['paid5s'].get('m1s_placementHazard'),'output':str(out_path)},ensure_ascii=False),flush=True)"
s=s.replace(old,new)
p.write_text(s,encoding='utf-8')
print('patched',p)
