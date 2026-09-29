import subprocess, sys
for script in ['tools/patch_execution_tape_v1.py','tools/patch_predict_observer_execution_meta_v1.py']:
    subprocess.check_call([sys.executable,script])
for target in ['src/predict_bot/predict_wallet_maker_book_inference_collector.py','src/predict_bot/predict_fun_observer.py','tools/backfill_predict_raw_matches_v1.py','tools/test_execution_tape_v1.py']:
    subprocess.check_call([sys.executable,'-m','py_compile',target])
subprocess.check_call([sys.executable,'tools/test_execution_tape_v1.py'])
print('ALL_OK')
