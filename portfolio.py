import sqlite3
import json
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
                entry_reason TEXT,
                entry_signals TEXT,
                entry_rsi REAL
            );
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                shares REAL NOT NULL,
                price REAL NOT NULL,
                reason TEXT,
                pnl REAL
            );
        """)
        # Migrations: add new columns to existing DBs
        existing_meta_cols = {
            r[1] for r in con.execute("PRAGMA table_info(position_meta)").fetchall()
        }
        if "entry_signals" not in existing_meta_cols:
            con.execute("ALTER TABLE position_meta ADD COLUMN entry_signals TEXT")
        if "entry_rsi" not in existing_meta_cols:
            con.execute("ALTER TABLE position_meta ADD COLUMN entry_rsi REAL")
        if "partial_taken" not in existing_meta_cols:
            con.execute("ALTER TABLE position_meta ADD COLUMN partial_taken INTEGER DEFAULT 0")

        # Alerts table — for alert mode (manual execution workflow)
        con.executescript("""
            CREATE TABLE IF NOT EXISTS trade_alerts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts            TEXT    NOT NULL,
                type          TEXT    NOT NULL,
                symbol        TEXT    NOT NULL,
                shares        REAL    NOT NULL,
                price         REAL    NOT NULL,
                stop_loss     REAL,
                take_profit   REAL,
                reason        TEXT,
                score         INTEGER,
                status        TEXT    NOT NULL DEFAULT 'pending',
                confirmed_at  TEXT,
                confirmed_price REAL
            );
        """)
        existing_trade_cols = {
            r[1] for r in con.execute("PRAGMA table_info(trades)").fetchall()
        }
        if "pnl" not in existing_trade_cols:
            con.execute("ALTER TABLE trades ADD COLUMN pnl REAL")

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
                      swing_score: int = None, entry_reason: str = "",
                      entry_signals: list = None, entry_rsi: float = None):
    now = datetime.utcnow().isoformat()
    signals_json = json.dumps(entry_signals) if entry_signals else None
    with _conn() as con:
        con.execute("""
            INSERT INTO position_meta
              (symbol, stop_loss, take_profit, entry_date, swing_score,
               entry_reason, entry_signals, entry_rsi)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol) DO UPDATE SET
                stop_loss=excluded.stop_loss,
                take_profit=excluded.take_profit,
                swing_score=excluded.swing_score,
                entry_reason=excluded.entry_reason,
                entry_signals=excluded.entry_signals,
                entry_rsi=excluded.entry_rsi
        """, (symbol, stop_loss, take_profit, now, swing_score,
              entry_reason, signals_json, entry_rsi))


def clear_position_meta(symbol: str):
    with _conn() as con:
        con.execute("DELETE FROM position_meta WHERE symbol=?", (symbol,))


def set_partial_taken(symbol: str):
    """Mark that a partial profit exit has been taken for this position."""
    with _conn() as con:
        con.execute(
            "UPDATE position_meta SET partial_taken=1 WHERE symbol=?", (symbol,)
        )


def import_positions(positions: list, total_equity: float):
    """
    Replace the entire portfolio with imported positions.
    positions: [{"symbol": "AAPL", "shares": 12, "avg_cost": 271.50}, ...]
    cash = total_equity - sum(shares * avg_cost)
    """
    invested = sum(p["shares"] * p["avg_cost"] for p in positions)
    cash = max(0, total_equity - invested)
    with _conn() as con:
        con.execute("DELETE FROM positions")
        con.execute("DELETE FROM position_meta")
        con.execute("UPDATE portfolio SET cash=? WHERE id=1", (cash,))
        for p in positions:
            con.execute(
                "INSERT INTO positions (symbol, shares, avg_cost) VALUES (?,?,?)",
                (p["symbol"], p["shares"], p["avg_cost"])
            )
            # Default stop/target: 5% below / 15% above avg cost
            stop   = round(p["avg_cost"] * 0.95, 2)
            target = round(p["avg_cost"] * 1.15, 2)
            con.execute("""
                INSERT INTO position_meta (symbol, stop_loss, take_profit, entry_date, entry_reason)
                VALUES (?, ?, ?, ?, 'Imported from Robinhood')
            """, (p["symbol"], stop, target, datetime.utcnow().isoformat()))
    return {"cash": round(cash, 2), "positions": len(positions)}


# ── Alert helpers ─────────────────────────────────────────────────────────────

def create_alert(type: str, symbol: str, shares: float, price: float,
                 stop_loss: float = None, take_profit: float = None,
                 reason: str = "", score: int = None) -> int:
    with _conn() as con:
        cur = con.execute("""
            INSERT INTO trade_alerts (ts, type, symbol, shares, price, stop_loss, take_profit, reason, score)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (datetime.utcnow().isoformat(), type, symbol, shares, price,
              stop_loss, take_profit, reason, score))
        return cur.lastrowid


def get_alerts(status: str = "pending") -> list:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM trade_alerts WHERE status=? ORDER BY id DESC", (status,)
        ).fetchall()
        return [dict(r) for r in rows]


def confirm_alert(alert_id: int, confirmed_price: float = None) -> dict:
    """Execute the trade in the paper portfolio and mark alert confirmed."""
    with _conn() as con:
        row = con.execute("SELECT * FROM trade_alerts WHERE id=?", (alert_id,)).fetchone()
    if not row:
        return {"ok": False, "error": "Alert not found"}
    alert = dict(row)
    if alert["status"] != "pending":
        return {"ok": False, "error": f"Alert already {alert['status']}"}

    exec_price = confirmed_price or alert["price"]
    if alert["type"] == "BUY":
        result = buy(alert["symbol"], alert["shares"], exec_price,
                     reason=f"Confirmed: {alert['reason']}",
                     stop_loss=alert["stop_loss"], take_profit=alert["take_profit"],
                     swing_score=alert["score"])
    else:
        result = sell(alert["symbol"], alert["shares"], exec_price,
                      reason=f"Confirmed: {alert['reason']}")

    if result.get("ok"):
        with _conn() as con:
            con.execute(
                "UPDATE trade_alerts SET status='confirmed', confirmed_at=?, confirmed_price=? WHERE id=?",
                (datetime.utcnow().isoformat(), exec_price, alert_id)
            )
    return result


def dismiss_alert(alert_id: int):
    with _conn() as con:
        con.execute("UPDATE trade_alerts SET status='dismissed' WHERE id=?", (alert_id,))


def raise_stop_to_breakeven(symbol: str, avg_cost: float):
    """Raise the stop-loss to breakeven (avg cost) after a partial exit."""
    with _conn() as con:
        con.execute(
            "UPDATE position_meta SET stop_loss=MAX(stop_loss, ?) WHERE symbol=?",
            (round(avg_cost, 2), symbol)
        )


def buy(symbol: str, shares: float, price: float, reason: str = "",
        stop_loss: float = None, take_profit: float = None, swing_score: int = None,
        entry_signals: list = None, entry_rsi: float = None) -> dict:
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
        set_position_meta(symbol, stop_loss, take_profit, swing_score, reason,
                          entry_signals=entry_signals, entry_rsi=entry_rsi)
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
            "INSERT INTO trades (ts, symbol, action, shares, price, reason, pnl) VALUES (?,?,?,?,?,?,?)",
            (datetime.utcnow().isoformat(), symbol, "SELL", shares, price, reason, pnl),
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
