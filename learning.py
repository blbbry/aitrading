"""
Adaptive Learning System
========================
Tracks which signals actually produce profitable trades and adjusts their
point weights in the scoring engine over time.

How it works:
  1. On every sell, record which signals fired at entry + the outcome (P&L %)
  2. After MIN_TRADES_TO_TUNE closed trades per signal, compute a weight multiplier
  3. The weight is applied in strategies/swing.py to that signal's base point value
  4. RSI thresholds adapt separately based on which RSI ranges perform best

Weight formula (Bayesian shrinkage):
  posterior_win_rate = (wins + K*0.5) / (n + K)   where K=SHRINKAGE_K
  raw_weight = posterior_win_rate / 0.5             (neutral=1.0, 70% winrate→1.4)
  + pnl_bonus (magnitude of average winner matters too)
  + decay toward 1.0 if no recent trades
  capped to [WEIGHT_MIN, WEIGHT_MAX]
"""

import sqlite3
import json
import os
from datetime import datetime, timedelta

_DATA_DIR  = "/data" if os.path.isdir("/data") else os.path.dirname(__file__)
DB_PATH    = os.environ.get("DB_PATH", os.path.join(_DATA_DIR, "portfolio.db"))

# ── Tuning constants ──────────────────────────────────────────────────────────
WEIGHT_MIN         = 0.4    # minimum weight multiplier (never fully disable)
WEIGHT_MAX         = 2.5    # maximum weight multiplier
MIN_TRADES_TO_TUNE = 10     # need this many closed trades before adjusting weight
ROLLING_DAYS       = 90     # only use trades from the last N days
DECAY_DAYS         = 30     # if no new trades for this many days, decay toward 1.0
DECAY_RATE         = 0.05   # per DECAY_DAYS interval, move weight 5% toward 1.0
SHRINKAGE_K        = 5      # pseudo-count (higher = more conservative updates)

# RSI bucket boundaries for threshold adaptation
RSI_BUCKETS = [(20, 30), (30, 35), (35, 40), (40, 45),
               (45, 50), (50, 55), (55, 65), (65, 75)]


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# ── Schema ────────────────────────────────────────────────────────────────────

def init_learning_db():
    """Create learning tables in the existing portfolio DB. Idempotent."""
    with _conn() as con:
        con.executescript("""
            -- Raw event log: one row per (trade, signal)
            CREATE TABLE IF NOT EXISTS trade_signals (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol       TEXT    NOT NULL,
                signal       TEXT    NOT NULL,
                entry_date   TEXT    NOT NULL,
                close_date   TEXT    NOT NULL,
                pnl_pct      REAL    NOT NULL,
                hold_days    INTEGER NOT NULL,
                rsi_at_entry REAL
            );
            CREATE INDEX IF NOT EXISTS idx_ts_signal ON trade_signals(signal);
            CREATE INDEX IF NOT EXISTS idx_ts_close  ON trade_signals(close_date);

            -- Computed aggregate: one row per signal name
            CREATE TABLE IF NOT EXISTS signal_stats (
                signal          TEXT  PRIMARY KEY,
                total_trades    INTEGER NOT NULL DEFAULT 0,
                wins            INTEGER NOT NULL DEFAULT 0,
                total_pnl_pct   REAL    NOT NULL DEFAULT 0.0,
                avg_hold_days   REAL    NOT NULL DEFAULT 0.0,
                current_weight  REAL    NOT NULL DEFAULT 1.0,
                last_updated    TEXT    NOT NULL DEFAULT '',
                last_trade_date TEXT
            );

            -- RSI bucket stats for threshold adaptation
            CREATE TABLE IF NOT EXISTS rsi_bucket_stats (
                bucket_label  TEXT PRIMARY KEY,
                rsi_min       REAL NOT NULL,
                rsi_max       REAL NOT NULL,
                total_trades  INTEGER NOT NULL DEFAULT 0,
                wins          INTEGER NOT NULL DEFAULT 0,
                total_pnl_pct REAL    NOT NULL DEFAULT 0.0,
                avg_hold_days REAL    NOT NULL DEFAULT 0.0,
                last_updated  TEXT    NOT NULL DEFAULT ''
            );
        """)

        # Seed RSI bucket rows so they exist from day one
        for (lo, hi) in RSI_BUCKETS:
            label = f"{lo}-{hi}"
            con.execute("""
                INSERT OR IGNORE INTO rsi_bucket_stats
                  (bucket_label, rsi_min, rsi_max, last_updated)
                VALUES (?, ?, ?, ?)
            """, (label, lo, hi, datetime.utcnow().isoformat()))

    print("[Learning] DB initialized")


