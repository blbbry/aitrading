import re
import json
from .base_bot import BaseBot
from .researcher import ResearchBot
from .technical import TechnicalAnalystBot
from .risk_manager import RiskManagerBot
from tools.market_data import get_current_price
import portfolio

TOOLS = [
    {
        "name": "execute_buy",
        "description": "Execute a paper buy order.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "shares": {"type": "number", "description": "Number of shares to buy"},
                "reason": {"type": "string", "description": "One-sentence rationale"},
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
        "description": "Get current price of a stock.",
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
]

SYSTEM = """You are TraderBot, the head trader and decision-maker on an AI trading team.
You receive research briefs from three specialists:
- ResearchBot (fundamentals, news, analyst ratings)
- TechnicalAnalystBot (price action, RSI, MACD, moving averages)
- RiskManagerBot (position sizing, stop-loss, portfolio limits)

Your job:
1. Synthesize their reports and decide: BUY, SELL, or HOLD.
2. If BUY or SELL: use the exact share count RiskManagerBot recommended (never exceed it).
3. Execute the trade using the provided tools.
4. Write a final trade summary explaining the decision.

Decision rules:
- Only BUY if at least 2 of 3 bots are BULLISH and RiskManagerBot says APPROVED.
- Only SELL an existing position if at least 2 of 3 bots are BEARISH.
- HOLD otherwise.
- Never override a RiskManagerBot REJECTED decision.

After deciding, briefly state: action taken, shares, price, and the key reasons."""


class TraderBot(BaseBot):
    name = "TraderBot"
    role = "Head trader: synthesizes team reports and executes paper trades"

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
                return {"ok": False, "error": "Could not fetch price"}
            return portfolio.buy(symbol, inputs["shares"], price, inputs.get("reason", ""))
        if name == "execute_sell":
            if not price:
                return {"ok": False, "error": "Could not fetch price"}
            return portfolio.sell(symbol, inputs["shares"], price, inputs.get("reason", ""))
        raise ValueError(f"Unknown tool: {name}")

    def _extract_shares_from_risk_report(self, report: str) -> float | None:
        match = re.search(r"(\d+(?:\.\d+)?)\s+share", report, re.IGNORECASE)
        if match:
            return float(match.group(1))
        return None

    def analyze(self, symbol: str, context: dict = None) -> dict:
        print(f"\n[TraderBot] Assembling team analysis for {symbol}...")

        price = get_current_price(symbol)
        if price:
            self._current_prices[symbol] = price
        print(f"  Current price: ${price:.2f}" if price else "  Could not fetch price")

        print("  [ResearchBot] Running fundamental analysis...")
        research = self.researcher.analyze(symbol)

        print("  [TechnicalAnalystBot] Running technical analysis...")
        technical = self.technical.analyze(symbol)

        print("  [RiskManagerBot] Running risk assessment...")
        risk = self.risk_manager.analyze(symbol, context={"proposed_action": "BUY", "current_price": price})

        team_briefing = f"""
=== TEAM BRIEFING FOR {symbol} ===

--- ResearchBot (Fundamentals & News) ---
{research['report']}

--- TechnicalAnalystBot (Price Action & Indicators) ---
{technical['report']}

--- RiskManagerBot (Risk & Position Sizing) ---
{risk['report']}

=== END BRIEFING ===

Current price: ${price:.2f if price else 'unknown'}
Now make your decision and execute if appropriate.
""".strip()

        messages = [{"role": "user", "content": team_briefing}]
        decision = self._run(SYSTEM, messages, tools=TOOLS, max_tokens=1024)

        return {
            "symbol": symbol,
            "price": price,
            "research": research["report"],
            "technical": technical["report"],
            "risk": risk["report"],
            "decision": decision,
        }

    def review_portfolio(self) -> str:
        """Review all open positions and decide whether to sell any."""
        positions = portfolio.get_positions()
        if not positions:
            return "No open positions to review."

        results = []
        for symbol in positions:
            print(f"\n[TraderBot] Reviewing position: {symbol}")
            result = self.analyze(symbol, context={"mode": "review"})
            results.append(f"[{symbol}] {result['decision']}")

        return "\n\n".join(results)
