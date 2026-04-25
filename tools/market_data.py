import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


def get_current_price(symbol: str) -> float | None:
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period="1d", interval="1m")
        if data.empty:
            return None
        return float(data["Close"].iloc[-1])
    except Exception:
        return None


def get_price_history(symbol: str, days: int = 90) -> pd.DataFrame:
    ticker = yf.Ticker(symbol)
    end = datetime.now()
    start = end - timedelta(days=days)
    df = ticker.history(start=start, end=end)
    return df


def get_fundamentals(symbol: str) -> dict:
    ticker = yf.Ticker(symbol)
    info = ticker.info
    keys = [
        "shortName", "sector", "industry", "marketCap", "trailingPE", "forwardPE",
        "priceToBook", "dividendYield", "beta", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
        "revenueGrowth", "earningsGrowth", "returnOnEquity", "debtToEquity",
        "currentRatio", "freeCashflow", "operatingMargins", "profitMargins",
        "recommendationMean", "numberOfAnalystOpinions", "targetMeanPrice",
        "shortPercentOfFloat", "heldPercentInstitutions",
    ]
    return {k: info.get(k) for k in keys}


def get_news(symbol: str, limit: int = 8) -> list[dict]:
    ticker = yf.Ticker(symbol)
    try:
        news = ticker.news or []
        results = []
        for item in news[:limit]:
            content = item.get("content", {})
            title = content.get("title", item.get("title", ""))
            summary = content.get("summary", "")
            provider = content.get("provider", {})
            source = provider.get("displayName", "") if isinstance(provider, dict) else ""
            pub_date = content.get("pubDate", "")
            results.append({"title": title, "summary": summary, "source": source, "published": pub_date})
        return results
    except Exception:
        return []


def compute_technicals(symbol: str, days: int = 120) -> dict:
    df = get_price_history(symbol, days=days)
    if df.empty or len(df) < 20:
        return {"error": "Insufficient price history"}

    close = df["Close"]
    volume = df["Volume"]

    # Moving averages
    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
    sma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None
    ema12 = close.ewm(span=12).mean().iloc[-1]
    ema26 = close.ewm(span=26).mean().iloc[-1]

    # MACD
    macd_line = ema12 - ema26
    signal_line = (close.ewm(span=12).mean() - close.ewm(span=26).mean()).ewm(span=9).mean().iloc[-1]
    macd_histogram = macd_line - signal_line

    # RSI
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss
    rsi = (100 - (100 / (1 + rs))).iloc[-1]

    # Bollinger Bands
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_upper = (bb_mid + 2 * bb_std).iloc[-1]
    bb_lower = (bb_mid - 2 * bb_std).iloc[-1]

    # ATR
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - close.shift()).abs()
    low_close = (df["Low"] - close.shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = true_range.rolling(14).mean().iloc[-1]

    # Volume
    avg_volume = volume.rolling(20).mean().iloc[-1]
    current_volume = volume.iloc[-1]

    current_price = float(close.iloc[-1])
    price_change_5d = float((close.iloc[-1] - close.iloc[-6]) / close.iloc[-6] * 100) if len(close) >= 6 else None
    price_change_20d = float((close.iloc[-1] - close.iloc[-21]) / close.iloc[-21] * 100) if len(close) >= 21 else None

    def _f(v):
        return round(float(v), 4) if v is not None and not (isinstance(v, float) and np.isnan(v)) else None

    return {
        "current_price": _f(current_price),
        "sma20": _f(sma20),
        "sma50": _f(sma50),
        "sma200": _f(sma200),
        "rsi_14": _f(rsi),
        "macd_line": _f(macd_line),
        "macd_signal": _f(signal_line),
        "macd_histogram": _f(macd_histogram),
        "bb_upper": _f(bb_upper),
        "bb_lower": _f(bb_lower),
        "atr_14": _f(atr),
        "avg_volume_20d": _f(avg_volume),
        "current_volume": _f(current_volume),
        "volume_ratio": _f(current_volume / avg_volume) if avg_volume else None,
        "price_change_5d_pct": _f(price_change_5d),
        "price_change_20d_pct": _f(price_change_20d),
        "price_above_sma20": current_price > float(sma20) if sma20 else None,
        "price_above_sma50": current_price > float(sma50) if sma50 else None,
        "price_above_sma200": current_price > float(sma200) if sma200 else None,
    }
