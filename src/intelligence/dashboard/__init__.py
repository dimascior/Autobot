"""Live read-only dashboard for Autobot organizational and authority state.

Three layers:
    readers   — read-only connections to intelligence.db, autobot.db, CAS, calls
    model     — assembles one coherent ActivitySnapshot from all readers
    renderers — presentation (cli_stream, rich_tui, web) consumes the snapshot

The ActivitySnapshot is the abstraction boundary. Readers never import
presentation; model never imports presentation; presentation only imports
model. You can run multiple renderers against the same readers.
"""

from .model import build_snapshot
from .views import ActivitySnapshot

__all__ = ["build_snapshot", "ActivitySnapshot"]
