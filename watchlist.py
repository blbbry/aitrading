"""
Watchlist of quality stocks well-suited for swing trading.
Focused on: high liquidity, strong fundamentals, good momentum, clear technicals.
"""

# Core swing trading watchlist — large-cap growth leaders
DEFAULT_WATCHLIST = [
    # Mega-cap tech (most liquid, cleanest charts)
    "AAPL",  # Apple
    "MSFT",  # Microsoft
    "NVDA",  # Nvidia — AI/GPU leader
    "GOOGL", # Alphabet
    "META",  # Meta Platforms
    "AMZN",  # Amazon
    "TSLA",  # Tesla — high beta, great swings

    # High-growth tech
    "AMD",   # AMD — NVDA alternative, strong chart
    "AVGO",  # Broadcom — AI infrastructure
    "CRM",   # Salesforce
    "NOW",   # ServiceNow
    "PANW",  # Palo Alto Networks — cybersecurity
    "CRWD",  # CrowdStrike — cybersecurity
    "SNOW",  # Snowflake — data cloud
    "PLTR",  # Palantir — AI/gov data

    # Financials & other sectors
    "JPM",   # JPMorgan
    "GS",    # Goldman Sachs
    "V",     # Visa
    "MA",    # Mastercard

    # Broad market ETFs (swing-friendly, less earnings risk)
    "SPY",   # S&P 500
    "QQQ",   # Nasdaq 100
    "SOXS",  # Semiconductor ETF
]

import json
import os

_DATA_DIR = "/data" if os.path.isdir("/data") else os.path.dirname(__file__)
WATCHLIST_FILE = os.path.join(_DATA_DIR, "watchlist.json")


def load_watchlist() -> list[str]:
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE) as f:
            return json.load(f)
    save_watchlist(DEFAULT_WATCHLIST)
    return DEFAULT_WATCHLIST.copy()


def save_watchlist(symbols: list[str]):
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(symbols, f, indent=2)


def add_symbol(symbol: str) -> list[str]:
    wl = load_watchlist()
    symbol = symbol.upper()
    if symbol not in wl:
        wl.append(symbol)
        save_watchlist(wl)
    return wl


def remove_symbol(symbol: str) -> list[str]:
    wl = load_watchlist()
    symbol = symbol.upper()
    wl = [s for s in wl if s != symbol]
    save_watchlist(wl)
    return wl


def show_watchlist():
    wl = load_watchlist()
    print("\n=== SWING TRADE WATCHLIST ===")
    for i, sym in enumerate(wl, 1):
        print(f"  {i:>2}. {sym}")
    print(f"\n  Total: {len(wl)} stocks")
