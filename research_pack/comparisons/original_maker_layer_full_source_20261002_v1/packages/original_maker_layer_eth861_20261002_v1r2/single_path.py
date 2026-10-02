"""Fresh process: original overlay, untouched mutable globals and decision chain."""
import os,runpy,sys
from pathlib import Path
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'overlay'))
sys.argv=[str(P/'overlay/run_variant.py'),'--variant','PREPARE','--market-id',os.environ['OML_MARKET'],'--mode','NO_DIRECTION','--money-mode','PARALLEL_PAYOFF_ZERO','--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE','--direction-rule','LEGACY']+sys.argv[1:]
runpy.run_path(str(P/'overlay/run_variant.py'),run_name='__main__')
