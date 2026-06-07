"""
News MCP Server
===============

A tiny MCP server exposing one tool, get_top_headlines, that fetches
current news headlines using the free NewsAPI.org API.

Get a free key at https://newsapi.org/register and set it:
    export NEWSAPI_KEY="your-key-here"

To run this server by itself (mostly for testing):
    python3 news_mcp_server.py

Normally you won't run it directly — news_agent.py starts it for you.
"""

import os
import sys
import urllib.request
import urllib.parse
import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("news-server")

NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY")


@mcp.tool()
def get_top_headlines(topic: str = "", country: str = "us") -> str:
    """Get current top news headlines, optionally filtered by topic/keyword.

    Args:
        topic: Optional keyword to search for, e.g. "technology" or "climate".
               Leave empty for general top headlines.
        country: Two-letter country code, e.g. "us", "gb", "jp" (default "us")
    """
    if not NEWSAPI_KEY:
        return (
            "Error: NEWSAPI_KEY environment variable is not set. "
            "Get a free key at https://newsapi.org/register and run "
            "`export NEWSAPI_KEY=your-key-here` before starting the agent."
        )

    params = {"apiKey": NEWSAPI_KEY, "pageSize": 5}
    if topic:
        params["q"] = topic
    else:
        params["country"] = country

    url = f"https://newsapi.org/v2/top-headlines?{urllib.parse.urlencode(params)}"

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return f"News API returned an error (HTTP {e.code})."
    except Exception as e:
        return f"Failed to fetch news data: {e}"

    articles = data.get("articles", [])
    if not articles:
        return f"No headlines found for topic '{topic or 'general'}'."

    lines = []
    for i, article in enumerate(articles, start=1):
        title = article.get("title", "Untitled")
        source = article.get("source", {}).get("name", "Unknown source")
        lines.append(f"{i}. {title} ({source})")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
