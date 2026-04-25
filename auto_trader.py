"""
Auto-Trading Engine — Swing Trading Logic
==========================================
Runs continuously during market hours.

BUY logic  : Screen watchlist every 30 min, buy top setups if score >= 65
SELL logic : Check positions every 5 min for stop-loss / take-profit / reversal signals

Swing trading rules enforced:
  Entry  → score ≥ 65, RSI 28–65, not already holding, cash available, market open
  Exit   → stop-loss hit | take-profit hit | RSI > 75 | MACD bearish cross | score < 35 | held > 15 days
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from tools.market_data import compute_technicals, get_current_price
from strategies.swing import score_swing_setup
from watchlist import load_watchlist
import portfolio

log = logging.getLogger("auto_trader")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

ET = ZoneInfo("America/New_York")

# ── Config ────────────────────────────────────────────────────────────────────
MIN_BUY_SCORE       = 65      # minimum swing score to trigger a buy
MAX_POSITIONS       = 6       # max concurrent open positions
MAX_POSITION_PCT    = 0.10    # max 10% of equity per position
RISK_PER_TRADE_PCT  = 0.01    # risk 1% of equity per trade (ATR-based sizing)
MIN_RSI_BUY         = 28      # don't buy if RSI below this (too volatile)
MAX_RSI_BUY         = 65      # don't buy if RSI above this (overbought)
MAX_HOLD_DAYS       = 15      # force-exit after this many days
SELL_RSI_OVERBOUGHT = 75      # take profit if RSI spikes here
SELL_SCORE_FLOOR    = 35      # exit if swing score collapses below this
SCREEN_INTERVAL_MIN = 30      # screen watchlist every N minutes
CHECK_INTERVAL_MIN  = 5       # check stop/target every N minutes

# Shared state — readable by server
state = {
    "enabled": False,
    "force_mode": False,   # bypass market hours check for testing
    "last_screen": None,
    "last_check": None,
    "trades_today": 0,
    "log": [],
}


def _log(msg: str, level: str = "info"):
    entry = {"ts": datetime.utcnow().isoformat(), "msg": msg, "level": level}
    state["log"].insert(0, entry)
    state["log"] = state["log"][:100]
    getattr(log, level)(msg)


def is_market_open() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:       # Saturday=5, Sunday=6
        return False
    market_open  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return market_open <= now <= market_close


def minutes_to_market_open() -> int:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        days_ahead = 7 - now.weekday()
        next_open = (now + timedelta(days=days_ahead)).replace(hour=9, minute=30, second=0, microsecond=0)
    else:
        today_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        if now < today_open:
            next_open = today_open
        elif now.hour >= 16:
            next_day = now + timedelta(days=1)
            while next_day.weekday() >= 5:
                next_day += timedelta(days=1)
            next_open = next_day.replace(hour=9, minute=30, second=0, microsecond=0)
        else:
            return 0
    return max(0, int((next_open - now).total_seconds() / 60))


# ── Position sizing ────────────────────────────────────────────────────────────
def calc_shares(price: float, atr: float, equity: float) -> float:
    """ATR-based position sizing: risk 1% of equity, stop = 2*ATR below entry."""
    if not atr or not price or atr <= 0:
        # Fallback: use 5% of equity / price
        return max(1, (equity * 0.05) / price)
    risk_amount = equity * RISK_PER_TRADE_PCT
    stop_distance = 2 * atr
    shares = risk_amount / stop_distance
    # Cap at MAX_POSITION_PCT of equity
    max_shares = (equity * MAX_POSITION_PCT) / price
    shares = min(shares, max_shares)
    return max(1, round(shares, 2))


# ── BUY logic ─────────────────────────────────────────────────────────────────
def _should_buy(symbol: str, setup: dict) -> tuple[bool, str]:
    """Returns (should_buy, reason_or_skip_reason)."""
    positions = portfolio.get_positions()

    if symbol in positions:
        return False, "already holding"

    if len(positions) >= MAX_POSITIONS:
        return False, f"max positions ({MAX_POSITIONS}) reached"

    score = setup.get("score", 0)
    if score < MIN_BUY_SCORE:
        return False, f"score {score} below threshold {MIN_BUY_SCORE}"

    rsi = setup.get("rsi")
    if rsi is not None:
        if rsi < MIN_RSI_BUY:
            return False, f"RSI {rsi:.0f} too low (min {MIN_RSI_BUY})"
        if rsi > MAX_RSI_BUY:
            return False, f"RSI {rsi:.0f} overbought (max {MAX_RSI_BUY})"

    cash = portfolio.get_cash()
    price = setup.get("current_price", 0)
    if price and cash < price * 1:
        return False, "insufficient cash"

    action = setup.get("action", "")
    if action not in ("STRONG BUY", "BUY"):
        return False, f"action is {action}"

    return True, "all entry conditions met"


def run_buy_scan():
    """Screen watchlist and buy any qualifying setups."""
    if not state["enabled"]:
        return

    _log("🔭 Auto-scan: screening watchlist for swing setups...")
    symbols = load_watchlist()
    setups = []

    for sym in symbols:
        try:
            s = score_swing_setup(sym)
            if "error" not in s:
                setups.append(s)
        except Exception as e:
            _log(f"  Error scoring {sym}: {e}", "warning")

    # Sort by score descending
    setups.sort(key=lambda x: x.get("score", 0), reverse=True)
    top = [s for s in setups if s.get("score", 0) >= MIN_BUY_SCORE]

    _log(f"  Found {len(top)} qualifying setups (score ≥ {MIN_BUY_SCORE})")

    cash = portfolio.get_cash()
    positions = portfolio.get_positions()
    equity = portfolio.get_portfolio_summary({}).get("total_equity", cash)

    bought = 0
    for setup in top:
        if not state["enabled"]:
            break

        symbol = setup["symbol"]
        ok, reason = _should_buy(symbol, setup)
        if not ok:
            _log(f"  ⏭  Skip {symbol}: {reason}")
            continue

        price = setup.get("current_price") or get_current_price(symbol)
        if not price:
            _log(f"  ⚠️  {symbol}: could not fetch price", "warning")
            continue

        atr = setup.get("atr")
        shares = calc_shares(price, atr, equity)
        stop_loss = round(price - 2 * atr, 2) if atr else round(price * 0.95, 2)
        take_profit = round(price + 3 * atr, 2) if atr else round(price * 1.10, 2)

        result = portfolio.buy(
            symbol, shares, price,
            reason=f"Auto-buy: score={setup['score']}, RSI={setup.get('rsi', '?'):.0f}, action={setup['action']}",
            stop_loss=stop_loss,
            take_profit=take_profit,
            swing_score=setup["score"],
        )

        if result.get("ok"):
            bought += 1
            state["trades_today"] += 1
            _log(f"  ✅ BOUGHT {shares:.2f}x {symbol} @ ${price:.2f} | stop=${stop_loss} target=${take_profit} | score={setup['score']}")
        else:
            _log(f"  ❌ Buy failed {symbol}: {result.get('error')}", "warning")

    state["last_screen"] = datetime.utcnow().isoformat()
    _log(f"🔭 Scan complete — {bought} new position(s) opened")


# ── SELL logic ────────────────────────────────────────────────────────────────
def _check_exit_conditions(symbol: str, price: float, meta: dict, tech: dict) -> tuple[bool, str]:
    """Returns (should_sell, reason)."""
    stop_loss   = meta.get("stop_loss")
    take_profit = meta.get("take_profit")
    entry_date  = meta.get("entry_date")
    rsi         = tech.get("rsi_14")
    macd_hist   = tech.get("macd_histogram")
    score_data  = None

    # ── 1. Hard stop-loss ──────────────────────────────────────────────────
    if stop_loss and price <= stop_loss:
        return True, f"STOP LOSS hit (price ${price:.2f} ≤ stop ${stop_loss:.2f})"

    # ── 2. Take-profit ────────────────────────────────────────────────────
    if take_profit and price >= take_profit:
        return True, f"TAKE PROFIT hit (price ${price:.2f} ≥ target ${take_profit:.2f})"

    # ── 3. RSI overbought → take profit ───────────────────────────────────
    if rsi and rsi > SELL_RSI_OVERBOUGHT:
        return True, f"RSI overbought ({rsi:.0f} > {SELL_RSI_OVERBOUGHT}) — taking profit"

    # ── 4. MACD bearish crossover while in profit ──────────────────────────
    pos = portfolio.get_positions().get(symbol, {})
    avg_cost = pos.get("avg_cost", price)
    in_profit = price > avg_cost * 1.02   # at least 2% profit
    if in_profit and macd_hist is not None and macd_hist < -0.05:
        return True, f"MACD turned bearish (hist={macd_hist:.3f}) — locking profit"

    # ── 5. Swing score collapse ────────────────────────────────────────────
    try:
        setup = score_swing_setup(symbol)
        current_score = setup.get("score", 50)
        if current_score < SELL_SCORE_FLOOR:
            return True, f"Swing score collapsed to {current_score} (below floor {SELL_SCORE_FLOOR})"
    except Exception:
        pass

    # ── 6. Time stop — max hold period ────────────────────────────────────
    if entry_date:
        try:
            entry_dt = datetime.fromisoformat(entry_date)
            days_held = (datetime.utcnow() - entry_dt).days
            if days_held >= MAX_HOLD_DAYS:
                return True, f"Time stop: held {days_held} days (max {MAX_HOLD_DAYS})"
        except Exception:
            pass

    return False, ""


def run_position_check():
    """Check all open positions for exit conditions."""
    if not state["enabled"]:
        return

    positions = portfolio.get_positions()
    if not positions:
        state["last_check"] = datetime.utcnow().isoformat()
        return

    _log(f"🔍 Checking {len(positions)} open position(s) for exit signals...")
    meta_all = portfolio.get_all_position_meta()

    for symbol, pos in positions.items():
        try:
            price = get_current_price(symbol)
            if not price:
                _log(f"  ⚠️  {symbol}: could not fetch price", "warning")
                continue

            tech = compute_technicals(symbol, days=60)
            meta = meta_all.get(symbol, {})
            should_sell, reason = _check_exit_conditions(symbol, price, meta, tech)

            if should_sell:
                shares = pos["shares"]
                result = portfolio.sell(symbol, shares, price, reason=f"Auto-sell: {reason}")
                if result.get("ok"):
                    pnl = result.get("pnl", 0)
                    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                    state["trades_today"] += 1
                    _log(f"  🔴 SOLD {shares:.2f}x {symbol} @ ${price:.2f} | P&L: {pnl_str} | {reason}")
                else:
                    _log(f"  ❌ Sell failed {symbol}: {result.get('error')}", "warning")
            else:
                avg_cost = pos.get("avg_cost", price)
                pnl_pct = (price - avg_cost) / avg_cost * 100
                stop = meta.get("stop_loss", "?")
                target = meta.get("take_profit", "?")
                _log(f"  ✅ HOLD {symbol} @ ${price:.2f} ({pnl_pct:+.1f}%) | stop=${stop} target=${target}")

        except Exception as e:
            _log(f"  ❌ Error checking {symbol}: {e}", "error")

    state["last_check"] = datetime.utcnow().isoformat()


# ── Main loop ─────────────────────────────────────────────────────────────────
async def run_auto_trader():
    """Main async loop — runs forever, self-schedules based on market hours."""
    _log("🤖 Auto-trader loop started")
    last_screen_time = None
    last_check_time  = None

    while True:
        try:
            if not state["enabled"]:
                await asyncio.sleep(30)
                continue

            market_ok = is_market_open() or state["force_mode"]
            if not market_ok:
                mins = minutes_to_market_open()
                _log(f"🌙 Market closed — next open in ~{mins} min")
                sleep_secs = min(mins * 60, 1800)
                await asyncio.sleep(max(60, sleep_secs))
                continue

            if state["force_mode"] and not is_market_open():
                _log("⚠️  FORCE MODE — bypassing market hours check")

            now = datetime.utcnow()

            # Check positions every CHECK_INTERVAL_MIN minutes
            if last_check_time is None or (now - last_check_time).seconds >= CHECK_INTERVAL_MIN * 60:
                run_position_check()
                last_check_time = now

            # Screen watchlist every SCREEN_INTERVAL_MIN minutes
            if last_screen_time is None or (now - last_screen_time).seconds >= SCREEN_INTERVAL_MIN * 60:
                run_buy_scan()
                last_screen_time = now

            await asyncio.sleep(60)   # tick every minute

        except asyncio.CancelledError:
            _log("🛑 Auto-trader stopped")
            break
        except Exception as e:
            _log(f"❌ Auto-trader error: {e}", "error")
            await asyncio.sleep(60)