# ── Core recording ────────────────────────────────────────────────────────────

def record_trade_outcome(symbol: str, pnl_pct: float,
                         entry_signals: list, hold_days: int,
                         rsi_at_entry: float = None,
                         entry_date: str = None):
    """
    Called after every sell. Records the outcome for each signal that was
    active at entry, then recomputes weights for those signals.

    Args:
        symbol:       Ticker that was traded.
        pnl_pct:      Realized P&L as a decimal (e.g. 0.073 = +7.3%).
        entry_signals: List of signal names that fired at buy time.
        hold_days:    Number of calendar days the position was held.
        rsi_at_entry: RSI value when the position was opened (optional).
        entry_date:   ISO timestamp of the buy (optional, defaults to now).
    """
    if not entry_signals:
        return

    now        = datetime.utcnow().isoformat()
    entry_ts   = entry_date or now
    signals_to_update = set()

    with _conn() as con:
        for sig in entry_signals:
            con.execute("""
                INSERT INTO trade_signals
                  (symbol, signal, entry_date, close_date, pnl_pct, hold_days, rsi_at_entry)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (symbol, sig, entry_ts, now, pnl_pct, hold_days, rsi_at_entry))
            signals_to_update.add(sig)

        # Update RSI bucket if we have RSI data
        if rsi_at_entry is not None:
            _update_rsi_bucket(con, rsi_at_entry, pnl_pct, hold_days)

    # Recompute weights for every affected signal
    for sig in signals_to_update:
        _recompute_signal_weight(sig)

    print(f"[Learning] Recorded outcome for {symbol}: {pnl_pct:+.2%} | "
          f"signals={entry_signals} | hold={hold_days}d")


def _update_rsi_bucket(con, rsi: float, pnl_pct: float, hold_days: int):
    """Update the RSI bucket stats for the given RSI value."""
    for (lo, hi) in RSI_BUCKETS:
        if lo <= rsi < hi:
            label = f"{lo}-{hi}"
            is_win = 1 if pnl_pct > 0 else 0
            con.execute("""
                UPDATE rsi_bucket_stats
                SET total_trades  = total_trades + 1,
                    wins          = wins + ?,
                    total_pnl_pct = total_pnl_pct + ?,
                    avg_hold_days = (avg_hold_days * total_trades + ?) / (total_trades + 1),
                    last_updated  = ?
                WHERE bucket_label = ?
            """, (is_win, pnl_pct, hold_days, datetime.utcnow().isoformat(), label))
            break


def _recompute_signal_weight(signal_name: str):
    """
    Recompute the weight for a single signal from the rolling-window raw data.
    Uses Bayesian shrinkage to avoid overfitting small samples.
    """
    cutoff = (datetime.utcnow() - timedelta(days=ROLLING_DAYS)).isoformat()

    with _conn() as con:
        rows = con.execute("""
            SELECT pnl_pct, hold_days, close_date
            FROM trade_signals
            WHERE signal = ? AND close_date >= ?
            ORDER BY close_date DESC
        """, (signal_name, cutoff)).fetchall()

        n = len(rows)
        now_str = datetime.utcnow().isoformat()

        if n == 0:
            # No data in rolling window — write neutral
            con.execute("""
                INSERT INTO signal_stats (signal, total_trades, wins, total_pnl_pct,
                    avg_hold_days, current_weight, last_updated, last_trade_date)
                VALUES (?, 0, 0, 0.0, 0.0, 1.0, ?, NULL)
                ON CONFLICT(signal) DO UPDATE SET
                    total_trades=0, wins=0, total_pnl_pct=0.0,
                    avg_hold_days=0.0, current_weight=1.0, last_updated=?
            """, (signal_name, now_str, now_str))
            return

        pnl_values   = [r["pnl_pct"]   for r in rows]
        hold_values  = [r["hold_days"]  for r in rows]
        close_dates  = [r["close_date"] for r in rows]

        wins         = sum(1 for p in pnl_values if p > 0)
        total_pnl    = sum(pnl_values)
        avg_pnl      = total_pnl / n
        avg_hold     = sum(hold_values) / n
        last_trade   = max(close_dates)

        if n < MIN_TRADES_TO_TUNE:
            # Track progress but don't adjust weight yet
            is_win = 1 if (pnl_values[-1] if pnl_values else 0) > 0 else 0
            con.execute("""
                INSERT INTO signal_stats (signal, total_trades, wins, total_pnl_pct,
                    avg_hold_days, current_weight, last_updated, last_trade_date)
                VALUES (?, ?, ?, ?, ?, 1.0, ?, ?)
                ON CONFLICT(signal) DO UPDATE SET
                    total_trades=excluded.total_trades,
                    wins=excluded.wins,
                    total_pnl_pct=excluded.total_pnl_pct,
                    avg_hold_days=excluded.avg_hold_days,
                    current_weight=1.0,
                    last_updated=excluded.last_updated,
                    last_trade_date=excluded.last_trade_date
            """, (signal_name, n, wins, total_pnl, avg_hold, now_str, last_trade))
            return

        # ── Bayesian shrinkage weight formula ──────────────────────────────
        # Prior: 50% win rate (weight=1.0). Pseudo-count K blends prior with data.
        posterior_win_rate = (wins + SHRINKAGE_K * 0.5) / (n + SHRINKAGE_K)

        # Base weight: 50% winrate → 1.0, 70% → 1.4, 30% → 0.6
        raw_weight = posterior_win_rate / 0.5

        # P&L magnitude bonus: a 7% avg winner scores higher than a 0.5% avg winner
        # avg_pnl=0.05 → +0.10 bonus, avg_pnl=-0.05 → -0.10, capped at ±0.30
        pnl_bonus = max(-0.30, min(0.30, avg_pnl * 2.0))
        raw_weight += pnl_bonus

        # ── Decay toward 1.0 if signal hasn't fired recently ───────────────
        try:
            days_stale = (datetime.utcnow() - datetime.fromisoformat(last_trade)).days
            decay_periods = days_stale // DECAY_DAYS
            for _ in range(decay_periods):
                raw_weight = raw_weight + DECAY_RATE * (1.0 - raw_weight)
        except Exception:
            pass

        # ── Hard cap ───────────────────────────────────────────────────────
        final_weight = max(WEIGHT_MIN, min(WEIGHT_MAX, raw_weight))

        con.execute("""
            INSERT INTO signal_stats (signal, total_trades, wins, total_pnl_pct,
                avg_hold_days, current_weight, last_updated, last_trade_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal) DO UPDATE SET
                total_trades=excluded.total_trades,
                wins=excluded.wins,
                total_pnl_pct=excluded.total_pnl_pct,
                avg_hold_days=excluded.avg_hold_days,
                current_weight=excluded.current_weight,
                last_updated=excluded.last_updated,
                last_trade_date=excluded.last_trade_date
        """, (signal_name, n, wins, total_pnl, avg_hold, final_weight, now_str, last_trade))

        direction = "↑" if final_weight > 1.05 else ("↓" if final_weight < 0.95 else "→")
        print(f"[Learning] {signal_name}: weight={final_weight:.3f}{direction} "
              f"(n={n}, winrate={wins/n:.0%}, avg_pnl={avg_pnl:+.2%})")


# ── Public query functions ────────────────────────────────────────────────────

def get_signal_weights() -> dict:
    """
    Returns current weight multipliers for all tracked signals.
    Untracked signals default to 1.0 (neutral).
    """
    try:
        with _conn() as con:
            rows = con.execute(
                "SELECT signal, current_weight FROM signal_stats"
            ).fetchall()
        return {r["signal"]: r["current_weight"] for r in rows}
    except Exception:
        return {}


def get_adapted_thresholds() -> dict:
    """
    Returns adapted MIN_RSI_BUY and MAX_RSI_BUY based on which RSI ranges
    have historically produced the best results.

    Falls back to defaults (28, 65) if not enough data.
    """
    DEFAULT = {"min_rsi_buy": 28, "max_rsi_buy": 65, "adapted": False}

    try:
        with _conn() as con:
            rows = con.execute("""
                SELECT bucket_label, rsi_min, rsi_max,
                       total_trades, wins, total_pnl_pct
                FROM rsi_bucket_stats
                WHERE total_trades >= ?
                ORDER BY rsi_min
            """, (MIN_TRADES_TO_TUNE,)).fetchall()

        if not rows:
            return DEFAULT

        profitable = []
        for r in rows:
            win_rate = r["wins"] / r["total_trades"]
            avg_pnl  = r["total_pnl_pct"] / r["total_trades"]
            ev = win_rate * avg_pnl   # expected value per trade
            if ev > 0:
                profitable.append((r["rsi_min"], r["rsi_max"], ev))

        if not profitable:
            return DEFAULT

        min_rsi = max(15.0, min(b[0] for b in profitable) - 3)
        max_rsi = min(80.0, max(b[1] for b in profitable) + 3)

        if max_rsi - min_rsi < 15:
            return DEFAULT

        return {
            "min_rsi_buy": round(min_rsi, 1),
            "max_rsi_buy": round(max_rsi, 1),
            "profitable_buckets": len(profitable),
            "adapted": True,
        }
    except Exception:
        return DEFAULT


def get_learning_stats() -> dict:
    """Returns full learning stats for the /learning dashboard endpoint."""
    try:
        with _conn() as con:
            signal_rows = con.execute("""
                SELECT signal, total_trades, wins, total_pnl_pct,
                       avg_hold_days, current_weight, last_trade_date
                FROM signal_stats
                ORDER BY current_weight DESC
            """).fetchall()

            total_logged = con.execute(
                "SELECT COUNT(*) as c FROM trade_signals"
            ).fetchone()["c"]

            rsi_rows = con.execute("""
                SELECT bucket_label, total_trades, wins, total_pnl_pct, avg_hold_days
                FROM rsi_bucket_stats ORDER BY rsi_min
            """).fetchall()

        signals = []
        for s in signal_rows:
            n = s["total_trades"]
            signals.append({
                "signal":        s["signal"],
                "trades":        n,
                "win_rate_pct":  round(s["wins"] / n * 100, 1) if n else 0,
                "avg_pnl_pct":   round(s["total_pnl_pct"] / n * 100, 2) if n else 0,
                "avg_hold_days": round(s["avg_hold_days"], 1),
                "weight":        round(s["current_weight"], 3),
                "qualified":     n >= MIN_TRADES_TO_TUNE,
                "last_trade":    s["last_trade_date"],
            })

        rsi_buckets = []
        for r in rsi_rows:
            n = r["total_trades"]
            rsi_buckets.append({
                "bucket":        r["bucket_label"],
                "trades":        n,
                "win_rate_pct":  round(r["wins"] / n * 100, 1) if n else 0,
                "avg_pnl_pct":   round(r["total_pnl_pct"] / n * 100, 2) if n else 0,
                "avg_hold_days": round(r["avg_hold_days"], 1),
            })

        return {
            "total_trade_signals_logged": total_logged,
            "min_trades_to_qualify":      MIN_TRADES_TO_TUNE,
            "rolling_window_days":        ROLLING_DAYS,
            "signals":                    signals,
            "rsi_buckets":                rsi_buckets,
            "adapted_thresholds":         get_adapted_thresholds(),
        }
    except Exception as e:
        return {"error": str(e)}
