"""
Live Paper Trading config.

GO_LIVE_DATE menandai kapan validasi paper dimulai.
Trade dengan entry_date >= GO_LIVE_DATE dihitung sebagai realisasi.
Trade sebelumnya = backtest historis.

JANGAN ubah GO_LIVE_DATE setelah set. Itu bakal invalidate seluruh log realisasi.
"""

from datetime import date

# Tanggal pertama trade live paper dianggap valid
# Set ke tanggal HARI INI saat Rio menjalankan tutorial ini pertama kali
GO_LIVE_DATE = date(2026, 6, 16)

# Universe production (5 coin yang profitable di backtest v2)
PRODUCTION_UNIVERSE = ['BNB', 'XRP', 'ADA', 'DOGE', 'TRX']

# Backtest expectation untuk perbandingan (dari tabel summary v2)
BACKTEST_EXPECTATION = {
    'BNB':  {'trades': 7,  'win_pct': 71.4, 'pf': 2.98, 'sharpe': 0.55},
    'XRP':  {'trades': 14, 'win_pct': 42.9, 'pf': 1.07, 'sharpe': 0.08},
    'ADA':  {'trades': 17, 'win_pct': 50.0, 'pf': 1.38, 'sharpe': 0.30},
    'DOGE': {'trades': 14, 'win_pct': 42.9, 'pf': 1.13, 'sharpe': 0.10},
    'TRX':  {'trades': 11, 'win_pct': 45.5, 'pf': 1.10, 'sharpe': 0.10},
}

# Threshold checkpoint
CHECKPOINT_TRADES = [30, 60]
WR_DELTA_ALERT_PP = 15  # alert kalau WR realisasi < backtest - 15 pp
