#!/usr/bin/env python3
"""
AI Paper Trading Team — Swing Trading Mode
===========================================
Commands:
  python main.py screen                    # Scan watchlist for best swing setups
  python main.py analyze AAPL             # Full team analysis + auto-trade
  python main.py analyze AAPL MSFT NVDA   # Analyze multiple stocks
  python main.py review                   # Review all open positions (hold/sell)
  python main.py portfolio                # Show portfolio snapshot
  python main.py history                  # Show trade history
  python main.py watchlist                # Show/manage watchlist
  python main.py watchlist add TSLA       # Add stock to watchlist
  python main.py watchlist remove TSLA    # Remove stock from watchlist
  python main.py server                   # Start TradingView webhook server
  python main.py reset                    # Reset portfolio (start fresh)
"""
import sys
import os
from dotenv import load_dotenv

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_ENV_PATH)

STARTING_CASH = float(os.environ.get("STARTING_CASH", 100_000))

import portfolio

portfolio.init_db(starting_cash=STARTING_CASH)


def cmd_portfolio():
    from tools.market_data import get_current_price
    positions = portfolio.get_positions()
    prices = {sym: get_current_price(sym) for sym in positions}
    prices = {k: v for k, v in prices.items() if v}
    summary = portfolio.get_portfolio_summary(prices)
    print("\n╔══════════════════════════════╗")
    print("║     PAPER TRADING PORTFOLIO  ║")
    print("╚══════════════════════════════╝")
    print(f"  Cash:           ${summary['cash']:>12,.2f}")
    print(f"  Market Value:   ${summary['total_market_value']:>12,.2f}")
    print(f"  Total Equity:   ${summary['total_equity']:>12,.2f}")
    print(f"  Unrealized P&L: ${summary['unrealized_pnl']:>+12,.2f}")
    print()
    print(summary["positions_table"])


def cmd_history():
    print("\n=== TRADE HISTORY ===")
    print(portfolio.get_trade_history(limit=30))


def cmd_screen():
    from screener import run_screen, print_screen_results
    from watchlist import load_watchlist
    results = run_screen(load_watchlist(), min_score=50, top_n=8)
    print_screen_results(results)

    if results:
        print("\n🤖 Auto-analyzing top 3 setups with the full bot team...")
        from bots.swing_trader import SwingTraderBot
        trader = SwingTraderBot()
        for r in results[:3]:
            sym = r["symbol"]
            print(f"\n{'─'*60}")
            result = trader.analyze(sym, context={"trigger": "screen_auto"})
            print(f"\n🏁 DECISION [{sym}]:\n{result['decision']}")

    cmd_portfolio()


def cmd_analyze(symbols: list[str]):
    from bots.swing_trader import SwingTraderBot
    trader = SwingTraderBot()
    for symbol in symbols:
        symbol = symbol.upper()
        print(f"\n{'═'*60}")
        print(f"  SWING ANALYSIS: {symbol}")
        print(f"{'═'*60}")
        result = trader.analyze(symbol, context={"trigger": "manual"})
        print(f"\n🏁 DECISION:\n{result['decision']}")
        if result.get("stop_loss"):
            print(f"\n  Stop Loss:   ${result['stop_loss']:.2f}")
        if result.get("take_profit"):
            print(f"  Take Profit: ${result['take_profit']:.2f}")
    print()
    cmd_portfolio()


def cmd_review():
    from bots.swing_trader import SwingTraderBot
    trader = SwingTraderBot()
    print("\n=== REVIEWING OPEN POSITIONS ===")
    results = trader.review_positions()
    for r in results:
        print(f"\n[{r['symbol']}] {r['decision']}")
    print()
    cmd_portfolio()


def cmd_watchlist(args: list[str]):
    from watchlist import load_watchlist, add_symbol, remove_symbol, show_watchlist
    if not args:
        show_watchlist()
        return
    sub = args[0].lower()
    if sub == "add" and len(args) > 1:
        updated = add_symbol(args[1].upper())
        print(f"✅ Added {args[1].upper()}. Watchlist now has {len(updated)} stocks.")
    elif sub == "remove" and len(args) > 1:
        updated = remove_symbol(args[1].upper())
        print(f"✅ Removed {args[1].upper()}. Watchlist now has {len(updated)} stocks.")
    else:
        show_watchlist()


def cmd_server():
    print("\n🚀 Starting TradingView Webhook Server...")
    print("   URL: http://localhost:8000")
    print("   Webhook endpoint: POST http://localhost:8000/webhook/tradingview")
    print("   Portfolio API:    GET  http://localhost:8000/portfolio")
    print("   Screen API:       GET  http://localhost:8000/screen")
    print("\n   To expose publicly: ngrok http 8000")
    print("   Then set TradingView alert URL to: https://<ngrok-id>.ngrok.io/webhook/tradingview\n")
    os.execlp("uvicorn", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--reload")


def cmd_reset():
    db_path = os.path.join(os.path.dirname(__file__), "portfolio.db")
    if os.path.exists(db_path):
        confirm = input(f"Reset portfolio and delete all trade history? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return
        os.remove(db_path)
    portfolio.init_db(starting_cash=STARTING_CASH)
    print(f"✅ Portfolio reset. Starting cash: ${STARTING_CASH:,.2f}")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    cmd = args[0].lower()

    if cmd == "portfolio":
        cmd_portfolio()
    elif cmd == "history":
        cmd_history()
    elif cmd == "screen":
        cmd_screen()
    elif cmd == "review":
        cmd_review()
    elif cmd == "server":
        cmd_server()
    elif cmd == "reset":
        cmd_reset()
    elif cmd == "watchlist":
        cmd_watchlist(args[1:])
    elif cmd == "analyze":
        if len(args) < 2:
            print("Usage: python main.py analyze SYMBOL [SYMBOL ...]")
            sys.exit(1)
        cmd_analyze(args[1:])
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
