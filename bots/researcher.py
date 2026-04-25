import json
from .base_bot import BaseBot
from tools.market_data import get_fundamentals, get_news, get_current_price

TOOLS = [
    {
        "name": "get_fundamentals",
        "description": "Fetch fundamental financial data for a stock: P/E, EPS growth, margins, analyst targets, etc.",
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string", "description": "Stock ticker symbol e.g. AAPL"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "get_news",
        "description": "Fetch recent news headlines and summaries for a stock.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "limit": {"type": "integer", "default": 8},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_current_price",
        "description": "Get the current market price of a stock.",
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
]

SYSTEM = """You are ResearchBot, a fundamental equity analyst on an AI trading team.
Your job: gather news and fundamental data for a stock and produce a clear research brief.

Include:
- Business quality and competitive position
- Key financial metrics (growth, margins, valuation vs peers)
- Analyst sentiment and price target vs current price
- Recent news sentiment (positive / negative / neutral)
- Catalysts or risks on the horizon
- Overall fundamental verdict: BULLISH / BEARISH / NEUTRAL with brief reasoning

Be concise but complete. The TraderBot will use your output to make a final decision."""


class ResearchBot(BaseBot):
    name = "ResearchBot"
    role = "Fundamental analyst: news, financials, analyst ratings"

    def _handle_tool(self, name: str, inputs: dict):
        if name == "get_fundamentals":
            return get_fundamentals(inputs["symbol"])
        if name == "get_news":
            return get_news(inputs["symbol"], inputs.get("limit", 8))
        if name == "get_current_price":
            price = get_current_price(inputs["symbol"])
            return {"price": price}
        raise ValueError(f"Unknown tool: {name}")

    def analyze(self, symbol: str, context: dict = None) -> dict:
        messages = [{"role": "user", "content": f"Please research {symbol} and give me your fundamental analysis brief."}]
        report = self._run(SYSTEM, messages, tools=TOOLS, max_tokens=1024)
        return {"bot": self.name, "symbol": symbol, "report": report}
