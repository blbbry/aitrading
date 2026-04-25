"""
SwingTraderBot — specialized for multi-day swing trades.
Combines team analysis with swing-specific strategy signals.
"""
import re
from .base_bot import BaseBot
from .researcher import ResearchBot
from .technical import TechnicalAnalystBot
from .risk_manager import RiskManagerBot
from tools.market_data import get_current_price
from strategies.swing import score_swing_setup
import portfolio

TOOLS = [
    {
        "name": "execute_buy",
        "description": "Execute a paper buy order.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "shares": {"type": "number"},
                "reason": {"type": "string"},
            },
            "required": ["symbol", "shares", "reason"],
        },
    },
    {
        "name": "execute_sell",
        "description": "Execute a paper sell order.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "shares": {"type": "number"},
                "reason": {"type": "string"},
            },
            "required": ["symbol", "shares", "reason"],
        },
    },
    {
        "name": "get_current_price",
        "description": "Get current market price.",
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
]

SYSTEM = """You are SwingTraderBot, a specialized swing trading AI.

Strategy: Swing trading — hold positions for 3 to 15 trading days targeting 5-20% gains.

You receive:
- A quantitative swing score (0-100) with specific signals
- Fundamental research brief
- Technical analysis brief
- Risk management assessment with exact share count

Your decision rules:
BUY when ALL of:
  ✅ Swing score >= 55
  ✅ At least 2 bullish technical signals
  ✅ RiskManagerBot says APPROVED
  ✅ No major negative news catalyst
  ✅ RSI not above 72 (not overbought)

SELL an existing position when ANY of:
  🔴 RSI crosses above 75 (take profit)
  🔴 MACD turns bearish crossover
  🔴 Price drops below stop-loss level
  🔴 Swing score drops below 30
  🔴 Strong negative fundamental catalyst

HOLD when signals are mixed or uncertain.

If you BUY:
- Use EXACTLY the share count from RiskManagerBot (never exceed it)
- State the stop-loss and take-profit targets clearly

Output format:
ACTION: [BUY/SELL/HOLD]
SHARES: [number or N/A]
STOP LOSS: [$price]
TAKE PROFIT: [$price]
HOLD PERIOD: [estimated days]
RATIONALE: [2-3 clear sentences]
"""


class SwingTraderBot(BaseBot):
    name = "SwingTraderBot"
    role = "Swing trader: 3-15 day holds, targets 5-20% gains"

    def __init__(self):
        super().__init__()
        self.researcher = ResearchBot()
        self.technical = TechnicalAnalystBot()
        self.risk_manager = RiskManagerBot()
        self._current_prices: dict[str, float] = {}

    def _handle_tool(self, name: str, inputs: dict):
        symbol = inputs.get("symbol", "")
        price = self._current_prices.get(symbol) or get_current_price(symbol)
        if name == "get_current_price":
            return {"price": price}
        if name == "execute_buy":
            if not price:
                return {"ok": False, "error": "Could not fetch current price"}
            return portfolio.buy(symbol, inputs["shares"], price, inputs.get("reason", ""))
        if name == "execute_sell":
            if not price:
                return {"ok": False, "error": "Could not fetch current price"}
            return portfolio.sell(symbol, inputs["shares"], price, inputs.get("reason", ""))
        raise ValueError(f"Unknown tool: {name}")

    def analyze(self, symbol: str, context: dict = None) -> dict:
        symbol = symbol.upper()
        trigger = (context or {}).get("trigger", "manual")

        print(f"\n[SwingTraderBot] Analyzing {symbol} (trigger: {trigger})")

        price = get_current_price(symbol)
        if price:
            self._current_prices[symbol] = price
        print(f"  Price: ${price:.2f}" if price else "  Price: unavailable")

        print("  → Scoring swing setup...")
        swing = score_swing_setup(symbol)

        print("  → ResearchBot running...")
        research = self.researcher.analyze(symbol)

        print("  → TechnicalAnalystBot running...")
        technical = self.technical.analyze(symbol)

        print("  → RiskManagerBot running...")
        risk = self.risk_manager.analyze(symbol, context={"proposed_action": "BUY", "current_price": price})

        # Format swing signals for the AI
        signal_lines = "\n".join(
            f"  {'✅' if s['bullish'] else '⚠️ '} [{s['signal']}] {s['detail']}"
            for s in swing.get("signals", [])
        )

        briefing = f"""
=== SWING TRADE ANALYSIS: {symbol} ===

SWING SCORE: {swing.get('score', 0)}/100  |  ACTION SIGNAL: {swing.get('action', 'N/A')}
Current Price: ${price:.2f if price else 'N/A'}
RSI: {swing.get('rsi', 'N/A')}
ATR (14): ${swing.get('atr', 'N/A')}
Suggested Stop Loss: ${swing.get('stop_loss', 'N/A')}
Suggested Take Profit: ${swing.get('take_profit', 'N/A')}
Potential Upside: {swing.get('reward_pct', 'N/A')}%  |  Risk: {swing.get('risk_pct', 'N/A')}%
Bullish Signals: {swing.get('bullish_signals', 0)}  |  Bearish Signals: {swing.get('bearish_signals', 0)}

SIGNALS:
{signal_lines}

--- FUNDAMENTAL RESEARCH ---
{research['report']}

--- TECHNICAL ANALYSIS ---
{technical['report']}

--- RISK ASSESSMENT ---
{risk['report']}

=== END BRIEFING ===

Trigger: {trigger}
Existing position: {'YES — ' + str(portfolio.get_positions().get(symbol, {}).get('shares', 0)) + ' shares' if symbol in portfolio.get_positions() else 'NO'}

Make your swing trade decision now and execute if appropriate.
""".strip()

        messages = [{"role": "user", "content": briefing}]
        decision = self._run(SYSTEM, messages, tools=TOOLS, max_tokens=1024)

        return {
            "symbol": symbol,
            "price": price,
            "swing_score": swing.get("score"),
            "swing_action": swing.get("action"),
            "stop_loss": swing.get("stop_loss"),
            "take_profit": swing.get("take_profit"),
            "decision": decision,
            "trigger": trigger,
        }

    def review_positions(self) -> list[dict]:
        """Check all open positions — decide to hold or sell."""
        positions = portfolio.get_positions()
        if not positions:
            print("[SwingTraderBot] No open positions to review.")
            return []
        results = []
        for symbol in positions:
            result = self.analyze(symbol, context={"trigger": "position_review"})
            results.append(result)
        return results
