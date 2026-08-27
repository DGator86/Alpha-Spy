from __future__ import annotations

# Importing this module patches the V2 candidate generator to the complete
# 47-family bounded-risk universe before v2_cli imports the engine service.
from . import strategy_v2_complete as _complete_geometry  # noqa: F401
from .v2_cli import main


if __name__ == "__main__":
    main()
