# Weather Agent + MCP Server (Beginner Project)

Two small Python files:

- `weather_mcp_server.py` — an MCP server exposing one tool, `get_weather`,
  backed by the OpenWeatherMap API.
- `weather_agent.py` — a command-line agent powered by Claude that uses
  that tool to answer weather questions.

## 1. Get your API keys ready

- Anthropic API key: https://console.anthropic.com/
- OpenWeatherMap API key (free tier is fine): https://openweathermap.org/api

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
```

(Add these two lines to your `~/.bashrc` if you don't want to re-type them
every time you open a terminal.)

## 4. Run the agent

```bash
python3 weather_agent.py
```

This automatically starts `weather_mcp_server.py` for you behind the scenes
and connects to it over MCP — you don't need to run the server separately.

Try asking things like:
- "What's the weather in Tokyo?"
- "Is it warmer in Paris or Berlin right now?"

Type `quit` to exit.

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
