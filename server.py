"""
TradingView Webhook Server
Receives alerts from TradingView and triggers the SwingTraderBot team.

Run with:
  source venv/bin/activate
  uvicorn server:app --host 0.0.0.0 --port 8000 --reload

Then expose publicly with:
  ngrok http 8000

Set your TradingView alert webhook URL to:
  https://<your-ngrok-url>/webhook/tradingview
"""

import os
import json
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import portfolio
import auto_trader
from watchlist import load_watchlist, add_symbol, remove_symbol
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Init portfolio on startup
portfolio.init_db(starting_cash=float(os.environ.get("STARTING_CASH", 100_000)))

app = FastAPI(title="AI Swing Trading Bot", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the dashboard
_STATIC = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_STATIC), name="static")

# ── Auto-trader lifecycle ─────────────────────────────────────────────────────
_auto_trader_task  = None
_debrief_task      = None
PT = ZoneInfo("America/Los_Angeles")


async def run_daily_debrief_scheduler():
    """Runs at 1:05pm PT every weekday (5 min after market close)."""
    import debrief
    print("[Debrief] Scheduler started — will send debrief at 1:05pm PT on trading days")
    last_sent_date = None

    while True:
        try:
            now = datetime.now(PT)
            # Only on weekdays
            if now.weekday() < 5:
                # Fire at 13:05 PT (1:05pm)
                if now.hour == 13 and now.minute == 5:
                    today = now.date()
                    if last_sent_date != today:
                        print(f"[Debrief] 🕐 1:05pm PT — sending daily debrief for {today}")
                        try:
                            debrief.run_debrief()
                            last_sent_date = today
                        except Exception as e:
                            print(f"[Debrief] ❌ Error running debrief: {e}")
            await asyncio.sleep(30)   # check every 30 seconds
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[Debrief] Scheduler error: {e}")
            await asyncio.sleep(60)


@app.on_event("startup")
async def startup():
    global _auto_trader_task, _debrief_task
    _auto_trader_task = asyncio.create_task(auto_trader.run_auto_trader())
    _debrief_task     = asyncio.create_task(run_daily_debrief_scheduler())

@app.on_event("shutdown")
async def shutdown():
    if _auto_trader_task:
        _auto_trader_task.cancel()
    if _debrief_task:
        _debrief_task.cancel()

# In-memory log of recent webhook events
event_log: list[dict] = []


# ─────────────────────────────────────────────────────────────────────────────
# Background task: run full bot team analysis
# ─────────────────────────────────────────────────────────────────────────────
def run_swing_analysis(symbol: str, trigger: str, action_hint: str = None):
    """Runs in a background thread — full team analysis + auto-trade."""
    from bots.swing_trader import SwingTraderBot
    try:
        bot = SwingTraderBot()
        result = bot.analyze(symbol, context={"trigger": trigger, "action_hint": action_hint})
        log_event({
            "type": "analysis_complete",
            "symbol": symbol,
            "trigger": trigger,
            "swing_score": result.get("swing_score"),
            "decision": result.get("decision"),
        })
        print(f"\n[Webhook] Analysis complete for {symbol}:\n{result['decision']}\n")
    except Exception as e:
        print(f"[Webhook] ERROR analyzing {symbol}: {e}")
        log_event({"type": "error", "symbol": symbol, "error": str(e)})


def log_event(event: dict):
    event["ts"] = datetime.utcnow().isoformat()
    event_log.insert(0, event)
    if len(event_log) > 100:
        event_log.pop()


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return FileResponse(os.path.join(_STATIC, "index.html"))


