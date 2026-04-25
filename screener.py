"""
Swing Trade Screener
Scans the watchlist for the best current swing trade setups.
"""
import concurrent.futures
from tabulate import tabulate
from watchlist import load_watchlist
from strategies.swing import score_swing_setup


def run_screen(symbols: list[str] = None, min_score: int = 55, top_n: int = 10) -> list[dict]:
    symbols = symbols or load_watchlist()
    print(f"\n[Screener] Scanning {len(symbols)} stocks for swing setups...")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(score_swing_setup, sym): sym for sym in symbols}
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            sym = futures[future]
            try:
                result = future.result()
                results.append(result)
                action = result.get("action", "?")
                score = result.get("score", 0)
                print(f"  [{i:>2}/{len(symbols)}] {sym:<6} score={score:>3}  {action}")
            except Exception as e:
                print(f"  [{i:>2}/{len(symbols)}] {sym:<6} ERROR: {e}")

    # Sort by score descending
    results.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Filter to actionable setups
    top = [r for r in results if r.get("score", 0) >= min_score and "error" not in r][:top_n]
    return top


def print_screen_results(results: list[dict]):
    if not results:
        print("\nNo strong swing setups found right now. Market may be extended or choppy.")
        return

    print(f"\n{'='*70}")
    print(f"  TOP SWING TRADE SETUPS")
    print(f"{'='*70}")

    rows = []
    for r in results:
        rows.append([
            r["symbol"],
            r["action"],
            f"{r['score']}/100",
            f"${r['current_price']:.2f}" if r.get("current_price") else "N/A",
            f"{r['rsi']:.0f}" if r.get("rsi") else "N/A",
            f"${r['stop_loss']:.2f}" if r.get("stop_loss") else "N/A",
            f"${r['take_profit']:.2f}" if r.get("take_profit") else "N/A",
            f"{r['reward_pct']:+.1f}%" if r.get("reward_pct") else "N/A",
            f"{r['bullish_signals']}✅ {r['bearish_signals']}❌",
        ])

    headers = ["Symbol", "Action", "Score", "Price", "RSI", "Stop Loss", "Target", "Upside", "Signals"]
    print(tabulate(rows, headers=headers, tablefmt="rounded_outline"))

    print("\n📋 KEY SIGNALS BY STOCK:")
    for r in results:
        print(f"\n  {r['symbol']} ({r['action']}, score {r['score']}):")
        for sig in r["signals"]:
            icon = "  ✅" if sig["bullish"] else "  ⚠️ "
            print(f"    {icon} {sig['detail']}")
