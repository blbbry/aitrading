"""
Daily Trading Debrief Bot
=========================
Runs at 1:05pm PT every trading day (after market close).
Sends an AI-powered email summary to the user.
"""

import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import anthropic
import urllib.request
import urllib.error
import json

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")

RECIPIENT_EMAIL = "zw@alexwang.com"
SENDER_EMAIL    = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")
RESEND_KEY      = os.environ.get("RESEND_API_KEY", "")


# ── Data gathering ─────────────────────────────────────────────────────────────

def get_todays_trades() -> list[dict]:
    db_path = os.environ.get("DB_PATH", "/data/portfolio.db" if os.path.isdir("/data") else "portfolio.db")
    if not os.path.exists(db_path):
        return []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    today = datetime.utcnow().strftime("%Y-%m-%d")
    rows = con.execute(
        "SELECT * FROM trades WHERE ts LIKE ? ORDER BY id DESC", (f"{today}%",)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_portfolio_snapshot() -> dict:
    import portfolio
    from tools.market_data import get_current_price
    positions = portfolio.get_positions()
    meta      = portfolio.get_all_position_meta()
    prices    = {sym: get_current_price(sym) for sym in positions}
    prices    = {k: v for k, v in prices.items() if v}
    summary   = portfolio.get_portfolio_summary(prices)

    pos_details = []
    for sym, pos in positions.items():
        price    = prices.get(sym, pos["avg_cost"])
        pnl_pct  = (price - pos["avg_cost"]) / pos["avg_cost"] * 100
        m        = meta.get(sym, {})
        days_held = 0
        if m.get("entry_date"):
            try:
                days_held = (datetime.utcnow() - datetime.fromisoformat(m["entry_date"])).days
            except Exception:
                pass
        pos_details.append({
            "symbol":      sym,
            "shares":      pos["shares"],
            "avg_cost":    pos["avg_cost"],
            "price":       price,
            "pnl_pct":     round(pnl_pct, 2),
            "pnl_dollar":  round((price - pos["avg_cost"]) * pos["shares"], 2),
            "stop_loss":   m.get("stop_loss"),
            "take_profit": m.get("take_profit"),
            "swing_score": m.get("swing_score"),
            "days_held":   days_held,
        })

    return {
        "cash":           round(summary["cash"], 2),
        "total_equity":   round(summary["total_equity"], 2),
        "market_value":   round(summary["total_market_value"], 2),
        "unrealized_pnl": round(summary["unrealized_pnl"], 2),
        "positions":      pos_details,
    }


def get_recent_auto_logs() -> list[str]:
    """Pull last 50 auto-trader log entries."""
    try:
        import auto_trader
        return [e["msg"] for e in auto_trader.state["log"][:50]]
    except Exception:
        return []


# ── AI Analysis ───────────────────────────────────────────────────────────────

def generate_ai_debrief(snapshot: dict, trades: list[dict], logs: list[str]) -> str:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    today_str = datetime.now(PT).strftime("%A, %B %d %Y")

    # Format positions
    pos_text = ""
    for p in snapshot["positions"]:
        sign = "+" if p["pnl_pct"] >= 0 else ""
        pos_text += f"  {p['symbol']}: {sign}{p['pnl_pct']}% (${p['pnl_dollar']:+.2f}) | held {p['days_held']} days | stop=${p['stop_loss']} target=${p['take_profit']}\n"
    if not pos_text:
        pos_text = "  No open positions\n"

    # Format trades
    trade_text = ""
    for t in trades:
        trade_text += f"  {t['action']} {t['shares']:.2f}x {t['symbol']} @ ${t['price']:.2f} — {t['reason']}\n"
    if not trade_text:
        trade_text = "  No trades executed today\n"

    # Format logs
    log_text = "\n".join(logs[:20]) if logs else "No logs available"

    prompt = f"""You are an expert swing trading analyst reviewing today's automated trading activity.

Date: {today_str}

PORTFOLIO SNAPSHOT (end of day):
  Total Equity:    ${snapshot['total_equity']:,.2f}
  Cash:            ${snapshot['cash']:,.2f}
  Market Value:    ${snapshot['market_value']:,.2f}
  Unrealized P&L:  ${snapshot['unrealized_pnl']:+,.2f}

OPEN POSITIONS:
{pos_text}

TODAY'S TRADES:
{trade_text}

AUTO-TRADER ACTIVITY LOGS:
{log_text}

Write a friendly, clear daily trading debrief email for a beginner swing trader. Include:

1. 📊 PORTFOLIO SUMMARY — how did today go overall, equity change, mood (good/neutral/bad day)

2. 📂 POSITION REVIEW — for each open position:
   - Is the trade thesis still valid?
   - Is it close to stop or target?
   - Should we be watching it closely tomorrow?

3. ✅ WHAT WENT WELL — specific things the bot did right today

4. ⚠️ WHAT COULD BE BETTER — honest critique of any mistakes or missed opportunities

5. 🔮 TOMORROW'S PLAN — what to watch, any setups forming, market conditions to be aware of

6. 💡 ONE LESSON — one simple swing trading lesson based on today's activity

Keep it conversational, encouraging, and educational. Use emojis. Max 400 words."""

    response = client.messages.create(
        model="claude-haiku-4-5",   # cheapest model — saves credits
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


# ── Email sending ─────────────────────────────────────────────────────────────

def send_email(subject: str, body: str) -> bool:
    if not RESEND_KEY:
        print("[Debrief] No RESEND_API_KEY set — skipping email")
        return False

    payload = json.dumps({
        "from":    f"AI Swing Trader <{SENDER_EMAIL}>",
        "to":      [RECIPIENT_EMAIL],
        "subject": subject,
        "text":    body,
    }).encode()

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_KEY}",
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = resp.read().decode()
            print(f"[Debrief] ✅ Email sent! Status: {resp.status} | {result}")
            return True
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        print(f"[Debrief] ❌ Email failed: {e.code} | {err}")
        return False
    except Exception as e:
        print(f"[Debrief] ❌ Email error: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def run_debrief():
    print("[Debrief] Generating daily trading debrief...")
    today = datetime.now(PT).strftime("%a %b %d")

    snapshot = get_portfolio_snapshot()
    trades   = get_todays_trades()
    logs     = get_recent_auto_logs()

    print(f"[Debrief] Portfolio: ${snapshot['total_equity']:,.2f} | Trades today: {len(trades)} | Positions: {len(snapshot['positions'])}")

    analysis = generate_ai_debrief(snapshot, trades, logs)

    subject = f"📈 Trading Debrief — {today} | Equity ${snapshot['total_equity']:,.2f}"
    body    = f"{analysis}\n\n---\nAI Swing Trader | https://aitrading.fly.dev"

    print(f"\n[Debrief] === DEBRIEF PREVIEW ===\n{body}\n")
    send_email(subject, body)
    print("[Debrief] Done!")
    return body


if __name__ == "__main__":
    run_debrief()
