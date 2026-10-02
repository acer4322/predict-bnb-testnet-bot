$p=Start-Process -FilePath python -ArgumentList 'tools/launch_r4_cross_value_batch_v2.py' -NoNewWindow -PassThru -Wait
python tools/r4_cross_value_batch_status_v2.py
