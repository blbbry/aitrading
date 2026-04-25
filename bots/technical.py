from .base_bot import BaseBot
from tools.market_data import compute_technicals, get_current_price

TOOLS = [
    {
        "name": "compute_technicals",
        "description": (
            "Compute technical indicators for a stock: RSI, MACD, Bollinger Bands, "
            "moving averages (SMA20/50/200), ATR, and volume analysis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "days": {"type": "integer", "default": 120, "description": "Days of history to use"},
            },
            "required": ["symbol"],
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

SYSTEM = """You are TechnicalAnalystBot, a technical analyst on an AI trading team.
Your job: analyze price action and technical indicators to assess short-to-medium term momentum and trend.

Interpret and explain:
- Trend: is price above/below key moving averages (SMA20, SMA50, SMA200)?
- Momentum: RSI level (overbought >70, oversold <30, neutral 40-60)
- MACD: bullish/bearish crossover or divergence?
- Bollinger Bands: is price near upper (overbought) or lower (oversold) band?
- Volume: is recent volume confirming the move?
- ATR: what's the typical daily range (useful for stop placement)?
- Support/resistance levels if visible

Conclude with: BULLISH / BEARISH / NEUTRAL and a suggested entry zone and stop-loss level.
Be concise — the TraderBot reads your output."""


class TechnicalAnalystBot(BaseBot):
    name = "TechnicalAnalystBot"
    role = "Technical analyst: price action, RSI, MACD, moving averages"

    def _handle_tool(self, name: str, inputs: dict):
        if name == "compute_technicals":
            return compute_technicals(inputs["symbol"], inputs.get("days", 120))
        if name == "get_current_price":
            price = get_current_price(inputs["symbol"])
            return {"price": price}
        raise ValueError(f"Unknown tool: {name}")

    def analyze(self, symbol: str, context: dict = None) -> dict:
        messages = [{"role": "user", "content": f"Please run a full technical analysis on {symbol}."}]
        report = self._run(SYSTEM, messages, tools=TOOLS, max_tokens=1024)
        return {"bot": self.name, "symbol": symbol, "report": report}
