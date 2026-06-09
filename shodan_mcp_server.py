"""
Shodan MCP Server
=================

A tiny MCP server exposing two tools:

1. lookup_ip(ip) — uses the FREE InternetDB API (no credits consumed,
   no API key needed) to get a quick exposure summary for any IP address:
   open ports, hostnames, tags (e.g. "honeypot"), and known CVEs.

2. search_shodan(query) — uses the real Shodan search API with your key
   to find internet-connected devices matching a filter, e.g.:
   "apache country:US port:8080" or "port:22 org:Amazon"
   Each call costs 1 query credit (free tier = 100/month).

Setup:
    export SHODAN_API_KEY="your-shodan-key-here"

To run this server by itself (mostly for testing):
    python3 shodan_mcp_server.py

Normally you won't run it directly — agent.py starts it for you.
"""

import os
import sys
import urllib.request
import urllib.parse
import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("shodan-server")

SHODAN_API_KEY = os.environ.get("SHODAN_API_KEY")


def _fetch(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _resolve_host(host: str) -> str:
    """If host looks like a hostname (not a bare IP), resolve it to an IP
    using Shodan's DNS resolve endpoint so InternetDB and host lookup
    both get a plain IP address to work with."""
    import re
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", host):
        return host  # already an IP

    if not SHODAN_API_KEY:
        # Fall back to stdlib DNS if no key available
        import socket
        return socket.gethostbyname(host)

    params = urllib.parse.urlencode({"hostnames": host})
    url = f"https://api.shodan.io/dns/resolve?{params}&key={SHODAN_API_KEY}"
    data = _fetch(url)
    return data.get(host, host)


@mcp.tool()
def lookup_ip(host: str) -> str:
    """Look up what Shodan knows about an IP address or hostname —
    open ports, hostnames, tags (e.g. 'honeypot', 'eol-product'),
    and known CVEs. Uses the free InternetDB API — no credits consumed.

    Args:
        host: IP address or hostname, e.g. "8.8.8.8" or "ec2.snoopy.org"
    """
    try:
        ip = _resolve_host(host)
    except Exception as e:
        return f"Could not resolve '{host}' to an IP address: {e}"

    try:
        data = _fetch(f"https://internetdb.shodan.io/{ip}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return f"Shodan has no data for {ip} ('{host}') — it may not have been scanned yet."
        return f"InternetDB returned HTTP {e.code} for {ip}."
    except Exception as e:
        return f"Failed to fetch Shodan data for {ip}: {e}"

    lines = [f"Shodan InternetDB report for {ip} ('{host}'):"]

    ports = data.get("ports", [])
    lines.append(f"  Open ports   : {', '.join(str(p) for p in sorted(ports)) or 'none found'}")

    hostnames = data.get("hostnames", [])
    lines.append(f"  Hostnames    : {', '.join(hostnames) or 'none'}")

    tags = data.get("tags", [])
    lines.append(f"  Tags         : {', '.join(tags) or 'none'}")

    cpes = data.get("cpes", [])
    if cpes:
        lines.append(f"  Software     : {', '.join(cpes)}")

    vulns = data.get("vulns", [])
    if vulns:
        lines.append(f"  Known CVEs   : {', '.join(vulns)}")
        lines.append("  (!) These CVEs were found in banners — verify before acting on them.")
    else:
        lines.append("  Known CVEs   : none reported")

    return "\n".join(lines)


@mcp.tool()
def search_shodan(query: str, max_results: int = 5) -> str:
    """Search Shodan for internet-connected devices matching a filter.
    Costs 1 query credit per call (free tier = 100 credits/month).

    Example queries:
        "apache country:US port:8080"
        "port:22 org:Amazon"
        "product:nginx version:1.14"
        "webcam country:JP"
        "default password"

    Args:
        query: Shodan search query string
        max_results: How many results to show, max 10 (default 5)
    """
    if not SHODAN_API_KEY:
        return (
            "Error: SHODAN_API_KEY environment variable is not set. "
            "Get a free key at https://account.shodan.io and run "
            "`export SHODAN_API_KEY=your-key-here` before starting the agent."
        )

    max_results = max(1, min(max_results, 10))

    params = urllib.parse.urlencode({
        "key": SHODAN_API_KEY,
        "query": query,
        "page": 1,
    })
    url = f"https://api.shodan.io/shodan/host/search?{params}"

    try:
        data = _fetch(url)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return f"Shodan search failed (HTTP {e.code}): {body}"
    except Exception as e:
        return f"Failed to query Shodan: {e}"

    total = data.get("total", 0)
    matches = data.get("matches", [])[:max_results]

    if not matches:
        return f"No results found for query: {query!r}"

    lines = [f"Shodan search: {query!r} — {total} total matches, showing {len(matches)}:\n"]
    for m in matches:
        ip = m.get("ip_str", "?")
        port = m.get("port", "?")
        org = m.get("org", "unknown org")
        country = m.get("location", {}).get("country_name", "?")
        product = m.get("product", "")
        version = m.get("version", "")
        banner = (m.get("data", "") or "").strip()[:120]

        summary = f"  {ip}:{port} — {org}, {country}"
        if product:
            summary += f" — {product}"
            if version:
                summary += f" {version}"
        lines.append(summary)
        if banner:
            lines.append(f"    Banner: {banner!r}")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
