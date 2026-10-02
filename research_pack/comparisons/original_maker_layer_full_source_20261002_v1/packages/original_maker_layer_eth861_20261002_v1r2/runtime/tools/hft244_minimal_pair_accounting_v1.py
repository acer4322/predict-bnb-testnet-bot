"""Receipt accounting for Pair-Core Minimal Safety, NOT R247/V8 policy."""
import os

from .hft244_receipt_adapter_v1 import Reader, Ledger
from .hft244_research_owner_accounting_v1 import physical_process, strict_advance_to


def install(base, binary):
    if 'BTC5M_LAN_RESULT_DIR' not in os.environ:
        raise RuntimeError('research LAN process only')
    if getattr(base.Sim, '_receipt_installed', False):
        raise RuntimeError('receipt integration already installed')
    original_init = base.Sim.__init__

    def initialize(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._receipt_reader = Reader(self.bt, binary)
        self._receipt_ledger = Ledger()
        self._receipt_invalid = False
        self._receipt_delta_rows = []

    base.Sim.__init__ = initialize
    base.Sim.process = physical_process
    base.ex.advance_to = strict_advance_to
    base.Sim._receipt_installed = True
    # Deliberately do not install V8 role_process, scope credit, service
    # quarantine, preview codecs, or any R247 economic/authority behavior.
