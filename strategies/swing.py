"""
Swing Trading Signal Engine
Evaluates stocks for swing trade setups based on technical conditions.
Targets: multi-day to multi-week holds, 5-20%+ gain potential.
"""

from tools.market_data import compute_technicals, get_fundamentals


def score_swing_setup(symbol: str) -> dict:
    """
    Score a stock for swing trade potential (0-100).
    Returns signals, score, and recommended action.
    """
    tech = compute_technicals(symbol, days=120)
    if "error" in tech:
        return {"symbol": symbol, "error": tech["error"], "score": 0}

    signals = []
    score = 0

    price = tech.get("current_price", 0)
    rsi = tech.get("rsi_14")
    macd_hist = tech.get("macd_histogram")
    macd_line = tech.get("macd_line")
    macd_signal = tech.get("macd_signal")
    sma20 = tech.get("sma20")
    sma50 = tech.get("sma50")
    sma200 = tech.get("sma200")
    bb_upper = tech.get("bb_upper")
    bb_lower = tech.get("bb_lower")
    vol_ratio = tech.get("volume_ratio")
    atr = tech.get("atr_14")
    change_5d = tech.get("price_change_5d_pct")
    change_20d = tech.get("price_change_20d_pct")

    # ── RSI signals ────────────────────────────────────────────────────────────
    if rsi is not None:
        if 30 <= rsi <= 45:
            signals.append({"signal": "RSI_OVERSOLD_RECOVERY", "detail": f"RSI {rsi:.1f} — bouncing from oversold zone", "bullish": True})
            score += 20
        elif rsi < 30:
            signals.append({"signal": "RSI_OVERSOLD", "detail": f"RSI {rsi:.1f} — extreme oversold, watch for reversal", "bullish": True})
            score += 15
        elif 55 <= rsi <= 65:
            signals.append({"signal": "RSI_BULLISH_MOMENTUM", "detail": f"RSI {rsi:.1f} — healthy momentum, not overbought", "bullish": True})
            score += 10
        elif rsi > 70:
            signals.append({"signal": "RSI_OVERBOUGHT", "detail": f"RSI {rsi:.1f} — overbought, risk of pullback", "bullish": False})
            score -= 15

    # ── MACD signals ───────────────────────────────────────────────────────────
    if macd_hist is not None and macd_line is not None:
        if macd_hist > 0 and macd_line > macd_signal:
            signals.append({"signal": "MACD_BULLISH_CROSS", "detail": "MACD crossed above signal line — bullish momentum", "bullish": True})
            score += 20
        elif macd_hist < 0 and abs(macd_hist) < 0.5:
            signals.append({"signal": "MACD_APPROACHING_CROSS", "detail": "MACD histogram narrowing — potential bullish crossover near", "bullish": True})
            score += 10
        elif macd_hist < -1:
            signals.append({"signal": "MACD_BEARISH", "detail": "MACD deeply negative — bearish momentum", "bullish": False})
            score -= 10

    # ── Moving average signals ─────────────────────────────────────────────────
    if sma20 and price:
        if price > sma20:
            signals.append({"signal": "ABOVE_SMA20", "detail": f"Price ${price:.2f} above SMA20 ${sma20:.2f} — short-term uptrend", "bullish": True})
            score += 10
        elif price < sma20 * 0.97:
            signals.append({"signal": "BELOW_SMA20", "detail": f"Price ${price:.2f} below SMA20 — short-term downtrend", "bullish": False})
            score -= 5

    if sma50 and price:
        if price > sma50:
            signals.append({"signal": "ABOVE_SMA50", "detail": f"Price above SMA50 ${sma50:.2f} — medium-term uptrend", "bullish": True})
            score += 10
        else:
            signals.append({"signal": "BELOW_SMA50", "detail": f"Price below SMA50 — medium-term caution", "bullish": False})
            score -= 5

    if sma200 and price:
        if price > sma200:
            signals.append({"signal": "ABOVE_SMA200", "detail": "Price above SMA200 — in long-term uptrend", "bullish": True})
            score += 10
        else:
            signals.append({"signal": "BELOW_SMA200", "detail": "Price below SMA200 — long-term downtrend caution", "bullish": False})
            score -= 10

    # ── Golden / Death cross ───────────────────────────────────────────────────
    if sma20 and sma50:
        if sma20 > sma50:
            signals.append({"signal": "GOLDEN_CROSS_ZONE", "detail": "SMA20 > SMA50 — golden cross alignment", "bullish": True})
            score += 10
        else:
            signals.append({"signal": "DEATH_CROSS_ZONE", "detail": "SMA20 < SMA50 — death cross alignment", "bullish": False})
            score -= 10

    # ── Bollinger Band signals ─────────────────────────────────────────────────
    if bb_lower and bb_upper and price:
        bb_range = bb_upper - bb_lower
        if bb_range > 0:
            bb_position = (price - bb_lower) / bb_range
            if bb_position < 0.2:
                signals.append({"signal": "BB_LOWER_BAND", "detail": f"Price near lower Bollinger Band — potential mean reversion bounce", "bullish": True})
                score += 15
            elif bb_position > 0.8:
                signals.append({"signal": "BB_UPPER_BAND", "detail": "Price near upper Bollinger Band — may be stretched", "bullish": False})
                score -= 5

    # ── Volume signals ─────────────────────────────────────────────────────────
    if vol_ratio is not None:
        if vol_ratio > 1.5:
            signals.append({"signal": "HIGH_VOLUME", "detail": f"Volume {vol_ratio:.1f}x above average — strong conviction move", "bullish": True})
            score += 10
        elif vol_ratio < 0.5:
            signals.append({"signal": "LOW_VOLUME", "detail": "Volume very low — weak move, less reliable signal", "bullish": False})
            score -= 5

    # ── Momentum ───────────────────────────────────────────────────────────────
    if change_5d is not None:
        if 2 <= change_5d <= 8:
            signals.append({"signal": "HEALTHY_5D_MOMENTUM", "detail": f"+{change_5d:.1f}% in 5 days — strong but not extended", "bullish": True})
            score += 5
        elif change_5d > 12:
            signals.append({"signal": "EXTENDED_5D", "detail": f"+{change_5d:.1f}% in 5 days — may be extended, wait for pullback", "bullish": False})
            score -= 5
        elif change_5d < -8:
            signals.append({"signal": "SHARP_5D_SELLOFF", "detail": f"{change_5d:.1f}% in 5 days — sharp drop, watch for reversal", "bullish": True})
            score += 5  # contrarian bounce potential

    # ── Risk/reward estimate ───────────────────────────────────────────────────
    # Cap stop at 4% and target at 8%
    raw_stop    = (price - 1.2 * atr) if atr and price else (price * 0.96 if price else None)
    raw_target  = (price + 1.8 * atr) if atr and price else (price * 1.08 if price else None)
    stop_loss   = round(max(raw_stop,   price * 0.96), 2) if raw_stop   else None
    take_profit = round(min(raw_target, price * 1.08), 2) if raw_target else None
    risk_pct = round((price - stop_loss) / price * 100, 1) if stop_loss and price else None
    reward_pct = round((take_profit - price) / price * 100, 1) if take_profit and price else None

    bullish_count = sum(1 for s in signals if s["bullish"])
    bearish_count = sum(1 for s in signals if not s["bullish"])

    if score >= 50:
        action = "STRONG BUY"
    elif score >= 30:
        action = "BUY"
    elif score >= 10:
        action = "WATCH"
    elif score <= -10:
        action = "AVOID"
    else:
        action = "NEUTRAL"

    return {
        "symbol": symbol,
        "score": max(0, min(100, score + 50)),  # normalize to 0-100
        "raw_score": score,
        "action": action,
        "current_price": price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "risk_pct": risk_pct,
        "reward_pct": reward_pct,
        "atr": atr,
        "rsi": rsi,
        "bullish_signals": bullish_count,
        "bearish_signals": bearish_count,
        "signals": signals,
    }
