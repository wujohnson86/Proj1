"""
Weather MCP Server
==================

This is a tiny "MCP server" — a program that exposes one tool, get_weather,
which an AI agent can call. MCP (Model Context Protocol) is just a standard
way for an AI agent to discover and call tools like this one.

It uses the free OpenWeatherMap API. You need an API key from:
https://openweathermap.org/api

Set it as an environment variable before running anything, e.g.:
    export OPENWEATHER_API_KEY="your-key-here"

To run this server by itself (mostly for testing), do:
    python3 weather_mcp_server.py

But normally you won't run it directly — the agent (weather_agent.py)
will start it automatically and talk to it over stdin/stdout.
"""

import os
import sys
import urllib.request
import urllib.parse
import json

from mcp.server.fastmcp import FastMCP

# Create the MCP server and give it a name. The agent will see this name.
mcp = FastMCP("weather-server")

OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY")


@mcp.tool()
def get_weather(city: str) -> str:
    """Get the current weather for a given city name.

    Args:
        city: Name of the city, e.g. "London" or "New York"
    """
    if not OPENWEATHER_API_KEY:
        return (
            "Error: OPENWEATHER_API_KEY environment variable is not set. "
            "Get a free key at https://openweathermap.org/api and run "
            "`export OPENWEATHER_API_KEY=your-key-here` before starting the agent."
        )

    params = urllib.parse.urlencode({
        "q": city,
        "appid": OPENWEATHER_API_KEY,
        "units": "metric",  # Celsius. Use "imperial" for Fahrenheit.
    })
    url = f"https://api.openweathermap.org/data/2.5/weather?{params}"

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return f"Could not find a city called '{city}'."
        return f"Weather API returned an error (HTTP {e.code})."
    except Exception as e:
        return f"Failed to fetch weather data: {e}"

    description = data["weather"][0]["description"]
    temp = data["main"]["temp"]
    feels_like = data["main"]["feels_like"]
    humidity = data["main"]["humidity"]
    name = data.get("name", city)

    return (
        f"Weather in {name}: {description}, "
        f"temperature {temp}°C (feels like {feels_like}°C), "
        f"humidity {humidity}%."
    )


if __name__ == "__main__":
    # This starts the server and makes it listen for tool calls over stdio
    # (standard input/output). That's how the agent will talk to it.
    mcp.run(transport="stdio")
