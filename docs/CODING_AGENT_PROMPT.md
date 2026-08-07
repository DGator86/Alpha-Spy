# Coding Agent Prompt

You are extending the `alpha-spy` repository. Preserve its central separation between the physical (`P`) distribution used for expected outcomes and the risk-neutral (`Q`) distribution used for option valuation.

## Immediate assignment

Implement the first real-data vertical slice:

1. Add one historical market-data adapter selected by the repository owner.
2. Ingest point-in-time S&P 500 membership and float-adjusted weights.
3. Build synchronized one-second snapshots for constituents, SPY, SPX, ES, and sector ETFs using backward-only joins and explicit quote-age fields.
4. Produce a basket-integrity report that attributes SPY-versus-basket residuals to stale quotes, uncovered weight, dividends, basis, and unexplained error.
5. Add integration tests built from a small recorded fixture. Tests must run without network access.

## Constraints

- Never use current constituents in historical periods.
- Never forward-fill through halts, overnight boundaries, or beyond configured quote-age limits.
- Never use future earnings, dividend, membership, or corporate-action information.
- Never calibrate a synthetic SPY dependence parameter to the same SPY option quote being evaluated.
- Preserve defined-risk-only strategy generation.
- Every new model must include a simpler baseline and walk-forward evaluation.
- All production decisions must be reproducible from an immutable snapshot and configuration hash.

## Definition of done

- `pytest` passes.
- The fixture generates a deterministic basket-integrity report.
- Covered weight and stale weight are explicit at every timestamp.
- Residual attribution sums to the observed SPY-versus-basket difference.
- Documentation identifies the vendor fields, exchange timestamps, and known data limitations.