@app.post("/webhook/tradingview")
async def tradingview_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receives TradingView alert webhooks.

    Expected JSON payload from TradingView alert message:
    {
        "symbol": "{{ticker}}",
        "action": "{{strategy.order.action}}",   // BUY or SELL
        "price": {{close}},
        "interval": "{{interval}}",
        "time": "{{time}}",
        "signal": "RSI_OVERSOLD"                 // optional — your label
    }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    symbol = body.get("symbol", "").upper().replace("NASDAQ:", "").replace("NYSE:", "").replace("AMEX:", "")
    action = body.get("action", "").upper()
    price = body.get("price")
    signal = body.get("signal", "tradingview_alert")
    interval = body.get("interval", "unknown")

    if not symbol:
        raise HTTPException(status_code=400, detail="Missing 'symbol' in payload")

    log_event({
        "type": "webhook_received",
        "symbol": symbol,
        "action": action,
        "price": price,
        "signal": signal,
        "interval": interval,
        "raw": body,
    })

    print(f"\n[Webhook] ⚡ Alert received: {symbol} | {action} | signal={signal} | price={price}")

    # Add to watchlist if not already there
    watchlist = load_watchlist()
    if symbol not in watchlist:
        add_symbol(symbol)
        print(f"[Webhook] Added {symbol} to watchlist")

    # Kick off background analysis — non-blocking
    background_tasks.add_task(run_swing_analysis, symbol, f"tv_alert:{signal}", action)

    return JSONResponse({
        "status": "accepted",
        "symbol": symbol,
        "action": action,
        "message": f"Bot team analysis triggered for {symbol}",
    })


@app.get("/autopilot")
def get_autopilot():
    return {
        "enabled": auto_trader.state["enabled"],
        "force_mode": auto_trader.state["force_mode"],
        "market_open": auto_trader.is_market_open(),
        "last_screen": auto_trader.state["last_screen"],
        "last_check": auto_trader.state["last_check"],
        "trades_today": auto_trader.state["trades_today"],
        "config": {
            "min_buy_score": auto_trader.MIN_BUY_SCORE,
            "max_positions": auto_trader.MAX_POSITIONS,
            "max_rsi_buy": auto_trader.MAX_RSI_BUY,
            "sell_rsi_overbought": auto_trader.SELL_RSI_OVERBOUGHT,
            "max_hold_days": auto_trader.MAX_HOLD_DAYS,
            "screen_interval_min": auto_trader.SCREEN_INTERVAL_MIN,
            "check_interval_min": auto_trader.CHECK_INTERVAL_MIN,
        },
    }


@app.post("/autopilot/enable")
def enable_autopilot():
    auto_trader.state["enabled"] = True
    auto_trader._log("🟢 Auto-pilot ENABLED via dashboard")
    log_event({"type": "autopilot_enabled"})
    return {"enabled": True, "message": "Auto-pilot enabled — bots will buy/sell automatically during market hours"}


@app.post("/autopilot/disable")
def disable_autopilot():
    auto_trader.state["enabled"] = False
    auto_trader._log("🔴 Auto-pilot DISABLED via dashboard")
    log_event({"type": "autopilot_disabled"})
    return {"enabled": False, "message": "Auto-pilot disabled — bots will only act on manual triggers"}


@app.get("/autopilot/logs")
def get_autopilot_logs(limit: int = 30):
    return {"logs": auto_trader.state["log"][:limit]}


@app.post("/autopilot/force-mode/enable")
def enable_force_mode():
    auto_trader.state["force_mode"] = True
    auto_trader._log("⚠️  FORCE MODE enabled — ignoring market hours")
    return {"force_mode": True, "message": "Force mode ON — auto-pilot will run regardless of market hours"}

@app.post("/autopilot/force-mode/disable")
def disable_force_mode():
    auto_trader.state["force_mode"] = False
    auto_trader._log("✅ Force mode disabled — market hours enforced")
    return {"force_mode": False, "message": "Force mode OFF — market hours enforced"}

@app.post("/autopilot/scan-now")
async def force_scan(background_tasks: BackgroundTasks):
    """Force an immediate buy scan regardless of schedule."""
    background_tasks.add_task(auto_trader.run_buy_scan)
    return {"message": "Immediate scan triggered"}


@app.post("/autopilot/check-now")
async def force_check(background_tasks: BackgroundTasks):
    """Force an immediate position check."""
    background_tasks.add_task(auto_trader.run_position_check)
    return {"message": "Immediate position check triggered"}


