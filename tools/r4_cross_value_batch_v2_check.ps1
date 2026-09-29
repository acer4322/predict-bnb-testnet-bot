python tools/r4_cross_value_batch_status_v2.py
if (Test-Path data/research/r4_v0/hourly/r4_cross_value_batch_v2.stdout.log) { Get-Content data/research/r4_v0/hourly/r4_cross_value_batch_v2.stdout.log -Tail 8 }
if (Test-Path data/research/r4_v0/hourly/r4_cross_value_batch_v2.stderr.log) { Get-Content data/research/r4_v0/hourly/r4_cross_value_batch_v2.stderr.log -Tail 4 }
