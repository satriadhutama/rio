"""strategy_trend_v2_4h.py — Wrapper untuk timeframe 4H.

Logic 100% identik dengan `strategy_trend_v2.compute_signals_v2`.
Yang berbeda hanya path data input di orchestrator
(`run_multi_coin_v2_4h.py`). Parameter strategi PERSIS SAMA dengan
versi daily — tidak ada tuning.
"""

from strategy_trend_v2 import compute_signals_v2

__all__ = ["compute_signals_v2"]