@app.get("/portfolio")
def get_portfolio():
    from tools.market_data import get_current_price
    positions = portfolio.get_positions()
    meta = portfolio.get_all_position_meta()
    prices = {sym: get_current_price(sym) for sym in positions}
    prices = {k: v for k, v in prices.items() if v}
    summary = portfolio.get_portfolio_summary(prices)

    enriched_positions = {}
    for sym, pos in positions.items():
        price = prices.get(sym, pos["avg_cost"])
        avg_cost = pos["avg_cost"]
        m = meta.get(sym, {})
        pnl = (price - avg_cost) * pos["shares"]
        pnl_pct = (price - avg_cost) / avg_cost * 100 if avg_cost else 0
        enriched_positions[sym] = {
            **pos,
            "current_price": round(price, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "stop_loss": m.get("stop_loss"),
            "take_profit": m.get("take_profit"),
            "entry_date": m.get("entry_date"),
            "swing_score": m.get("swing_score"),
        }

    return {
        "cash": round(summary["cash"], 2),
        "total_equity": round(summary["total_equity"], 2),
        "total_market_value": round(summary["total_market_value"], 2),
        "unrealized_pnl": round(summary["unrealized_pnl"], 2),
        "positions": enriched_positions,
    }


@app.get("/history")
def get_history():
    import sqlite3
    db_path = os.path.join(os.path.dirname(__file__), "portfolio.db")
    if not os.path.exists(db_path):
        return {"trades": []}
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT ts, symbol, action, shares, price, reason FROM trades ORDER BY id DESC LIMIT 50"
    ).fetchall()
    con.close()
    return {"trades": [dict(r) for r in rows]}


@app.get("/screen")
async def screen_watchlist(background_tasks: BackgroundTasks, top_n: int = 5):
    """Trigger a full watchlist screen and return top setups."""
    from screener import run_screen
    watchlist = load_watchlist()
    results = run_screen(watchlist, min_score=50, top_n=top_n)
    simplified = [
        {
            "symbol": r["symbol"],
            "score": r["score"],
            "action": r["action"],
            "price": r.get("current_price"),
            "rsi": r.get("rsi"),
            "stop_loss": r.get("stop_loss"),
            "take_profit": r.get("take_profit"),
            "upside_pct": r.get("reward_pct"),
            "top_signals": [s["signal"] for s in r.get("signals", []) if s["bullish"]][:3],
        }
        for r in results
    ]

    # No auto-Claude analysis — saves credits. Use manual Analyze button if needed.
    return {"setups": simplified, "scanned": len(watchlist), "message": f"Top {len(simplified)} setups found."}


@app.post("/analyze/{symbol}")
async def analyze_symbol(symbol: str, background_tasks: BackgroundTasks):
    """Manually trigger full team analysis on a specific stock."""
    symbol = symbol.upper()
    background_tasks.add_task(run_swing_analysis, symbol, "manual_api")
    log_event({"type": "manual_trigger", "symbol": symbol})
    return {"status": "triggered", "symbol": symbol, "message": "Bot team analysis started in background"}


@app.get("/watchlist")
def get_watchlist():
    return {"watchlist": load_watchlist()}


@app.post("/watchlist/{symbol}")
def add_to_watchlist(symbol: str):
    updated = add_symbol(symbol.upper())
    return {"watchlist": updated}


@app.delete("/watchlist/{symbol}")
def remove_from_watchlist(symbol: str):
    updated = remove_symbol(symbol.upper())
    return {"watchlist": updated}


@app.get("/logs")
def get_logs(limit: int = 20):
    # Merge webhook event_log + auto_trader log into one unified feed
    at_logs = [{"type": e["level"], "msg": e["msg"], "ts": e["ts"], "source": "auto"} for e in auto_trader.state["log"][:limit]]
    wh_logs = [{"ts": e.get("ts",""), "type": e.get("type",""), "symbol": e.get("symbol",""), "signal": e.get("signal",""), "source": "webhook"} for e in event_log[:limit]]
    combined = sorted(at_logs + wh_logs, key=lambda x: x.get("ts",""), reverse=True)
    return {"events": combined[:limit]}


@app.post("/debrief/send-now")
async def send_debrief_now(background_tasks: BackgroundTasks):
    """Manually trigger the daily debrief email right now (for testing)."""
    import debrief
    background_tasks.add_task(debrief.run_debrief)
    return {"message": "Debrief email triggered — check your inbox in ~30 seconds!"}


@app.get("/review")
async def review_positions(background_tasks: BackgroundTasks):
    """Review all open positions and decide to hold or sell."""
    positions = portfolio.get_positions()
    if not positions:
        return {"message": "No open positions to review"}
    for symbol in positions:
        background_tasks.add_task(run_swing_analysis, symbol, "position_review")
    return {"message": f"Reviewing {len(positions)} positions in background", "symbols": list(positions.keys())}
