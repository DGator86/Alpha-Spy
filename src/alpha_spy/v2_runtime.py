from __future__ import annotations

# Import patches before v2_cli imports the engine service:
# 1) exact 47-family bounded-risk geometry;
# 2) trust-weighted Beta V2 prior blended into Alpha P, never Q/family authority.
from . import strategy_v2_complete as _complete_geometry  # noqa: F401
from . import strategy_v2_prior as _beta_prior  # noqa: F401
from .v2_cli import main


if __name__ == "__main__":
    main()
