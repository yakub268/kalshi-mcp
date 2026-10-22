"""
Enhanced Kalshi MCP Server - Prediction market integration with improved error handling
Features: Stack traces, timestamps, retry mechanisms, file logging, connection pooling
"""

import os
import time
import base64
import logging
import traceback
from typing import Optional, Dict, List, Any
from datetime import datetime
from pathlib import Path
import json
from functools import wraps

import httpx
from mcp.server.fastmcp import FastMCP
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

# ================== ENHANCED LOGGING SETUP ==================

def setup_logging():
    """Setup enhanced file + console logging with timestamps and rotation."""

    # Create logs directory
    log_dir = Path(os.path.expanduser("~/.claude/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    # Setup root logger
    logger = logging.getLogger("kalshi-mcp")
    logger.setLevel(logging.DEBUG)

    # Remove existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Create formatters
    detailed_formatter = logging.Formatter(
        '%(asctime)s.%(msecs)03d | %(name)s | %(levelname)-8s | %(funcName)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    console_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-5s | %(message)s',
        datefmt='%H:%M:%S'
    )

    # File handler (with rotation)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "kalshi_mcp.log",
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed_formatter)

    # Error file handler
    error_handler = logging.handlers.RotatingFileHandler(
        log_dir / "kalshi_mcp_errors.log",
        maxBytes=5*1024*1024,  # 5MB
        backupCount=3
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(detailed_formatter)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(console_formatter)

    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(error_handler)
    logger.addHandler(console_handler)

    # Also import RotatingFileHandler
    import logging.handlers

    return logger

# Setup logger first
logger = setup_logging()

# Initialize FastMCP server
mcp = FastMCP("kalshi")

# ================== ENHANCED ERROR HANDLING ==================

class KalshiError(Exception):
    """Base exception for Kalshi MCP operations."""
    def __init__(self, message: str, original_error: Optional[Exception] = None, context: Optional[Dict] = None):
        super().__init__(message)
        self.original_error = original_error
        self.context = context or {}
        self.timestamp = datetime.now().isoformat()

class KalshiConnectionError(KalshiError):
    """Connection-related errors."""
    pass

class KalshiAPIError(KalshiError):
    """API response errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, response_text: Optional[str] = None, **kwargs):
        super().__init__(message, **kwargs)
        self.status_code = status_code
        self.response_text = response_text

class KalshiAuthError(KalshiError):
    """Authentication errors."""
    pass

def enhanced_error_handler(func):
    """Decorator to add enhanced error handling with stack traces and context."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        func_name = func.__name__

        try:
            logger.debug(f"Starting {func_name} with args={args[1:]} kwargs={kwargs}")
            result = func(*args, **kwargs)
            duration = time.time() - start_time
            logger.debug(f"Completed {func_name} in {duration:.3f}s")
            return result

        except Exception as e:
            duration = time.time() - start_time

            # Collect detailed error context
            error_context = {
                'function': func_name,
                'duration': f"{duration:.3f}s",
                'args': str(args[1:]) if len(args) > 1 else None,  # Skip 'self' arg
                'kwargs': kwargs,
                'timestamp': datetime.now().isoformat(),
                'stack_trace': traceback.format_exc(),
                'error_type': type(e).__name__,
                'error_message': str(e)
            }

            # Log with full context
            logger.error(
                f"Error in {func_name}: {type(e).__name__}: {str(e)}\n"
                f"Context: {json.dumps(error_context, indent=2)}\n"
                f"Stack trace:\n{traceback.format_exc()}"
            )

            # Re-raise as enhanced error if not already
            if not isinstance(e, KalshiError):
                raise KalshiError(
                    f"Error in {func_name}: {str(e)}",
                    original_error=e,
                    context=error_context
                )
            raise e

    return wrapper

# ================== ENHANCED CONNECTION & RETRY LOGIC ==================

class RetryConfig:
    """Configuration for retry logic."""
    def __init__(self):
        self.max_retries = int(os.getenv("KALSHI_MAX_RETRIES", "3"))
        self.base_delay = float(os.getenv("KALSHI_BASE_DELAY", "1.0"))
        self.max_delay = float(os.getenv("KALSHI_MAX_DELAY", "60.0"))
        self.exponential_base = float(os.getenv("KALSHI_EXPONENTIAL_BASE", "2.0"))
        self.jitter = True

def calculate_backoff_delay(attempt: int, config: RetryConfig) -> float:
    """Calculate exponential backoff delay with jitter."""
    delay = config.base_delay * (config.exponential_base ** attempt)
    delay = min(delay, config.max_delay)

    if config.jitter:
        import random
        delay *= (0.5 + random.random() * 0.5)  # Add 0-50% jitter

    return delay

def should_retry_error(error: Exception, attempt: int, max_retries: int) -> bool:
    """Determine if an error should trigger a retry."""
    if attempt >= max_retries:
        return False

    # Always retry connection errors
    if isinstance(error, (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)):
        return True

    # Retry specific HTTP status codes
    if isinstance(error, httpx.HTTPStatusError):
        retryable_codes = {429, 500, 502, 503, 504}  # Rate limit, server errors
        return error.response.status_code in retryable_codes

    return False

# ================== API CONFIGURATION ==================

API_BASE = os.getenv("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")
API_KEY = os.getenv("KALSHI_API_KEY")
PRIVATE_KEY_PATH = os.path.expanduser(os.getenv("KALSHI_PRIVATE_KEY_PATH", "~/.trading_keys/kalshi_private_key.pem"))

# Enhanced HTTP client with connection pooling
http_client: Optional[httpx.Client] = None
private_key = None
last_request_time = 0.0
MIN_REQUEST_INTERVAL = 0.15  # 150ms between requests
retry_config = RetryConfig()

# ================== AUTHENTICATION ==================

@enhanced_error_handler
def _load_private_key():
    """Load RSA private key from file with enhanced error handling."""
    if not os.path.exists(PRIVATE_KEY_PATH):
        raise KalshiAuthError(f"Kalshi private key not found at {PRIVATE_KEY_PATH}")

    try:
        with open(PRIVATE_KEY_PATH, "rb") as f:
            key = serialization.load_pem_private_key(
                f.read(),
                password=None,
                backend=default_backend()
            )
        logger.info("Successfully loaded Kalshi private key")
        return key
    except Exception as e:
        raise KalshiAuthError(f"Failed to load private key: {str(e)}", original_error=e)

@enhanced_error_handler
def _sign_request(method: str, path: str, timestamp: int) -> str:
    """Create RSA-PSS signature for Kalshi API request with enhanced error handling."""
    try:
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
    except Exception as e:
        raise KalshiAuthError(f"Failed to sign request: {str(e)}", original_error=e)

# ================== ENHANCED HTTP CLIENT ==================

@enhanced_error_handler
def _request(method: str, endpoint: str, **kwargs) -> Dict:
    """
    Make authenticated request to Kalshi API with enhanced retry logic and error handling.
    """
    global last_request_time, http_client

    attempt = 0
    last_error = None

    while attempt <= retry_config.max_retries:
        try:
            # Rate limiting
            elapsed = time.time() - last_request_time
            if elapsed < MIN_REQUEST_INTERVAL:
                sleep_time = MIN_REQUEST_INTERVAL - elapsed
                logger.debug(f"Rate limiting: sleeping {sleep_time:.3f}s")
                time.sleep(sleep_time)
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

            # Log request details
            logger.debug(f"Making {method.upper()} request to {endpoint} (attempt {attempt + 1})")

            response = http_client.request(method, url, headers=headers, **kwargs)

            # Log response details
            logger.debug(f"Response: {response.status_code} from {endpoint}")

            response.raise_for_status()
            return response.json()

        except Exception as e:
            last_error = e
            attempt += 1

            # Create appropriate error type
            if isinstance(e, httpx.HTTPStatusError):
                api_error = KalshiAPIError(
                    f"API request failed: {e.response.status_code} {e.response.reason_phrase}",
                    status_code=e.response.status_code,
                    response_text=e.response.text,
                    original_error=e,
                    context={'endpoint': endpoint, 'method': method, 'attempt': attempt}
                )
            elif isinstance(e, (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)):
                api_error = KalshiConnectionError(
                    f"Connection failed: {str(e)}",
                    original_error=e,
                    context={'endpoint': endpoint, 'method': method, 'attempt': attempt}
                )
            else:
                api_error = KalshiError(
                    f"Request failed: {str(e)}",
                    original_error=e,
                    context={'endpoint': endpoint, 'method': method, 'attempt': attempt}
                )

            # Check if we should retry
            if should_retry_error(e, attempt - 1, retry_config.max_retries):
                backoff_delay = calculate_backoff_delay(attempt - 1, retry_config)
                logger.warning(
                    f"Request to {endpoint} failed (attempt {attempt}/{retry_config.max_retries + 1}): "
                    f"{type(e).__name__}: {str(e)}. Retrying in {backoff_delay:.2f}s"
                )
                time.sleep(backoff_delay)
                continue
            else:
                # No more retries, log final error and raise
                logger.error(
                    f"Request to {endpoint} failed after {attempt} attempts: "
                    f"{type(e).__name__}: {str(e)}"
                )
                raise api_error

    # Should never reach here, but just in case
    raise last_error

# ================== ENHANCED TOOLS WITH ERROR HANDLING ==================

@mcp.tool()
@enhanced_error_handler
def search_markets(query: str, limit: int = 20) -> str:
    """
    Search Kalshi prediction markets by keyword.

    Args:
        query: Search keyword (e.g., "bitcoin", "fed rate", "weather chicago")
        limit: Maximum number of results (default 20, max 100)

    Returns:
        JSON-formatted list of matching markets with ticker, title, prices, volume
    """
    try:
        limit = min(limit, 100)
        params = {'status': 'open', 'limit': 200}

        logger.info(f"Searching markets for query: '{query}', limit: {limit}")
        data = _request("GET", "/markets", params=params)
        markets = data.get('markets', [])
        logger.debug(f"Retrieved {len(markets)} markets from API")

        # Filter by keyword
        query_lower = query.lower()
        matches = [
            m for m in markets
            if query_lower in m.get('title', '').lower() or
               query_lower in m.get('subtitle', '').lower() or
               query_lower in m.get('ticker', '').lower()
        ][:limit]

        logger.info(f"Found {len(matches)} markets matching query '{query}'")

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

        return json.dumps(results, indent=2)

    except Exception as e:
        error_msg = f"Error searching markets for '{query}': {str(e)}"
        if isinstance(e, KalshiError):
            error_msg += f" (Timestamp: {e.timestamp})"
            if e.original_error:
                error_msg += f" Original error: {str(e.original_error)}"
        logger.error(error_msg)
        return json.dumps({"error": error_msg, "timestamp": datetime.now().isoformat()})

@mcp.tool()
@enhanced_error_handler
def get_market_details(ticker: str) -> str:
    """
    Get full details for a specific market including orderbook.

    Args:
        ticker: Market ticker (e.g., "KXHIGHNYC-26FEB20-B34.5")

    Returns:
        JSON with market metadata, current prices, orderbook bids/asks
    """
    try:
        logger.info(f"Getting market details for ticker: {ticker}")

        # Get market info and orderbook in parallel if possible
        market_data = _request("GET", f"/markets/{ticker}")
        market = market_data.get('market', market_data)

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

        logger.debug(f"Successfully retrieved market details for {ticker}")
        return json.dumps(result, indent=2)

    except Exception as e:
        error_msg = f"Error fetching market details for '{ticker}': {str(e)}"
        if isinstance(e, KalshiError):
            error_msg += f" (Timestamp: {e.timestamp})"
        logger.error(error_msg)
        return json.dumps({"error": error_msg, "timestamp": datetime.now().isoformat()})

@mcp.tool()
@enhanced_error_handler
def get_portfolio() -> str:
    """
    Get your Kalshi portfolio - account balance and all open positions.

    Returns:
        JSON with balance (USD) and list of open positions with P&L
    """
    try:
        logger.info("Retrieving portfolio information")

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

        logger.info(f"Portfolio: ${balance:.2f} balance, {len(positions)} positions")
        return json.dumps(result, indent=2)

    except Exception as e:
        error_msg = f"Error fetching portfolio: {str(e)}"
        if isinstance(e, KalshiError):
            error_msg += f" (Timestamp: {e.timestamp})"
        logger.error(error_msg)
        return json.dumps({"error": error_msg, "timestamp": datetime.now().isoformat()})

@mcp.tool()
@enhanced_error_handler
def place_order(ticker: str, side: str, quantity: int, price_cents: int) -> str:
    """
    Place a limit order on Kalshi with enhanced validation and error handling.

    Args:
        ticker: Market ticker (e.g., "KXHIGHNYC-26FEB20-B34.5")
        side: "yes" or "no"
        quantity: Number of contracts (must be positive integer)
        price_cents: Price in cents, 1-99 (e.g., 25 = $0.25)

    Returns:
        Order confirmation with order ID and status
    """
    try:
        # Enhanced validation
        if side not in ("yes", "no"):
            raise ValueError(f"side must be 'yes' or 'no', got '{side}'")
        if not isinstance(quantity, int) or quantity < 1:
            raise ValueError(f"quantity must be positive integer, got {quantity}")
        if not isinstance(price_cents, int) or price_cents < 1 or price_cents > 99:
            raise ValueError(f"price_cents must be 1-99, got {price_cents}")

        logger.info(f"Placing order: {quantity} {side} contracts of {ticker} @ ${price_cents/100:.2f}")

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

        logger.info(f"Order placed successfully: {result['order_id']} ({result['status']})")
        return json.dumps(result, indent=2)

    except Exception as e:
        error_msg = f"Error placing order for {ticker}: {str(e)}"
        if isinstance(e, KalshiError):
            error_msg += f" (Timestamp: {e.timestamp})"
        logger.error(error_msg)
        return json.dumps({"error": error_msg, "timestamp": datetime.now().isoformat()})

# ================== ADDITIONAL TOOLS (Enhanced versions of existing ones) ==================

@mcp.tool()
@enhanced_error_handler
def get_trending_markets(limit: int = 10) -> str:
    """Get top trending markets by 24-hour volume with enhanced error handling."""
    try:
        limit = min(limit, 50)
        logger.info(f"Getting {limit} trending markets")

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
        logger.info(f"Calculated liquidity scores for {len(scored)} markets")

        return json.dumps(scored[:limit], indent=2)

    except Exception as e:
        error_msg = f"Error fetching trending markets: {str(e)}"
        if isinstance(e, KalshiError):
            error_msg += f" (Timestamp: {e.timestamp})"
        logger.error(error_msg)
        return json.dumps({"error": error_msg, "timestamp": datetime.now().isoformat()})

@mcp.tool()
@enhanced_error_handler
def get_series_markets(series_ticker: str) -> str:
    """Get all markets in a series with enhanced error handling."""
    try:
        logger.info(f"Getting markets for series: {series_ticker}")

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

        logger.info(f"Found {len(results)} markets in series {series_ticker}")
        return json.dumps(results, indent=2)

    except Exception as e:
        error_msg = f"Error fetching series markets for '{series_ticker}': {str(e)}"
        if isinstance(e, KalshiError):
            error_msg += f" (Timestamp: {e.timestamp})"
        logger.error(error_msg)
        return json.dumps({"error": error_msg, "timestamp": datetime.now().isoformat()})

# ================== HEALTH CHECK TOOL ==================

@mcp.tool()
@enhanced_error_handler
def health_check() -> str:
    """
    Perform a comprehensive health check of the Kalshi MCP server.
    Tests authentication, API connectivity, and basic functionality.
    """
    try:
        health_results = {
            'timestamp': datetime.now().isoformat(),
            'server_status': 'healthy',
            'checks': {}
        }

        logger.info("Starting health check...")

        # Test 1: Private key loading
        try:
            test_key = _load_private_key()
            health_results['checks']['private_key'] = 'OK'
            logger.debug("Private key check: OK")
        except Exception as e:
            health_results['checks']['private_key'] = f'FAILED: {str(e)}'
            health_results['server_status'] = 'degraded'

        # Test 2: API connectivity
        try:
            # Simple GET request to test connectivity
            _request("GET", "/markets", params={'limit': 1})
            health_results['checks']['api_connectivity'] = 'OK'
            logger.debug("API connectivity check: OK")
        except Exception as e:
            health_results['checks']['api_connectivity'] = f'FAILED: {str(e)}'
            health_results['server_status'] = 'unhealthy'

        # Test 3: Authentication
        try:
            # Test auth by getting balance (requires valid authentication)
            _request("GET", "/portfolio/balance")
            health_results['checks']['authentication'] = 'OK'
            logger.debug("Authentication check: OK")
        except Exception as e:
            health_results['checks']['authentication'] = f'FAILED: {str(e)}'
            health_results['server_status'] = 'degraded'

        # Test 4: HTTP client status
        if http_client:
            health_results['checks']['http_client'] = 'OK'
        else:
            health_results['checks']['http_client'] = 'NOT_INITIALIZED'
            health_results['server_status'] = 'degraded'

        # Test 5: Configuration
        config_issues = []
        if not API_KEY:
            config_issues.append("KALSHI_API_KEY not set")
        if not os.path.exists(PRIVATE_KEY_PATH):
            config_issues.append(f"Private key file not found: {PRIVATE_KEY_PATH}")

        if config_issues:
            health_results['checks']['configuration'] = f'ISSUES: {"; ".join(config_issues)}'
            health_results['server_status'] = 'unhealthy'
        else:
            health_results['checks']['configuration'] = 'OK'

        # Summary
        passed_checks = sum(1 for check in health_results['checks'].values() if check == 'OK')
        total_checks = len(health_results['checks'])
        health_results['summary'] = f"{passed_checks}/{total_checks} checks passed"

        logger.info(f"Health check completed: {health_results['server_status']} ({health_results['summary']})")
        return json.dumps(health_results, indent=2)

    except Exception as e:
        error_msg = f"Health check failed: {str(e)}"
        logger.error(error_msg)
        return json.dumps({
            'timestamp': datetime.now().isoformat(),
            'server_status': 'unhealthy',
            'error': error_msg
        }, indent=2)

# ================== RESOURCES (Enhanced with error handling) ==================

@mcp.resource("kalshi://balance")
@enhanced_error_handler
def get_balance_resource() -> str:
    """Get current Kalshi account balance with enhanced error handling."""
    try:
        data = _request("GET", "/portfolio/balance")
        balance = data.get('balance', 0) / 100
        return f"Kalshi Account Balance: ${balance:.2f}"
    except Exception as e:
        return f"Error fetching balance: {str(e)} (Timestamp: {datetime.now().isoformat()})"

@mcp.resource("kalshi://positions")
@enhanced_error_handler
def get_positions_resource() -> str:
    """Get list of open Kalshi positions with enhanced error handling."""
    try:
        data = _request("GET", "/portfolio/positions")
        positions = data.get('market_positions', [])

        if not positions:
            return "No open positions"

        lines = [f"Open Kalshi Positions (as of {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}):", ""]
        for pos in positions:
            ticker = pos.get('ticker')
            side = pos.get('position', 'unknown')
            qty = pos.get('total_traded', 0)
            lines.append(f"  {ticker}: {qty} {side}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching positions: {str(e)} (Timestamp: {datetime.now().isoformat()})"

# ================== ENHANCED STARTUP ==================

@enhanced_error_handler
def initialize():
    """Initialize API client and credentials with enhanced error handling."""
    global private_key, http_client

    logger.info("Initializing Kalshi MCP server...")

    if not API_KEY:
        raise KalshiAuthError("KALSHI_API_KEY environment variable not set")

    # Load private key
    private_key = _load_private_key()

    # Initialize HTTP client with enhanced configuration
    timeout_config = httpx.Timeout(
        timeout=30.0,          # Total timeout
        connect=10.0,          # Connection timeout
        read=20.0,             # Read timeout
        write=10.0             # Write timeout
    )

    limits = httpx.Limits(
        max_connections=20,     # Max connections total
        max_keepalive_connections=10  # Max persistent connections
    )

    http_client = httpx.Client(
        timeout=timeout_config,
        limits=limits,
        follow_redirects=True,
        verify=True  # SSL verification
    )

    logger.info("Kalshi MCP server initialized successfully")
    logger.info(f"Configuration: API_BASE={API_BASE}, Retry config: max_retries={retry_config.max_retries}")

if __name__ == "__main__":
    try:
        initialize()
        mcp.run()
    except Exception as e:
        logger.critical(f"Failed to start Kalshi MCP server: {str(e)}\n{traceback.format_exc()}")
        raise