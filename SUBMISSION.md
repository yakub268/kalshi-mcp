# MCP Registry Submission Guide

## Repository Status
✅ **GitHub Repository**: https://github.com/yakub268/kalshi-mcp
✅ **Initial Release**: v0.1.0 committed and pushed

## Registry Submission (Next Steps)

The [official MCP Registry](https://registry.modelcontextprotocol.io/) requires:

### 1. Publish to PyPI (Python Package Index)

```bash
# Install build tools
pip install build twine

# Build distribution
python -m build

# Upload to PyPI (requires PyPI account)
twine upload dist/*
```

### 2. Update `pyproject.toml` with Registry Metadata

Add to `pyproject.toml`:
```toml
[project]
mcp-name = "io.github.yakub268/kalshi"
```

### 3. Publish to MCP Registry

Use the official `mcp-publisher` tool:

```bash
# Install publisher
npm install -g @modelcontextprotocol/mcp-publisher

# Create server.json (required metadata)
# See: https://modelcontextprotocol.io/registry/quickstart

# Publish to registry
mcp publish server.json
```

### 4. Required `server.json` Structure

```json
{
  "name": "io.github.yakub268/kalshi",
  "version": "0.1.0",
  "description": "MCP server for Kalshi prediction markets",
  "sourceUrl": "https://github.com/yakub268/kalshi-mcp",
  "installationMethod": "pip",
  "packageName": "kalshi-mcp",
  "tools": [
    "search_markets",
    "get_market_details",
    "get_portfolio",
    "get_trending_markets",
    "place_order",
    "get_series_markets"
  ],
  "resources": [
    "kalshi://balance",
    "kalshi://positions"
  ],
  "categories": ["finance", "trading", "prediction-markets"]
}
```

## Alternative: Community Discovery

While preparing for official registry:
- Add topic tags on GitHub: `mcp-server`, `prediction-markets`, `kalshi`, `claude-desktop`
- Share on MCP community Discord/forums
- List in README.md awesome lists (awesome-mcp-servers)

## References

- Registry quickstart: https://modelcontextprotocol.io/registry/quickstart
- MCP Registry docs: https://registry.modelcontextprotocol.io/docs
- MCP Registry GitHub: https://github.com/modelcontextprotocol/registry

---

**Status**: Repository created, local testing complete. PyPI publication pending (requires account setup).
