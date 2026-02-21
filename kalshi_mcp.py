"""
Kalshi MCP Server - Prediction market integration for Claude Desktop
Provides tools to search markets, analyze orderbooks, manage portfolio, and execute trades.
"""

import os
import time
import base64
import logging
from typing import Optional, Dict, List
from datetime import datetime

import httpx
from mcp.server.fastmcp import FastMCP
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kalshi-mcp")

# Initialize FastMCP server
mcp = FastMCP("kalshi")

# API Configuration
API_BASE = os.getenv("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")
API_KEY = os.getenv("KALSHI_API_KEY")
PRIVATE_KEY_PATH = os.path.expanduser(os.getenv("KALSHI_PRIVATE_KEY_PATH", "~/.trading_keys/kalshi_private_key.pem"))

# Global httpx client with persistent connection
http_client: Optional[httpx.Client] = None
private_key = None
last_request_time = 0.0
MIN_REQUEST_INTERVAL = 0.15  # 150ms between requests


def _load_private_key():
    """Load RSA private key from file."""
    if not os.path.exists(PRIVATE_KEY_PATH):
        raise FileNotFoundError(f"Kalshi private key not found at {PRIVATE_KEY_PATH}")

    with open(PRIVATE_KEY_PATH, "rb") as f:
        key = serialization.load_pem_private_key(
            f.read(),
            password=None,
            backend=default_backend()
        )
    logger.info("Loaded Kalshi private key")
    return key


def _sign_request(method: str, path: str, timestamp: int) -> str:
    """Create RSA-PSS signature for Kalshi API request."""
    message = f"{timestamp}{method}{path}"
    signature = private_key.sign(
        message.encode('utf-8'),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH
        ),
        hashes.SHA256()
    )
    return base64.b64encode(signature).decode('utf-8')


def _request(method: str, endpoint: str, retry: int = 0, **kwargs) -> Dict:
    """Make authenticated request to Kalshi API with rate limiting."""
    global last_request_time, http_client

    # Rate limiting
    elapsed = time.time() - last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    last_request_time = time.time()

    # Authentication
    timestamp = int(time.time() * 1000)
    path = f"/trade-api/v2{endpoint}"
    signature = _sign_request(method.upper(), path, timestamp)

    headers = {
        "KALSHI-ACCESS-KEY": API_KEY,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": str(timestamp),
        "Content-Type": "application/json",
    }

    url = f"{API_BASE}{endpoint}"
    kwargs.setdefault('timeout', 30)

    try:
        response = http_client.request(method, url, headers=headers, **kwargs)

        # Auto-retry on 429 (rate limit)
        if response.status_code == 429 and retry < 3:
            backoff = (2 ** retry) * 0.5
            logger.warning(f"Rate limited, backing off {backoff:.1f}s (retry {retry + 1}/3)")
            time.sleep(backoff)
            return _request(method, endpoint, retry=retry + 1, **kwargs)

        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"Kalshi API error {e.response.status_code}: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"Request failed: {e}")
        raise


# ================== TOOLS ==================

@mcp.tool()
def search_markets(query: str, limit: int = 20) -> str:
    """
    Search Kalshi prediction markets by keyword.

    Args:
        query: Search keyword (e.g., "bitcoin", "fed rate", "weather chicago")
        limit: Maximum number of results (default 20, max 100)

    Returns:
        JSON-formatted list of matching markets with ticker, title, prices, volume
    """
    limit = min(limit, 100)
    params = {'status': 'open', 'limit': 200}

    try:
        data = _request("GET", "/markets", params=params)
        markets = data.get('markets', [])

        # Filter by keyword
        query_lower = query.lower()
        matches = [
            m for m in markets
            if query_lower in m.get('title', '').lower() or
               query_lower in m.get('subtitle', '').lower() or
               query_lower in m.get('ticker', '').lower()
        ][:limit]

        results = []
        for m in matches:
            results.append({
                'ticker': m.get('ticker'),
                'title': m.get('title'),
                'subtitle': m.get('subtitle'),
                'yes_price': m.get('yes_price', 0) / 100 if m.get('yes_price') else None,
                'no_price': m.get('no_price', 0) / 100 if m.get('no_price') else None,
                'volume_24h': m.get('volume_24h', 0),
                'close_time': m.get('close_time'),
            })

        import json
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error searching markets: {str(e)}"


@mcp.tool()
def get_market_details(ticker: str) -> str:
    """
    Get full details for a specific market including orderbook.

    Args:
        ticker: Market ticker (e.g., "KXHIGHNYC-26FEB20-B34.5")

    Returns:
        JSON with market metadata, current prices, orderbook bids/asks
    """
    try:
        # Get market info
        market_data = _request("GET", f"/markets/{ticker}")
        market = market_data.get('market', market_data)

        # Get orderbook
        orderbook_data = _request("GET", f"/markets/{ticker}/orderbook")
        orderbook = orderbook_data.get('orderbook', orderbook_data)

        result = {
            'ticker': market.get('ticker'),
            'title': market.get('title'),
            'subtitle': market.get('subtitle'),
            'yes_price': market.get('yes_price', 0) / 100 if market.get('yes_price') else None,
            'no_price': market.get('no_price', 0) / 100 if market.get('no_price') else None,
            'volume_24h': market.get('volume_24h', 0),
            'open_interest': market.get('open_interest', 0),
            'close_time': market.get('close_time'),
            'orderbook': {
                'yes_bids': orderbook.get('yes', [])[:5],
                'no_bids': orderbook.get('no', [])[:5],
            }
        }

        import json
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error fetching market details: {str(e)}"


