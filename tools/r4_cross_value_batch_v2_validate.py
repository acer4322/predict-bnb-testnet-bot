import py_compile
for p in ['tools/r4_scan_baseline_cross_candidates_checkpoint_v2.py','tools/r4_collect_paired_forks_from_scan_v2.py','tools/launch_r4_cross_value_batch_v2.py','tools/r4_cross_value_batch_status_v2.py']:
 py_compile.compile(p,doraise=True); print('OK',p)
