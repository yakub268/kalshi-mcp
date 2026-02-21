# Kalshi MCP Server

[![MCP](https://img.shields.io/badge/MCP-Compatible-blue)](https://modelcontextprotocol.io)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**Model Context Protocol server for Kalshi prediction markets.** Search, analyze, and trade prediction markets directly through Claude Desktop.

Built with production-grade authentication and rate limiting from a live trading system with 4+ months of uptime.

## Features

### Tools (6)
- **`search_markets`** - Search by keyword, get prices/volume
- **`get_market_details`** - Full market info + orderbook depth
- **`get_portfolio`** - Account balance + open positions
- **`get_trending_markets`** - Top markets by 24h volume
- **`place_order`** - Execute limit orders
- **`get_series_markets`** - All markets in a series (e.g., Fed events)

### Resources (2)
- **`kalshi://balance`** - Current account balance
- **`kalshi://positions`** - Open positions list

## Installation

### Prerequisites
1. **Kalshi API credentials**: Get from [kalshi.com/profile/api-keys](https://kalshi.com/profile/api-keys)
   - Download your API key ID
   - Download the RSA private key (.pem file)

2. **Python 3.10+**

### Setup

```bash
# Clone repository
git clone https://github.com/yakub268/kalshi-mcp.git
cd kalshi-mcp

# Install dependencies
uv pip install -e .

# Configure credentials
cp .env.example .env
# Edit .env with your API key and private key path
```

### Claude Desktop Configuration

Add to your Claude Desktop config file:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "kalshi": {
      "command": "uv",
      "args": [
        "--directory",
        "/ABSOLUTE/PATH/TO/kalshi-mcp",
        "run",
        "kalshi_mcp.py"
      ],
      "env": {
        "KALSHI_API_KEY": "your-api-key-here",
        "KALSHI_PRIVATE_KEY_PATH": "/path/to/kalshi_private_key.pem"
      }
    }
  }
}
```

**Important**: Replace `/ABSOLUTE/PATH/TO/kalshi-mcp` with the actual path where you cloned the repository.

### Test the Connection

Restart Claude Desktop, then try:

```
What's my Kalshi balance?
```

or

```
Search for bitcoin prediction markets
```

## Usage Examples

### Search for Markets
```
Search for markets about the Federal Reserve
```

### Get Market Analysis
```
Show me details for ticker KXFED-26MAR19-B5.25
```

### Check Portfolio
```
What's my current Kalshi portfolio?
```

### Place an Order
```
Buy 10 contracts of KXHIGHNYC-26FEB20-B34.5 YES at 25 cents
```

## Authentication

This server uses **RSA-PSS signature authentication**:
1. Each request is signed with your private key
2. Kalshi verifies the signature with your public key
3. Thread-safe rate limiting (150ms between requests)
4. Automatic retry on 429 rate limit errors

**Security**: Your private key never leaves your machine. The server only signs requests locally.

## Rate Limiting

- Built-in 150ms spacing between requests (~6.6 req/s)
- Automatic exponential backoff on 429 errors (0.5s → 1s → 2s)
- Safe for concurrent use across multiple Claude conversations

## Architecture

Built on production code from a live Kalshi trading bot:
- **Authentication**: Reused from `kalshi_client.py` (4+ months uptime)
- **Rate limiting**: Shared across all client instances
- **Error handling**: Battle-tested retry logic
- **Market discovery**: Liquidity scoring from `scanner.py`

## Contributing

Issues and PRs welcome! This is an open-source project built to fill a gap in the MCP ecosystem.

## License

MIT License - see LICENSE file

## Acknowledgments

- Built with [FastMCP](https://github.com/jlowin/fastmcp)
- Kalshi API documentation: [docs.kalshi.com](https://docs.kalshi.com)
- Production code from my [trading bot arsenal](https://github.com/yakub268/trading_bot)

---

**Questions?** Open an issue or reach out on GitHub.