@mcp.tool()
def get_portfolio() -> str:
    """
    Get your Kalshi portfolio - account balance and all open positions.

    Returns:
        JSON with balance (USD) and list of open positions with P&L
    """
    try:
        # Get balance
        balance_data = _request("GET", "/portfolio/balance")
        balance = balance_data.get('balance', 0) / 100  # Convert cents to dollars

        # Get positions
        positions_data = _request("GET", "/portfolio/positions")
        positions = positions_data.get('market_positions', [])

        result = {
            'balance_usd': f"${balance:.2f}",
            'positions': []
        }

        for pos in positions:
            result['positions'].append({
                'ticker': pos.get('ticker'),
                'side': pos.get('position', 'unknown'),
                'quantity': pos.get('total_traded', 0),
                'avg_price': pos.get('market_exposure', 0) / max(pos.get('total_traded', 1), 1) / 100,
                'current_value': pos.get('market_exposure', 0) / 100,
            })

        import json
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error fetching portfolio: {str(e)}"


@mcp.tool()
def get_trending_markets(limit: int = 10) -> str:
    """
    Get top trending markets by 24-hour volume.

    Args:
        limit: Number of markets to return (default 10, max 50)

    Returns:
        JSON list of markets sorted by volume with liquidity score
    """
    limit = min(limit, 50)

    try:
        params = {'status': 'open', 'limit': 200}
        data = _request("GET", "/markets", params=params)
        markets = data.get('markets', [])

        # Calculate liquidity score and sort
        scored = []
        for m in markets:
            volume = m.get('volume_24h', 0)
            oi = m.get('open_interest', 0)
            liquidity = (volume * 0.6) + (oi * 0.4)

            scored.append({
                'ticker': m.get('ticker'),
                'title': m.get('title'),
                'subtitle': m.get('subtitle'),
                'yes_price': m.get('yes_price', 0) / 100 if m.get('yes_price') else None,
                'volume_24h': volume,
                'liquidity_score': liquidity,
            })

        scored.sort(key=lambda x: x['liquidity_score'], reverse=True)

        import json
        return json.dumps(scored[:limit], indent=2)
    except Exception as e:
        return f"Error fetching trending markets: {str(e)}"


@mcp.tool()
def place_order(ticker: str, side: str, quantity: int, price_cents: int) -> str:
    """
    Place a limit order on Kalshi.

    Args:
        ticker: Market ticker (e.g., "KXHIGHNYC-26FEB20-B34.5")
        side: "yes" or "no"
        quantity: Number of contracts (must be positive integer)
        price_cents: Price in cents, 1-99 (e.g., 25 = $0.25)

    Returns:
        Order confirmation with order ID and status
    """
    # Validation
    if side not in ("yes", "no"):
        return f"Error: side must be 'yes' or 'no', got '{side}'"
    if not isinstance(quantity, int) or quantity < 1:
        return f"Error: quantity must be positive integer, got {quantity}"
    if not isinstance(price_cents, int) or price_cents < 1 or price_cents > 99:
        return f"Error: price_cents must be 1-99, got {price_cents}"

    try:
        payload = {
            "ticker": ticker,
            "action": "buy",
            "side": side,
            "count": quantity,
            "type": "limit",
        }

        if side == "yes":
            payload["yes_price"] = price_cents
        else:
            payload["no_price"] = price_cents

        data = _request("POST", "/portfolio/orders", json=payload)
        order = data.get('order', data)

        result = {
            'order_id': order.get('order_id'),
            'status': order.get('status'),
            'ticker': ticker,
            'side': side,
            'quantity': quantity,
            'price': f"${price_cents / 100:.2f}",
            'created_at': order.get('created_time'),
        }

        import json
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error placing order: {str(e)}"


@mcp.tool()
def get_series_markets(series_ticker: str) -> str:
    """
    Get all markets in a series (e.g., all Federal Reserve event markets).

    Args:
        series_ticker: Series ticker (e.g., "KXFED", "KXHIGH", "KXCPI")

    Returns:
        JSON list of markets in the series
    """
    try:
        params = {'series_ticker': series_ticker, 'status': 'open', 'limit': 200}
        data = _request("GET", "/markets", params=params)
        markets = data.get('markets', [])

        results = []
        for m in markets:
            results.append({
                'ticker': m.get('ticker'),
                'title': m.get('title'),
                'subtitle': m.get('subtitle'),
                'yes_price': m.get('yes_price', 0) / 100 if m.get('yes_price') else None,
                'close_time': m.get('close_time'),
            })

        import json
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error fetching series markets: {str(e)}"


# ================== RESOURCES ==================

@mcp.resource("kalshi://balance")
def get_balance_resource() -> str:
    """Get current Kalshi account balance."""
    try:
        data = _request("GET", "/portfolio/balance")
        balance = data.get('balance', 0) / 100
        return f"Kalshi Account Balance: ${balance:.2f}"
    except Exception as e:
        return f"Error fetching balance: {str(e)}"


@mcp.resource("kalshi://positions")
def get_positions_resource() -> str:
    """Get list of open Kalshi positions."""
    try:
        data = _request("GET", "/portfolio/positions")
        positions = data.get('market_positions', [])

        if not positions:
            return "No open positions"

        lines = ["Open Kalshi Positions:", ""]
        for pos in positions:
            ticker = pos.get('ticker')
            side = pos.get('position', 'unknown')
            qty = pos.get('total_traded', 0)
            lines.append(f"  {ticker}: {qty} {side}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching positions: {str(e)}"


# ================== STARTUP ==================

def initialize():
    """Initialize API client and credentials."""
    global private_key, http_client

    if not API_KEY:
        raise ValueError("KALSHI_API_KEY environment variable not set")

    private_key = _load_private_key()
    http_client = httpx.Client()
    logger.info("Kalshi MCP server initialized")


if __name__ == "__main__":
    initialize()
    mcp.run()
