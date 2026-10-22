"""
Prediction Market MCP Server - Unified multi-platform prediction market integration for Claude Desktop.

Aggregates Kalshi, Polymarket, and Metaculus into a single MCP server with cross-platform
search, arbitrage scanning, and per-platform tools.

Platforms:
    - Kalshi: Regulated US prediction market (authenticated via RSA-PSS)
    - Polymarket: Decentralized prediction market on Polygon (public read-only)
    - Metaculus: Community forecasting platform (public read-only)
"""

import os
import json
import time
import asyncio
import logging
from typing import Optional, Dict, List, Any
from datetime import datetime

import httpx
from mcp.server.fastmcp import FastMCP

# Reuse Kalshi auth infrastructure from existing module
try:
    from kalshi_mcp import (
        _load_private_key,
        _sign_request,
        API_BASE as KALSHI_API_BASE,
        API_KEY as KALSHI_API_KEY,
        MIN_REQUEST_INTERVAL as KALSHI_MIN_REQUEST_INTERVAL,
    )
    KALSHI_AVAILABLE = True
except ImportError:
    KALSHI_AVAILABLE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("prediction-markets")

# ================== MCP SERVER ==================

mcp = FastMCP("prediction-markets")

# ================== PLATFORM CONFIGURATION ==================

# Kalshi (authenticated - reuses existing env vars)
KALSHI_API_BASE_URL = os.getenv("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")
KALSHI_API_KEY_VAL = os.getenv("KALSHI_API_KEY")
KALSHI_PRIVATE_KEY_PATH = os.path.expanduser(
    os.getenv("KALSHI_PRIVATE_KEY_PATH", "~/.trading_keys/kalshi_private_key.pem")
)

# Polymarket (public, no auth needed)
POLYMARKET_CLOB_BASE = "https://clob.polymarket.com"
POLYMARKET_GAMMA_BASE = "https://gamma-api.polymarket.com"

# Metaculus (public, no auth needed)
METACULUS_API_BASE = "https://www.metaculus.com/api2"

# ================== RATE LIMITING ==================

