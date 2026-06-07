"""
Crypto MCP Server
=================

A tiny MCP server exposing one tool, get_crypto_price, that looks up the
current price of a cryptocurrency using the free CoinGecko API.

No API key needed! CoinGecko's basic price endpoint is open to everyone.

To run this server by itself (mostly for testing):
    python3 crypto_mcp_server.py

Normally you won't run it directly — crypto_agent.py starts it for you.
"""

import sys
import urllib.request
import urllib.parse
import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("crypto-server")


@mcp.tool()
def get_crypto_price(coin: str, currency: str = "usd") -> str:
    """Get the current price of a cryptocurrency.

    Args:
        coin: Coin name in CoinGecko's format, e.g. "bitcoin", "ethereum", "dogecoin"
        currency: Currency to show the price in, e.g. "usd", "eur", "gbp" (default "usd")
    """
    params = urllib.parse.urlencode({
        "ids": coin.lower(),
        "vs_currencies": currency.lower(),
        "include_24hr_change": "true",
    })
    url = f"https://api.coingecko.com/api/v3/simple/price?{params}"

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        return f"Failed to fetch crypto data: {e}"

    coin_data = data.get(coin.lower())
    if not coin_data:
        return (
            f"Could not find a coin called '{coin}'. "
            f"Try the CoinGecko ID format, e.g. 'bitcoin', 'ethereum', 'dogecoin'."
        )

    price = coin_data.get(currency.lower())
    change = coin_data.get(f"{currency.lower()}_24h_change")

    if price is None:
        return f"Could not find a price for '{coin}' in '{currency}'."

    result = f"{coin.capitalize()}: {price} {currency.upper()}"
    if change is not None:
        result += f" ({change:+.2f}% over the last 24h)"
    return result


if __name__ == "__main__":
    mcp.run(transport="stdio")
