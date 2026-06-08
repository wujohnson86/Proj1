# MCP Agent Playground (Beginner Project)

The main thing to run is **`agent.py`** — a single agent that connects to
all three MCP servers below at once and figures out on its own which
tool(s) to use based on what you ask (it can even combine them, e.g.
"weather in Tokyo and the price of Bitcoin").

Small Python pairs of (MCP server + Claude agent), each demonstrating the
same pattern with a different free API — these also still work standalone
if you want to see one in isolation:

- `weather_mcp_server.py` / `weather_agent.py` — weather lookups via
  OpenWeatherMap (needs a free API key).
- `crypto_mcp_server.py` / `crypto_agent.py` — cryptocurrency prices via
  CoinGecko (no API key needed at all).
- `news_mcp_server.py` / `news_agent.py` — news headlines via NewsAPI.org
  (needs a free API key).

Each "agent" file is the one you run — it automatically launches its
matching "server" file(s) as a subprocess and talks to them over MCP.

## 1. Get your API keys ready

- Anthropic API key: https://console.anthropic.com/
- OpenWeatherMap API key (free tier is fine, only needed for the weather agent): https://openweathermap.org/api
- NewsAPI key (free tier is fine, only needed for the news agent): https://newsapi.org/register
- (No key needed for the crypto agent — CoinGecko's basic API is open.)

## 2. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 3. Set your keys as environment variables

Never put keys directly in the code. Export them in your shell instead:

```bash
export ANTHROPIC_API_KEY="your-anthropic-key-here"
export OPENWEATHER_API_KEY="your-openweather-key-here"
export NEWSAPI_KEY="your-newsapi-key-here"
```

(You only need to set the keys for the agents you plan to run — the crypto
agent doesn't need any key besides `ANTHROPIC_API_KEY`.)

(Add these two lines to your `~/.bashrc` if you don't want to re-type them
every time you open a terminal.)

## 4. Run an agent

The easiest way to start is the all-in-one agent — it has access to every
tool and figures out which one(s) to use:

```bash
python3 agent.py
```

Try asking it things like:
- "What's the weather in Tokyo?"
- "What's the price of Bitcoin?"
- "What's the top tech news today?"
- "Give me the weather in Paris AND the price of Ethereum" (it can chain
  multiple tool calls to answer one question!)

You can also run any of the single-purpose agents on their own:

```bash
python3 weather_agent.py
# or
python3 crypto_agent.py
# or
python3 news_agent.py
```

Each one automatically starts its matching MCP server behind the scenes —
you don't need to run the servers separately.

Try asking the weather agent things like:
- "What's the weather in Tokyo?"
- "Is it warmer in Paris or Berlin right now?"

Try asking the crypto agent things like:
- "What's the price of Bitcoin?"
- "Compare Ethereum and Dogecoin in EUR"

Try asking the news agent things like:
- "What's the top news today?"
- "Summarize the latest technology headlines"

Type `quit` to exit any of them.

## How it fits together

```
You  -->  weather_agent.py  -->  Claude (Anthropic API)
                |                       |
                |  (decides to call a tool)
                v
          MCP connection
                |
                v
       weather_mcp_server.py  -->  OpenWeatherMap API
```
