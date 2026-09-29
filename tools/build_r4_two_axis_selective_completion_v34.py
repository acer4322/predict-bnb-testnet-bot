import json
from pathlib import Path
B=Path('data/research/r4_v0/p0_provenance_v1')
base=json.loads((B/'r4_exact_first_late_event_map_v25.json').read_text(encoding='utf-8'))
ana=json.loads((B/'r4_two_axis_lifecycle_v33_anatomy.json').read_text(encoding='utf-8'))
eligible={int(r['marketId']) for r in ana['rows'] if r['state']=='LOW_ADVERSE__QUEUE_ADVANCING_OR_FLAT'}
events=base.get('events') or base
filtered={str(k):v for k,v in events.items() if int(k) in eligible}
out={'version':'R4_TWO_AXIS_SELECTIVE_COMPLETION_V34_EVENT_MAP','researchOnly':True,'eligibilitySource':'r4_two_axis_lifecycle_v33_anatomy.json state only; labels not consulted for membership','rule':'adverse<=0 AND queueDecay1s>=0','eligibleMarketIds':sorted(eligible),'events':filtered}
(B/'r4_two_axis_selective_completion_v34_event_map.json').write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
empty={'version':'R4_TWO_AXIS_SELECTIVE_COMPLETION_V34_EMPTY_BASELINE_MAP','events':{}}
(B/'r4_two_axis_selective_completion_v34_empty_event_map.json').write_text(json.dumps(empty,indent=2),encoding='utf-8')
print(json.dumps({'eligible':sorted(eligible),'events':len(filtered)}))
