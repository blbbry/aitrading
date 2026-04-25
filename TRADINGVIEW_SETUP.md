# TradingView → AI Bot Integration Setup

## Step 1: Add your Anthropic API key

Copy `.env.example` to `.env` and fill it in:
```
ANTHROPIC_API_KEY=sk-ant-...
STARTING_CASH=100000
```

## Step 2: Start the webhook server

```bash
cd /Users/temp/aitrading
source venv/bin/activate
python main.py server
```

Server runs at: **http://localhost:8000**

## Step 3: Expose it publicly with ngrok

Install ngrok (https://ngrok.com) then:
```bash
ngrok http 8000
```

You'll get a URL like: `https://abc123.ngrok-free.app`

Your TradingView webhook URL = `https://abc123.ngrok-free.app/webhook/tradingview`

---

## Step 4: Set up TradingView Alerts

In TradingView, create an alert on any chart and set the **Webhook URL** and **Message** as follows.

### Alert Message (JSON) — copy this exactly:
```json
{
  "symbol": "{{ticker}}",
  "action": "{{strategy.order.action}}",
  "price": {{close}},
  "interval": "{{interval}}",
  "time": "{{time}}",
  "signal": "YOUR_SIGNAL_NAME"
}
```

Replace `YOUR_SIGNAL_NAME` with one of these (helps the bots understand the trigger):
- `RSI_OVERSOLD` — RSI crossed below 30
- `RSI_RECOVERY` — RSI crossed back above 35
- `MACD_BULLISH_CROSS` — MACD line crossed above signal
- `GOLDEN_CROSS` — 20MA crossed above 50MA
- `BREAKOUT` — Price broke above key resistance
- `VOLUME_SURGE` — Unusual volume detected
- `PULLBACK_BUY` — Pullback to moving average support

### Recommended TradingView Alerts to Create (Swing Trading):

| Indicator | Condition | Signal Name |
|-----------|-----------|-------------|
| RSI(14) | Crossing Down 30 | RSI_OVERSOLD |
| RSI(14) | Crossing Up 35 | RSI_RECOVERY |
| MACD | Histogram turns positive | MACD_BULLISH_CROSS |
| EMA20 | Crossing Up EMA50 | GOLDEN_CROSS |
| EMA20 | Crossing Down EMA50 | DEATH_CROSS |
| Volume | Greater than 1.5x 20-day avg | VOLUME_SURGE |
| Bollinger Bands | Price crosses lower band | BB_LOWER_TOUCH |

---

## Step 5: Test the webhook

```bash
curl -X POST http://localhost:8000/webhook/tradingview \
  -H "Content-Type: application/json" \
  -d '{"symbol": "AAPL", "action": "BUY", "price": 195.50, "signal": "RSI_RECOVERY"}'
```

---

## CLI Commands (no webhook needed)

```bash
# Scan all watchlist stocks for best swing setups (recommended daily)
python main.py screen

# Analyze specific stocks with full bot team
python main.py analyze NVDA AAPL MSFT

# Review open positions — bots decide hold or sell
python main.py review

# Check portfolio
python main.py portfolio

# View trade history
python main.py history

# Manage watchlist
python main.py watchlist
python main.py watchlist add COIN
python main.py watchlist remove SOXS
```

---

## API Endpoints (when server is running)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/webhook/tradingview` | TradingView alert receiver |
| GET | `/portfolio` | Current portfolio JSON |
| GET | `/screen` | Screen watchlist, auto-analyze top 3 |
| POST | `/analyze/AAPL` | Trigger analysis for AAPL |
| GET | `/review` | Review all open positions |
| GET | `/watchlist` | Get watchlist |
| POST | `/watchlist/TSLA` | Add TSLA to watchlist |
| GET | `/logs` | Recent webhook & analysis events |
| GET | `/history` | Trade history |

---

## How the Bot Team Works

```
TradingView Alert
      ↓
  Webhook Server  ←── or ──→  CLI: python main.py analyze AAPL
      ↓
  SwingTraderBot (orchestrator)
   ├── ResearchBot      → news, fundamentals, analyst targets
   ├── TechnicalBot     → RSI, MACD, MAs, Bollinger Bands
   ├── RiskManagerBot   → position size, stop-loss, portfolio limits
   └── Swing Score      → quantitative signal scoring (0-100)
      ↓
  Decision: BUY / SELL / HOLD
      ↓
  Paper Trade Executed → SQLite portfolio
```

### Risk Rules (auto-enforced):
- Max 10% of portfolio in any single stock
- Risk max 1% of equity per trade (ATR-based sizing)
- Stop loss = entry − 2×ATR
- Take profit = entry + 3×ATR (3:1 reward/risk)
- No buys if cash < $500
