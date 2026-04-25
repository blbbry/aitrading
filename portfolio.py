import sqlite3
import os
from datetime import datetime
from tabulate import tabulate


# Use /data volume on Fly.io, fallback to local for development
_DATA_DIR = "/data" if os.path.isdir("/data") else os.path.dirname(__file__)
DB_PATH = os.environ.get("DB_PATH", os.path.join(_DATA_DIR, "portfolio.db"))


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db(starting_cash: float = 100_000.0):
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS portfolio (
                id INTEGER PRIMARY KEY,
                cash REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                shares REAL NOT NULL,
                avg_cost REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS position_meta (
                symbol TEXT PRIMARY KEY,
                stop_loss REAL,
                take_profit REAL,
                entry_date TEXT,
                swing_score INTEGER,
                entry_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                shares REAL NOT NULL,
                price REAL NOT NULL,
                reason TEXT
            );
        """)
        row = con.execute("SELECT id FROM portfolio").fetchone()
        if not row:
            con.execute("INSERT INTO portfolio (id, cash) VALUES (1, ?)", (starting_cash,))


def get_cash() -> float:
    with _conn() as con:
        return con.execute("SELECT cash FROM portfolio WHERE id=1").fetchone()["cash"]


def get_positions() -> dict:
    with _conn() as con:
        rows = con.execute("SELECT symbol, shares, avg_cost FROM positions").fetchall()
        return {r["symbol"]: {"shares": r["shares"], "avg_cost": r["avg_cost"]} for r in rows}


def get_position_meta(symbol: str) -> dict:
    with _conn() as con:
        row = con.execute("SELECT * FROM position_meta WHERE symbol=?", (symbol,)).fetchone()
        return dict(row) if row else {}


def get_all_position_meta() -> dict:
    with _conn() as con:
        rows = con.execute("SELECT * FROM position_meta").fetchall()
        return {r["symbol"]: dict(r) for r in rows}


def set_position_meta(symbol: str, stop_loss: float, take_profit: float,
                      swing_score: int = None, entry_reason: str = ""):
    now = datetime.utcnow().isoformat()
    with _conn() as con:
        con.execute("""
            INSERT INTO position_meta (symbol, stop_loss, take_profit, entry_date, swing_score, entry_reason)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(symbol) DO UPDATE SET
                stop_loss=excluded.stop_loss,
                take_profit=excluded.take_profit,
                swing_score=excluded.swing_score,
                entry_reason=excluded.entry_reason
        """, (symbol, stop_loss, take_profit, now, swing_score, entry_reason))


def clear_position_meta(symbol: str):
    with _conn() as con:
        con.execute("DELETE FROM position_meta WHERE symbol=?", (symbol,))


def buy(symbol: str, shares: float, price: float, reason: str = "",
        stop_loss: float = None, take_profit: float = None, swing_score: int = None) -> dict:
    cost = shares * price
    with _conn() as con:
        cash = con.execute("SELECT cash FROM portfolio WHERE id=1").fetchone()["cash"]
        if cost > cash:
            return {"ok": False, "error": f"Insufficient cash (need ${cost:,.2f}, have ${cash:,.2f})"}
        con.execute("UPDATE portfolio SET cash = cash - ? WHERE id=1", (cost,))
        existing = con.execute("SELECT shares, avg_cost FROM positions WHERE symbol=?", (symbol,)).fetchone()
        if existing:
            total_shares = existing["shares"] + shares
            avg_cost = (existing["shares"] * existing["avg_cost"] + cost) / total_shares
            con.execute("UPDATE positions SET shares=?, avg_cost=? WHERE symbol=?", (total_shares, avg_cost, symbol))
        else:
            con.execute("INSERT INTO positions (symbol, shares, avg_cost) VALUES (?,?,?)", (symbol, shares, price))
        con.execute(
            "INSERT INTO trades (ts, symbol, action, shares, price, reason) VALUES (?,?,?,?,?,?)",
            (datetime.utcnow().isoformat(), symbol, "BUY", shares, price, reason),
        )
    if stop_loss or take_profit:
        set_position_meta(symbol, stop_loss, take_profit, swing_score, reason)
    return {"ok": True, "symbol": symbol, "shares": shares, "price": price, "cost": cost,
            "stop_loss": stop_loss, "take_profit": take_profit}


def sell(symbol: str, shares: float, price: float, reason: str = "") -> dict:
    with _conn() as con:
        existing = con.execute("SELECT shares, avg_cost FROM positions WHERE symbol=?", (symbol,)).fetchone()
        if not existing or existing["shares"] < shares:
            held = existing["shares"] if existing else 0
            return {"ok": False, "error": f"Not enough shares (have {held}, want to sell {shares})"}
        proceeds = shares * price
        pnl = (price - existing["avg_cost"]) * shares
        new_shares = existing["shares"] - shares
        if new_shares < 0.0001:
            con.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
            clear_position_meta(symbol)
        else:
            con.execute("UPDATE positions SET shares=? WHERE symbol=?", (new_shares, symbol))
        con.execute("UPDATE portfolio SET cash = cash + ? WHERE id=1", (proceeds,))
        con.execute(
            "INSERT INTO trades (ts, symbol, action, shares, price, reason) VALUES (?,?,?,?,?,?)",
            (datetime.utcnow().isoformat(), symbol, "SELL", shares, price, reason),
        )
    return {"ok": True, "symbol": symbol, "shares": shares, "price": price, "proceeds": proceeds, "pnl": pnl}


def get_portfolio_summary(current_prices: dict) -> dict:
    cash = get_cash()
    positions = get_positions()
    meta = get_all_position_meta()
    rows = []
    total_market_value = 0.0
    total_cost_basis = 0.0
    for symbol, pos in positions.items():
        price = current_prices.get(symbol, pos["avg_cost"])
        market_value = pos["shares"] * price
        cost_basis = pos["shares"] * pos["avg_cost"]
        unrealized_pnl = market_value - cost_basis
        pnl_pct = (unrealized_pnl / cost_basis * 100) if cost_basis else 0
        m = meta.get(symbol, {})
        rows.append([symbol, f"{pos['shares']:.2f}", f"${pos['avg_cost']:.2f}", f"${price:.2f}",
                      f"${market_value:,.2f}", f"${unrealized_pnl:+,.2f}", f"{pnl_pct:+.1f}%",
                      f"${m.get('stop_loss', 0):.2f}" if m.get("stop_loss") else "—",
                      f"${m.get('take_profit', 0):.2f}" if m.get("take_profit") else "—"])
        total_market_value += market_value
        total_cost_basis += cost_basis
    total_equity = cash + total_market_value
    headers = ["Symbol", "Shares", "Avg Cost", "Price", "Market Value", "Unrealized P&L", "P&L %", "Stop", "Target"]
    return {
        "cash": cash,
        "positions_table": tabulate(rows, headers=headers, tablefmt="rounded_outline") if rows else "No open positions.",
        "total_equity": total_equity,
        "total_market_value": total_market_value,
        "unrealized_pnl": total_market_value - total_cost_basis,
    }


def get_trade_history(limit: int = 20) -> str:
    with _conn() as con:
        rows = con.execute(
            "SELECT ts, symbol, action, shares, price, reason FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    if not rows:
        return "No trades yet."
    table = [[r["ts"][:19], r["symbol"], r["action"], f"{r['shares']:.2f}", f"${r['price']:.2f}", r["reason"]] for r in rows]
    return tabulate(table, headers=["Time", "Symbol", "Action", "Shares", "Price", "Reason"], tablefmt="rounded_outline")
