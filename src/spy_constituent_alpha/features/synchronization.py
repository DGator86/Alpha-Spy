from __future__ import annotations

import numpy as np
import pandas as pd


def align_last_observation(
    frame: pd.DataFrame,
    *,
    timestamp_col: str,
    entity_col: str,
    value_cols: list[str],
    frequency: str = "1s",
    max_age: pd.Timedelta = pd.Timedelta(milliseconds=250),
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Align asynchronous observations to a common grid without looking forward.

    Returns `(aligned_values, age_table)`. Values older than `max_age` are replaced by NaN.
    """
    data = frame.copy()
    data[timestamp_col] = pd.to_datetime(data[timestamp_col], utc=True)
    data = data.sort_values([entity_col, timestamp_col])
    start = data[timestamp_col].min().ceil(frequency)
    end = data[timestamp_col].max().floor(frequency)
    grid = pd.date_range(start, end, freq=frequency, tz="UTC")

    value_parts: list[pd.DataFrame] = []
    age_parts: list[pd.DataFrame] = []
    for entity, group in data.groupby(entity_col, sort=False):
        source = group.set_index(timestamp_col).sort_index()
        union = source.index.union(grid).sort_values()
        reindexed = source.reindex(union).ffill().reindex(grid)
        source_ts = pd.Series(source.index, index=source.index)
        last_ts = source_ts.reindex(union).ffill().reindex(grid)
        ages = pd.Series(grid, index=grid) - pd.to_datetime(last_ts, utc=True)
        stale = ages > max_age
        values = reindexed[value_cols].copy()
        values.loc[stale.to_numpy(), :] = np.nan
        values.columns = pd.MultiIndex.from_product([[entity], value_cols])
        value_parts.append(values)
        age_parts.append(pd.DataFrame({entity: ages.dt.total_seconds() * 1000.0}, index=grid))

    aligned = pd.concat(value_parts, axis=1).sort_index(axis=1)
    age_table = pd.concat(age_parts, axis=1).sort_index(axis=1)
    return aligned, age_table


def synchronized_log_returns(prices: pd.DataFrame, periods: int = 1) -> pd.DataFrame:
    clean = prices.astype(float).where(prices > 0)
    return np.log(clean).diff(periods)
