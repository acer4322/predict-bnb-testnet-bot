from pathlib import Path
p=Path('tools/hftbacktest_r2_fixed_tape_tox_episode_cf_v0.py')
s=p.read_text(encoding='utf-8')
old="ap=argparse.ArgumentParser(); ap.add_argument('--market-ids',required=True); ap.add_argument('--output',default='r2_fixed_tape_queue_value_ev_v1.json'); a=ap.parse_args(); mids=[int(x) for x in a.market_ids.split(',') if x.strip()]; rows=[]"
new="global EPISODE_OVERRIDE; ap=argparse.ArgumentParser(); ap.add_argument('--market-ids',required=True); ap.add_argument('--output',default='r2_fixed_tape_queue_value_ev_v1.json'); ap.add_argument('--override',choices=['NONE','TRACKING','MAKER_NET'],default=EPISODE_OVERRIDE); a=ap.parse_args(); EPISODE_OVERRIDE=str(a.override).upper(); mids=[int(x) for x in a.market_ids.split(',') if x.strip()]; rows=[]"
assert old in s
s=s.replace(old,new)
p.write_text(s,encoding='utf-8')
print('ok')
