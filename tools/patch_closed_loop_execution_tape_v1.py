from pathlib import Path
p=Path('tools/hftbacktest_cap100_closed_loop_v0.py')
s=p.read_text(encoding='utf-8')
s=s.replace('from tools import hftbacktest_true_match_calibration_v0 as tm\n','from tools import hftbacktest_execution_tape_feed_v1 as tape_v1\n')
old='self.events, self.update_times, self.meta = tm.depth_plus_true_trades(self.market_id, trade_offset=trade_offset)'
new='self.events, self.update_times, self.meta = tape_v1.build_archive_events(self.market_id, trade_offset=trade_offset)'
if old not in s:
    raise SystemExit('target feed line not found')
s=s.replace(old,new)
s=s.replace('"makerExecution":"HFTBACKTEST_L2_PLUS_TRUE_MATCHES"','"makerExecution":"HFTBACKTEST_EXECUTION_TAPE_V1_ARCHIVE"')
p.write_text(s,encoding='utf-8')
print('patched',p)
