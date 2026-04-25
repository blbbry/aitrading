import json
from .base_bot import BaseBot
from tools.market_data import get_current_price, compute_technicals
import portfolio

TOOLS = [
    {
        "name": "get_portfolio_state",
        "description": "Get current cash, open positions, and total equity.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
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
    {
        "name": "get_atr",
        "description": "Get the Average True Range (ATR-14) for a stock — used for volatility-based position sizing.",
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
]

SYSTEM = """You are RiskManagerBot, the risk officer on an AI trading team.
Your job: evaluate whether a proposed trade is safe given the current portfolio, and recommend position sizing.

Rules you must enforce:
1. No single position should exceed 10% of total portfolio equity.
2. Total portfolio risk (sum of all open positions at cost) should not exceed 80% of equity.
3. Use ATR-based position sizing: risk no more than 1% of equity per trade (position size = (equity * 0.01) / ATR).
4. If cash < $500, recommend no new buys until something is sold.
5. Flag if the symbol is already heavily concentrated (>5% of equity).
6. Suggest a stop-loss level (entry - 2*ATR) and take-profit (entry + 3*ATR).

Output:
- APPROVED or REJECTED for the trade
- Recommended share count (if approved)
- Stop-loss price
- Take-profit price
- Max allowed position value
- Risk notes"""


class RiskManagerBot(BaseBot):
    name = "RiskManagerBot"
    role = "Risk officer: position sizing, stop-loss, portfolio limits"

    def _handle_tool(self, name: str, inputs: dict):
        if name == "get_portfolio_state":
            cash = portfolio.get_cash()
            positions = portfolio.get_positions()
            prices = {}
            for sym in positions:
                p = get_current_price(sym)
                if p:
                    prices[sym] = p
            summary = portfolio.get_portfolio_summary(prices)
            return {
                "cash": cash,
                "positions": positions,
                "total_equity": summary["total_equity"],
                "total_market_value": summary["total_market_value"],
            }
        if name == "get_current_price":
            price = get_current_price(inputs["symbol"])
            return {"price": price}
        if name == "get_atr":
            tech = compute_technicals(inputs["symbol"])
            return {"atr_14": tech.get("atr_14"), "current_price": tech.get("current_price")}
        raise ValueError(f"Unknown tool: {name}")

    def analyze(self, symbol: str, context: dict = None) -> dict:
        action = (context or {}).get("proposed_action", "BUY")
        price = (context or {}).get("current_price", "unknown")
        prompt = (
            f"The trading team is considering a {action} on {symbol} at around ${price}. "
            f"Please evaluate the risk, check our portfolio state, and give your sizing recommendation."
        )
        messages = [{"role": "user", "content": prompt}]
        report = self._run(SYSTEM, messages, tools=TOOLS, max_tokens=1024)
        return {"bot": self.name, "symbol": symbol, "report": report}