class RateLimiter:
    """Per-platform rate limiter to respect API limits."""

    def __init__(self, min_interval: float = 0.15):
        self.min_interval = min_interval
        self._last_request_time = 0.0

    def wait(self):
        """Block until the minimum interval has elapsed since the last request."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_time = time.time()


_rate_limiters = {
    "kalshi": RateLimiter(min_interval=0.15),
    "polymarket": RateLimiter(min_interval=0.10),
    "metaculus": RateLimiter(min_interval=0.20),
}

# ================== HTTP CLIENTS ==================

# Persistent clients per platform for connection reuse
_clients: Dict[str, Optional[httpx.Client]] = {
    "kalshi": None,
    "polymarket": None,
    "metaculus": None,
}

# Kalshi auth state
_kalshi_private_key = None


def _get_client(platform: str) -> httpx.Client:
    """Get or create a persistent httpx client for the given platform."""
    if _clients[platform] is None:
        _clients[platform] = httpx.Client(timeout=30)
    return _clients[platform]


# ================== KALSHI REQUEST HELPERS ==================

def _kalshi_init():
    """Initialize Kalshi authentication. Raises if credentials are missing."""
    global _kalshi_private_key
    if _kalshi_private_key is not None:
        return

    if not KALSHI_API_KEY_VAL:
        raise ValueError(
            "KALSHI_API_KEY environment variable not set. "
            "Kalshi tools require authentication."
        )

    if KALSHI_AVAILABLE:
        _kalshi_private_key = _load_private_key()
    else:
        # Inline key loading if kalshi_mcp module is not importable
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend

        if not os.path.exists(KALSHI_PRIVATE_KEY_PATH):
            raise FileNotFoundError(
                f"Kalshi private key not found at {KALSHI_PRIVATE_KEY_PATH}"
            )
        with open(KALSHI_PRIVATE_KEY_PATH, "rb") as f:
            _kalshi_private_key = serialization.load_pem_private_key(
                f.read(), password=None, backend=default_backend()
            )
    logger.info("Kalshi authentication initialized")


def _kalshi_sign(method: str, path: str, timestamp: int) -> str:
    """Create RSA-PSS signature for Kalshi API request."""
    import base64
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    message = f"{timestamp}{method}{path}"
    signature = _kalshi_private_key.sign(
        message.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


def _kalshi_request(method: str, endpoint: str, retry: int = 0, **kwargs) -> Dict:
    """Make authenticated request to Kalshi API with rate limiting and retry."""
    _kalshi_init()
    _rate_limiters["kalshi"].wait()

    timestamp = int(time.time() * 1000)
    path = f"/trade-api/v2{endpoint}"
    signature = _kalshi_sign(method.upper(), path, timestamp)

    headers = {
        "KALSHI-ACCESS-KEY": KALSHI_API_KEY_VAL,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": str(timestamp),
        "Content-Type": "application/json",
    }

    url = f"{KALSHI_API_BASE_URL}{endpoint}"
    client = _get_client("kalshi")

    try:
        response = client.request(method, url, headers=headers, **kwargs)
        if response.status_code == 429 and retry < 3:
            backoff = (2 ** retry) * 0.5
            logger.warning(f"Kalshi rate limited, backing off {backoff:.1f}s (retry {retry + 1}/3)")
            time.sleep(backoff)
            return _kalshi_request(method, endpoint, retry=retry + 1, **kwargs)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"Kalshi API error {e.response.status_code}: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"Kalshi request failed: {e}")
        raise


# ================== POLYMARKET REQUEST HELPERS ==================

def _polymarket_request(
    endpoint: str, base: str = POLYMARKET_CLOB_BASE, retry: int = 0, **kwargs
) -> Any:
    """Make request to Polymarket API with rate limiting and retry."""
    _rate_limiters["polymarket"].wait()

    url = f"{base}{endpoint}"
    client = _get_client("polymarket")

    try:
        response = client.get(url, **kwargs)
        if response.status_code == 429 and retry < 3:
            backoff = (2 ** retry) * 0.5
            logger.warning(f"Polymarket rate limited, backing off {backoff:.1f}s (retry {retry + 1}/3)")
            time.sleep(backoff)
            return _polymarket_request(endpoint, base=base, retry=retry + 1, **kwargs)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"Polymarket API error {e.response.status_code}: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"Polymarket request failed: {e}")
        raise


# ================== METACULUS REQUEST HELPERS ==================

def _metaculus_request(endpoint: str, retry: int = 0, **kwargs) -> Any:
    """Make request to Metaculus API with rate limiting and retry."""
    _rate_limiters["metaculus"].wait()

    url = f"{METACULUS_API_BASE}{endpoint}"
    client = _get_client("metaculus")
    headers = {"Content-Type": "application/json"}

    try:
        response = client.get(url, headers=headers, **kwargs)
        if response.status_code == 429 and retry < 3:
            backoff = (2 ** retry) * 0.5
            logger.warning(f"Metaculus rate limited, backing off {backoff:.1f}s (retry {retry + 1}/3)")
            time.sleep(backoff)
            return _metaculus_request(endpoint, retry=retry + 1, **kwargs)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"Metaculus API error {e.response.status_code}: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"Metaculus request failed: {e}")
        raise


# ================== POLYMARKET TOOLS ==================

@mcp.tool()
def polymarket_search(query: str, limit: int = 20) -> str:
    """
    Search Polymarket prediction markets by keyword.

    Uses the Gamma API to find markets matching a search query. Returns market
    titles, prices, volume, and condition IDs for further lookup.

    Args:
        query: Search keyword (e.g., "bitcoin", "election", "AI")
        limit: Maximum number of results (default 20, max 100)

    Returns:
        JSON list of matching Polymarket markets with title, price, volume, and IDs
    """
    limit = min(limit, 100)

    try:
        params = {"query": query, "limit": limit, "active": True}
        data = _polymarket_request(
            "/markets", base=POLYMARKET_GAMMA_BASE, params=params
        )

        # Gamma API may return a list directly or a dict with results
        markets = data if isinstance(data, list) else data.get("markets", data.get("results", []))

        results = []
        for m in markets[:limit]:
            # Parse outcome prices if available
            outcome_prices = None
            if m.get("outcomePrices"):
                try:
                    outcome_prices = json.loads(m["outcomePrices"]) if isinstance(m["outcomePrices"], str) else m["outcomePrices"]
                except (json.JSONDecodeError, TypeError):
                    outcome_prices = None

            yes_price = None
            no_price = None
            if outcome_prices and len(outcome_prices) >= 2:
                try:
                    yes_price = round(float(outcome_prices[0]), 4)
                    no_price = round(float(outcome_prices[1]), 4)
                except (ValueError, TypeError):
                    pass

            results.append({
                "platform": "polymarket",
                "condition_id": m.get("conditionId") or m.get("condition_id"),
                "question_id": m.get("questionId") or m.get("question_id"),
                "title": m.get("question") or m.get("title"),
                "description": (m.get("description") or "")[:200],
                "yes_price": yes_price,
                "no_price": no_price,
                "volume": m.get("volume") or m.get("volumeNum") or 0,
                "volume_24h": m.get("volume24hr") or 0,
                "liquidity": m.get("liquidity") or 0,
                "end_date": m.get("endDate") or m.get("end_date_iso"),
                "active": m.get("active", True),
            })

        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error searching Polymarket: {str(e)}"


@mcp.tool()
def polymarket_market_details(condition_id: str) -> str:
    """
    Get full details for a specific Polymarket market including orderbook.

    Retrieves market metadata from the Gamma API and orderbook depth from
    the CLOB API.

    Args:
        condition_id: The market's condition ID (from polymarket_search results)

    Returns:
        JSON with market metadata, current prices, and top-of-book orders
    """
    try:
        # Get market info from Gamma API
        market_data = _polymarket_request(
            f"/markets/{condition_id}", base=POLYMARKET_GAMMA_BASE
        )

        # Normalize: Gamma may return the market directly or nested
        market = market_data if isinstance(market_data, dict) else {}

        # Parse tokens to get token_id for orderbook
        tokens = market.get("tokens", [])
        if isinstance(tokens, str):
            try:
                tokens = json.loads(tokens)
            except (json.JSONDecodeError, TypeError):
                tokens = []

        # Try to get orderbook for the first token (YES outcome)
        orderbook = None
        if tokens and len(tokens) > 0:
            token_id = tokens[0].get("token_id") or tokens[0].get("tokenId")
            if token_id:
                try:
                    orderbook = _polymarket_request(
                        "/book", base=POLYMARKET_CLOB_BASE, params={"token_id": token_id}
                    )
                except Exception as ob_err:
                    logger.warning(f"Could not fetch Polymarket orderbook: {ob_err}")

        # Parse outcome prices
        outcome_prices = None
        if market.get("outcomePrices"):
            try:
                outcome_prices = json.loads(market["outcomePrices"]) if isinstance(market["outcomePrices"], str) else market["outcomePrices"]
            except (json.JSONDecodeError, TypeError):
                outcome_prices = None

        yes_price = None
        no_price = None
        if outcome_prices and len(outcome_prices) >= 2:
            try:
                yes_price = round(float(outcome_prices[0]), 4)
                no_price = round(float(outcome_prices[1]), 4)
            except (ValueError, TypeError):
                pass

        result = {
            "platform": "polymarket",
            "condition_id": condition_id,
            "title": market.get("question") or market.get("title"),
            "description": market.get("description"),
            "yes_price": yes_price,
            "no_price": no_price,
            "volume": market.get("volume") or market.get("volumeNum") or 0,
            "volume_24h": market.get("volume24hr") or 0,
            "liquidity": market.get("liquidity") or 0,
            "end_date": market.get("endDate") or market.get("end_date_iso"),
            "active": market.get("active", True),
            "tokens": [
                {
                    "token_id": t.get("token_id") or t.get("tokenId"),
                    "outcome": t.get("outcome"),
                    "price": t.get("price"),
                }
                for t in tokens[:4]
            ],
        }

        if orderbook:
            bids = orderbook.get("bids", [])[:5]
            asks = orderbook.get("asks", [])[:5]
            result["orderbook"] = {
                "bids": [{"price": b.get("price"), "size": b.get("size")} for b in bids],
                "asks": [{"price": a.get("price"), "size": a.get("size")} for a in asks],
            }

        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error fetching Polymarket market details: {str(e)}"


@mcp.tool()
def polymarket_trending(limit: int = 10) -> str:
    """
    Get top trending Polymarket markets sorted by 24-hour volume.

    Fetches active markets and ranks them by recent trading activity.

    Args:
        limit: Number of markets to return (default 10, max 50)

    Returns:
        JSON list of top Polymarket markets by volume with prices
    """
    limit = min(limit, 50)

    try:
        # Fetch a broad set of active markets sorted by volume
        params = {"limit": 100, "active": True, "order": "volume24hr", "ascending": False}
        data = _polymarket_request(
            "/markets", base=POLYMARKET_GAMMA_BASE, params=params
        )

        markets = data if isinstance(data, list) else data.get("markets", data.get("results", []))

        results = []
        for m in markets[:limit]:
            outcome_prices = None
            if m.get("outcomePrices"):
                try:
                    outcome_prices = json.loads(m["outcomePrices"]) if isinstance(m["outcomePrices"], str) else m["outcomePrices"]
                except (json.JSONDecodeError, TypeError):
                    outcome_prices = None

            yes_price = None
            if outcome_prices and len(outcome_prices) >= 1:
                try:
                    yes_price = round(float(outcome_prices[0]), 4)
                except (ValueError, TypeError):
                    pass

            results.append({
                "platform": "polymarket",
                "condition_id": m.get("conditionId") or m.get("condition_id"),
                "title": m.get("question") or m.get("title"),
                "yes_price": yes_price,
                "volume": m.get("volume") or m.get("volumeNum") or 0,
                "volume_24h": m.get("volume24hr") or 0,
                "liquidity": m.get("liquidity") or 0,
                "end_date": m.get("endDate") or m.get("end_date_iso"),
            })

        # Sort by volume_24h descending (in case API didn't sort)
        results.sort(key=lambda x: float(x.get("volume_24h") or 0), reverse=True)

        return json.dumps(results[:limit], indent=2)
    except Exception as e:
        return f"Error fetching Polymarket trending: {str(e)}"


# ================== METACULUS TOOLS ==================

@mcp.tool()
def metaculus_search(query: str, limit: int = 20) -> str:
    """
    Search Metaculus forecasting questions by keyword.

    Searches the Metaculus community forecasting platform for questions
    matching the query. Returns question titles, community predictions,
    and resolution criteria.

    Args:
        query: Search keyword (e.g., "AGI", "climate", "pandemic")
        limit: Maximum number of results (default 20, max 100)

    Returns:
        JSON list of matching Metaculus questions with community predictions
    """
    limit = min(limit, 100)

    try:
        params = {"search": query, "limit": limit, "status": "open"}
        data = _metaculus_request("/questions/", params=params)

        questions = data.get("results", [])

        results = []
        for q in questions[:limit]:
            # Extract community prediction
            community_prediction = None
            prediction_data = q.get("community_prediction") or {}
            if isinstance(prediction_data, dict):
                # Binary questions have a 'full' field with 'q2' (median)
                full = prediction_data.get("full", {})
                if isinstance(full, dict):
                    community_prediction = full.get("q2")
                elif isinstance(prediction_data.get("history"), list) and prediction_data["history"]:
                    # Get latest prediction from history
                    latest = prediction_data["history"][-1]
                    if isinstance(latest, dict):
                        community_prediction = latest.get("x2") or latest.get("means")

            results.append({
                "platform": "metaculus",
                "question_id": q.get("id"),
                "title": q.get("title"),
                "description": (q.get("description") or "")[:200],
                "community_prediction": community_prediction,
                "type": q.get("type") or q.get("possibilities", {}).get("type"),
                "created_time": q.get("created_time"),
                "resolve_time": q.get("resolve_time"),
                "close_time": q.get("close_time"),
                "number_of_forecasters": q.get("number_of_forecasters") or q.get("forecasts_count", 0),
                "url": q.get("url") or f"https://www.metaculus.com/questions/{q.get('id')}/",
            })

        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error searching Metaculus: {str(e)}"


@mcp.tool()
def metaculus_question_details(question_id: int) -> str:
    """
    Get full details for a specific Metaculus question including community prediction.

    Retrieves the full question text, resolution criteria, community prediction
    distribution, and forecaster count.

    Args:
        question_id: The Metaculus question ID (from metaculus_search results)

    Returns:
        JSON with question details, community prediction, and metadata
    """
    try:
        data = _metaculus_request(f"/questions/{question_id}/")

        # Extract community prediction details
        community_prediction = None
        prediction_details = {}
        pred_data = data.get("community_prediction") or {}
        if isinstance(pred_data, dict):
            full = pred_data.get("full", {})
            if isinstance(full, dict):
                community_prediction = full.get("q2")
                prediction_details = {
                    "median": full.get("q2"),
                    "q1": full.get("q1"),  # 25th percentile
                    "q3": full.get("q3"),  # 75th percentile
                    "mean": full.get("mean"),
                }

        result = {
            "platform": "metaculus",
            "question_id": data.get("id"),
            "title": data.get("title"),
            "description": data.get("description"),
            "resolution_criteria": data.get("resolution_criteria"),
            "fine_print": data.get("fine_print"),
            "type": data.get("type") or data.get("possibilities", {}).get("type"),
            "community_prediction": community_prediction,
            "prediction_details": prediction_details,
            "number_of_forecasters": data.get("number_of_forecasters") or data.get("forecasts_count", 0),
            "created_time": data.get("created_time"),
            "publish_time": data.get("publish_time"),
            "close_time": data.get("close_time"),
            "resolve_time": data.get("resolve_time"),
            "active": data.get("active_state") == "OPEN",
            "url": data.get("url") or f"https://www.metaculus.com/questions/{data.get('id')}/",
        }

        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error fetching Metaculus question details: {str(e)}"


@mcp.tool()
def metaculus_trending(limit: int = 10) -> str:
    """
    Get hot/trending Metaculus questions by forecaster activity.

    Fetches open questions sorted by recent activity and forecaster engagement.

    Args:
        limit: Number of questions to return (default 10, max 50)

    Returns:
        JSON list of trending Metaculus questions with community predictions
    """
    limit = min(limit, 50)

    try:
        params = {
            "limit": limit,
            "status": "open",
            "order_by": "-activity",
        }
        data = _metaculus_request("/questions/", params=params)

        questions = data.get("results", [])

        results = []
        for q in questions[:limit]:
            community_prediction = None
            pred_data = q.get("community_prediction") or {}
            if isinstance(pred_data, dict):
                full = pred_data.get("full", {})
                if isinstance(full, dict):
                    community_prediction = full.get("q2")

            results.append({
                "platform": "metaculus",
                "question_id": q.get("id"),
                "title": q.get("title"),
                "community_prediction": community_prediction,
                "number_of_forecasters": q.get("number_of_forecasters") or q.get("forecasts_count", 0),
                "close_time": q.get("close_time"),
                "url": q.get("url") or f"https://www.metaculus.com/questions/{q.get('id')}/",
            })

        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error fetching Metaculus trending: {str(e)}"


# ================== KALSHI TOOLS (proxied through unified server) ==================

@mcp.tool()
def kalshi_search(query: str, limit: int = 20) -> str:
    """
    Search Kalshi prediction markets by keyword.

    Searches across Kalshi market titles, subtitles, and tickers for matches.
    Returns market prices, volumes, and tickers for further lookup or trading.

    Args:
        query: Search keyword (e.g., "bitcoin", "fed rate", "weather chicago")
        limit: Maximum number of results (default 20, max 100)

    Returns:
        JSON list of matching Kalshi markets with ticker, title, prices, volume
    """
    limit = min(limit, 100)

    try:
        params = {"status": "open", "limit": 200}
        data = _kalshi_request("GET", "/markets", params=params)
        markets = data.get("markets", [])

        query_lower = query.lower()
        matches = [
            m for m in markets
            if query_lower in m.get("title", "").lower()
            or query_lower in m.get("subtitle", "").lower()
            or query_lower in m.get("ticker", "").lower()
        ][:limit]

        results = []
        for m in matches:
            results.append({
                "platform": "kalshi",
                "ticker": m.get("ticker"),
                "title": m.get("title"),
                "subtitle": m.get("subtitle"),
                "yes_price": m.get("yes_price", 0) / 100 if m.get("yes_price") else None,
                "no_price": m.get("no_price", 0) / 100 if m.get("no_price") else None,
                "volume_24h": m.get("volume_24h", 0),
                "close_time": m.get("close_time"),
            })

        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error searching Kalshi: {str(e)}"


@mcp.tool()
def kalshi_market_details(ticker: str) -> str:
    """
    Get full details for a specific Kalshi market including orderbook.

    Args:
        ticker: Market ticker (e.g., "KXHIGHNYC-26FEB20-B34.5")

    Returns:
        JSON with market metadata, current prices, and orderbook bids/asks
    """
    try:
        market_data = _kalshi_request("GET", f"/markets/{ticker}")
        market = market_data.get("market", market_data)

        orderbook_data = _kalshi_request("GET", f"/markets/{ticker}/orderbook")
        orderbook = orderbook_data.get("orderbook", orderbook_data)

        result = {
            "platform": "kalshi",
            "ticker": market.get("ticker"),
            "title": market.get("title"),
            "subtitle": market.get("subtitle"),
            "yes_price": market.get("yes_price", 0) / 100 if market.get("yes_price") else None,
            "no_price": market.get("no_price", 0) / 100 if market.get("no_price") else None,
            "volume_24h": market.get("volume_24h", 0),
            "open_interest": market.get("open_interest", 0),
            "close_time": market.get("close_time"),
            "orderbook": {
                "yes_bids": orderbook.get("yes", [])[:5],
                "no_bids": orderbook.get("no", [])[:5],
            },
        }

        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error fetching Kalshi market details: {str(e)}"


@mcp.tool()
def kalshi_trending(limit: int = 10) -> str:
    """
    Get top trending Kalshi markets by 24-hour volume and liquidity score.

    Args:
        limit: Number of markets to return (default 10, max 50)

    Returns:
        JSON list of Kalshi markets sorted by liquidity score
    """
    limit = min(limit, 50)

    try:
        params = {"status": "open", "limit": 200}
        data = _kalshi_request("GET", "/markets", params=params)
        markets = data.get("markets", [])

        scored = []
        for m in markets:
            volume = m.get("volume_24h", 0)
            oi = m.get("open_interest", 0)
            liquidity = (volume * 0.6) + (oi * 0.4)

            scored.append({
                "platform": "kalshi",
                "ticker": m.get("ticker"),
                "title": m.get("title"),
                "subtitle": m.get("subtitle"),
                "yes_price": m.get("yes_price", 0) / 100 if m.get("yes_price") else None,
                "volume_24h": volume,
                "liquidity_score": liquidity,
            })

        scored.sort(key=lambda x: x["liquidity_score"], reverse=True)
        return json.dumps(scored[:limit], indent=2)
    except Exception as e:
        return f"Error fetching Kalshi trending: {str(e)}"


@mcp.tool()
def kalshi_portfolio() -> str:
    """
    Get your Kalshi portfolio - account balance and all open positions.

    Requires KALSHI_API_KEY and KALSHI_PRIVATE_KEY_PATH to be configured.

    Returns:
        JSON with balance (USD) and list of open positions with P&L
    """
    try:
        balance_data = _kalshi_request("GET", "/portfolio/balance")
        balance = balance_data.get("balance", 0) / 100

        positions_data = _kalshi_request("GET", "/portfolio/positions")
        positions = positions_data.get("market_positions", [])

        result = {
            "platform": "kalshi",
            "balance_usd": f"${balance:.2f}",
            "positions": [],
        }

        for pos in positions:
            result["positions"].append({
                "ticker": pos.get("ticker"),
                "side": pos.get("position", "unknown"),
                "quantity": pos.get("total_traded", 0),
                "avg_price": pos.get("market_exposure", 0) / max(pos.get("total_traded", 1), 1) / 100,
                "current_value": pos.get("market_exposure", 0) / 100,
            })

        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error fetching Kalshi portfolio: {str(e)}"


@mcp.tool()
def kalshi_place_order(ticker: str, side: str, quantity: int, price_cents: int) -> str:
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

        data = _kalshi_request("POST", "/portfolio/orders", json=payload)
        order = data.get("order", data)

        result = {
            "platform": "kalshi",
            "order_id": order.get("order_id"),
            "status": order.get("status"),
            "ticker": ticker,
            "side": side,
            "quantity": quantity,
            "price": f"${price_cents / 100:.2f}",
            "created_at": order.get("created_time"),
        }

        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error placing Kalshi order: {str(e)}"


@mcp.tool()
def kalshi_series(series_ticker: str) -> str:
    """
    Get all markets in a Kalshi series (e.g., all Federal Reserve event markets).

    Args:
        series_ticker: Series ticker (e.g., "KXFED", "KXHIGH", "KXCPI")

    Returns:
        JSON list of markets in the series with prices and close times
    """
    try:
        params = {"series_ticker": series_ticker, "status": "open", "limit": 200}
        data = _kalshi_request("GET", "/markets", params=params)
        markets = data.get("markets", [])

        results = []
        for m in markets:
            results.append({
                "platform": "kalshi",
                "ticker": m.get("ticker"),
                "title": m.get("title"),
                "subtitle": m.get("subtitle"),
                "yes_price": m.get("yes_price", 0) / 100 if m.get("yes_price") else None,
                "close_time": m.get("close_time"),
            })

        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error fetching Kalshi series: {str(e)}"


# ================== CROSS-PLATFORM TOOLS ==================

def _normalize_price(price_value: Any) -> Optional[float]:
    """Normalize a price value to a float between 0 and 1, or None."""
    if price_value is None:
        return None
    try:
        val = float(price_value)
        # Kalshi returns 0-1 after our conversion, Polymarket returns 0-1, Metaculus returns 0-1
        if val > 1.0:
            val = val / 100.0
        return round(val, 4)
    except (ValueError, TypeError):
        return None


@mcp.tool()
def cross_platform_search(query: str, limit: int = 10) -> str:
    """
    Search ALL 3 prediction market platforms simultaneously.

    Searches Kalshi, Polymarket, and Metaculus in parallel for markets/questions
    matching the query, returning a unified result set. This is the best starting
    point for finding prediction markets on any topic.

    Args:
        query: Search keyword (e.g., "bitcoin", "election", "AI", "recession")
        limit: Maximum results per platform (default 10, max 30)

    Returns:
        JSON with results grouped by platform, each with unified format
    """
    limit = min(limit, 30)
    all_results = {
        "query": query,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "platforms": {},
        "errors": [],
    }

    # Search Kalshi
    try:
        kalshi_raw = json.loads(kalshi_search(query, limit=limit))
        all_results["platforms"]["kalshi"] = {
            "count": len(kalshi_raw),
            "markets": kalshi_raw,
        }
    except Exception as e:
        all_results["errors"].append(f"Kalshi: {str(e)}")
        all_results["platforms"]["kalshi"] = {"count": 0, "markets": []}

    # Search Polymarket
    try:
        poly_raw = json.loads(polymarket_search(query, limit=limit))
        all_results["platforms"]["polymarket"] = {
            "count": len(poly_raw),
            "markets": poly_raw,
        }
    except Exception as e:
        all_results["errors"].append(f"Polymarket: {str(e)}")
        all_results["platforms"]["polymarket"] = {"count": 0, "markets": []}

    # Search Metaculus
    try:
        meta_raw = json.loads(metaculus_search(query, limit=limit))
        all_results["platforms"]["metaculus"] = {
            "count": len(meta_raw),
            "markets": meta_raw,
        }
    except Exception as e:
        all_results["errors"].append(f"Metaculus: {str(e)}")
        all_results["platforms"]["metaculus"] = {"count": 0, "markets": []}

    total = sum(p["count"] for p in all_results["platforms"].values())
    all_results["total_results"] = total

    return json.dumps(all_results, indent=2)


@mcp.tool()
def arbitrage_scanner(query: str) -> str:
    """
    Find the same event across Kalshi, Polymarket, and Metaculus and highlight price discrepancies.

    Searches all 3 platforms for a topic, groups similar markets, and identifies
    potential arbitrage opportunities where the same event is priced differently
    across platforms. A price discrepancy of >5% is flagged as noteworthy.

    Args:
        query: Event or topic to scan (e.g., "fed rate cut", "bitcoin 100k", "Trump election")

    Returns:
        JSON with cross-platform price comparison and arbitrage opportunities
    """
    try:
        # Gather results from all platforms
        kalshi_results = []
        polymarket_results = []
        metaculus_results = []

        try:
            kalshi_results = json.loads(kalshi_search(query, limit=15))
        except Exception as e:
            logger.warning(f"Kalshi search failed in arbitrage scan: {e}")

        try:
            polymarket_results = json.loads(polymarket_search(query, limit=15))
        except Exception as e:
            logger.warning(f"Polymarket search failed in arbitrage scan: {e}")

        try:
            metaculus_results = json.loads(metaculus_search(query, limit=15))
        except Exception as e:
            logger.warning(f"Metaculus search failed in arbitrage scan: {e}")

        # Normalize all results into a common format for comparison
        normalized = []

        for m in kalshi_results:
            normalized.append({
                "platform": "kalshi",
                "id": m.get("ticker"),
                "title": m.get("title", "") + " " + (m.get("subtitle") or ""),
                "yes_price": _normalize_price(m.get("yes_price")),
                "volume": m.get("volume_24h", 0),
            })

        for m in polymarket_results:
            normalized.append({
                "platform": "polymarket",
                "id": m.get("condition_id"),
                "title": m.get("title", ""),
                "yes_price": _normalize_price(m.get("yes_price")),
                "volume": m.get("volume_24h") or m.get("volume", 0),
            })

        for m in metaculus_results:
            normalized.append({
                "platform": "metaculus",
                "id": str(m.get("question_id")),
                "title": m.get("title", ""),
                "yes_price": _normalize_price(m.get("community_prediction")),
                "volume": m.get("number_of_forecasters", 0),
            })

        # Simple keyword-overlap matching to find cross-platform pairs
        # Extract key terms from each title for fuzzy matching
        def _extract_terms(title: str) -> set:
            """Extract meaningful terms from a title for comparison."""
            stop_words = {
                "the", "a", "an", "is", "are", "was", "were", "will", "be",
                "to", "of", "in", "on", "at", "by", "for", "with", "from",
                "or", "and", "not", "no", "yes", "if", "it", "its", "this",
                "that", "than", "what", "when", "where", "who", "how",
                "before", "after", "above", "below", "between", "during",
            }
            terms = set()
            for word in title.lower().split():
                cleaned = "".join(c for c in word if c.isalnum())
                if cleaned and len(cleaned) > 2 and cleaned not in stop_words:
                    terms.add(cleaned)
            return terms

        # Find potential matches across platforms
        opportunities = []
        seen_pairs = set()

        for i, a in enumerate(normalized):
            terms_a = _extract_terms(a["title"])
            if not terms_a or a["yes_price"] is None:
                continue

            for j, b in enumerate(normalized):
                if i >= j or a["platform"] == b["platform"]:
                    continue
                if b["yes_price"] is None:
                    continue

                pair_key = (min(i, j), max(i, j))
                if pair_key in seen_pairs:
                    continue

                terms_b = _extract_terms(b["title"])
                if not terms_b:
                    continue

                # Calculate Jaccard similarity
                intersection = terms_a & terms_b
                union = terms_a | terms_b
                similarity = len(intersection) / len(union) if union else 0

                if similarity >= 0.3:  # At least 30% term overlap
                    seen_pairs.add(pair_key)
                    price_diff = abs(a["yes_price"] - b["yes_price"])
                    price_diff_pct = price_diff * 100

                    opp = {
                        "market_a": {
                            "platform": a["platform"],
                            "id": a["id"],
                            "title": a["title"].strip(),
                            "yes_price": a["yes_price"],
                        },
                        "market_b": {
                            "platform": b["platform"],
                            "id": b["id"],
                            "title": b["title"].strip(),
                            "yes_price": b["yes_price"],
                        },
                        "title_similarity": round(similarity, 3),
                        "price_difference": round(price_diff, 4),
                        "price_difference_pct": round(price_diff_pct, 2),
                        "noteworthy": price_diff_pct > 5.0,
                        "shared_terms": sorted(list(intersection)),
                    }
                    opportunities.append(opp)

        # Sort by price difference descending
        opportunities.sort(key=lambda x: x["price_difference"], reverse=True)

        result = {
            "query": query,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "platforms_searched": {
                "kalshi": len(kalshi_results),
                "polymarket": len(polymarket_results),
                "metaculus": len(metaculus_results),
            },
            "total_markets_found": len(normalized),
            "cross_platform_matches": len(opportunities),
            "noteworthy_discrepancies": sum(1 for o in opportunities if o["noteworthy"]),
            "opportunities": opportunities,
        }

        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error running arbitrage scan: {str(e)}"


# ================== RESOURCES ==================

@mcp.resource("kalshi://balance")
def get_balance_resource() -> str:
    """Get current Kalshi account balance."""
    try:
        data = _kalshi_request("GET", "/portfolio/balance")
        balance = data.get("balance", 0) / 100
        return f"Kalshi Account Balance: ${balance:.2f}"
    except Exception as e:
        return f"Error fetching balance: {str(e)}"


@mcp.resource("kalshi://positions")
def get_positions_resource() -> str:
    """Get list of open Kalshi positions."""
    try:
        data = _kalshi_request("GET", "/portfolio/positions")
        positions = data.get("market_positions", [])

        if not positions:
            return "No open positions"

        lines = ["Open Kalshi Positions:", ""]
        for pos in positions:
            ticker = pos.get("ticker")
            side = pos.get("position", "unknown")
            qty = pos.get("total_traded", 0)
            lines.append(f"  {ticker}: {qty} {side}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching positions: {str(e)}"


# ================== STARTUP ==================

def initialize():
    """Initialize HTTP clients. Kalshi auth is lazy-loaded on first Kalshi request."""
    logger.info("Prediction Markets MCP server starting")
    logger.info("Platforms: Polymarket (public), Metaculus (public), Kalshi (auth required)")

    # Pre-create clients for connection reuse
    _get_client("polymarket")
    _get_client("metaculus")

    # Kalshi auth is deferred - will initialize on first Kalshi tool call
    if KALSHI_API_KEY_VAL:
        logger.info("Kalshi API key detected - Kalshi tools will be available")
    else:
        logger.warning(
            "KALSHI_API_KEY not set - Kalshi tools will fail. "
            "Set KALSHI_API_KEY and KALSHI_PRIVATE_KEY_PATH for Kalshi access."
        )

    logger.info("Prediction Markets MCP server initialized")


if __name__ == "__main__":
    initialize()
    mcp.run()
