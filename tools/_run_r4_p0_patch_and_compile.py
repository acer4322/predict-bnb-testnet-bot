import runpy, py_compile
runpy.run_path('tools/_mkdir_p0_provenance_v1.py', run_name='__main__')
runpy.run_path('tools/_patch_r4_p0_provenance_journal_v1.py', run_name='__main__')
py_compile.compile('tools/hftbacktest_r4_p0_provenance_journal_v1.py', doraise=True)
print('compile_ok')
