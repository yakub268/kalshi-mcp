# Enhanced Kalshi MCP Server Configuration

## Key Improvements

### 1. Enhanced Error Messages with Stack Traces & Timestamps
- All errors now include full stack traces, timestamps, and detailed context
- Custom error types: `KalshiError`, `KalshiConnectionError`, `KalshiAPIError`, `KalshiAuthError`
- Error context includes function name, duration, arguments, and execution details
- Structured error logging with JSON formatting

### 2. Comprehensive Retry Mechanism
- **Connection failures**: Automatic retry with exponential backoff
- **Rate limits (429)**: Existing retry logic enhanced
- **Server errors (500-504)**: Automatic retry
- **Configurable**: Environment variables for retry settings

### 3. File Logging with Rotation
- **Main log**: `~/.claude/logs/kalshi_mcp.log` (10MB, 5 backups)
- **Error log**: `~/.claude/logs/kalshi_mcp_errors.log` (5MB, 3 backups)
- **Console output**: Still available for immediate feedback
- **Rotating logs**: Automatic cleanup of old log files

### 4. Enhanced HTTP Client
- Connection pooling for better performance
- Configurable timeouts (connect, read, write)
- Better SSL verification and redirect handling
- Persistent connections with keep-alive

### 5. Health Check Tool
- New `health_check()` tool for monitoring server health
- Tests authentication, connectivity, configuration
- Provides detailed status report

## Environment Variables

```bash
# Retry Configuration
KALSHI_MAX_RETRIES=3                # Max retry attempts (default: 3)
KALSHI_BASE_DELAY=1.0               # Base delay in seconds (default: 1.0)
KALSHI_MAX_DELAY=60.0               # Max delay in seconds (default: 60.0)
KALSHI_EXPONENTIAL_BASE=2.0         # Exponential backoff base (default: 2.0)

# Timeout Configuration
KALSHI_CONNECT_TIMEOUT=10           # Connection timeout (default: 10s)
KALSHI_READ_TIMEOUT=20              # Read timeout (default: 20s)
KALSHI_TOTAL_TIMEOUT=30             # Total request timeout (default: 30s)

# Existing Configuration
KALSHI_API_KEY=your_api_key
KALSHI_PRIVATE_KEY_PATH=~/.trading_keys/kalshi_private_key.pem
KALSHI_API_BASE=https://api.elections.kalshi.com/trade-api/v2
```

## Usage

### Switch to Enhanced Version
1. **Test the enhanced version**:
   ```bash
   cd C:\Users\yakub\Desktop\kalshi-mcp
   python kalshi_mcp_enhanced.py
   ```

2. **Update your MCP configuration** in `.mcp.json`:
   ```json
   {
     "mcpServers": {
       "kalshi": {
         "command": "python",
         "args": ["C:\\Users\\yakub\\Desktop\\kalshi-mcp\\kalshi_mcp_enhanced.py"],
         "env": {
           "KALSHI_API_KEY": "your_api_key",
           "KALSHI_PRIVATE_KEY_PATH": "~/.trading_keys/kalshi_private_key.pem",
           "KALSHI_MAX_RETRIES": "5"
         }
       }
     }
   }
   ```

3. **Monitor logs**:
   ```bash
   # View main log
   tail -f ~/.claude/logs/kalshi_mcp.log

   # View error log
   tail -f ~/.claude/logs/kalshi_mcp_errors.log
   ```

### New Health Check Tool
```python
# Test server health via MCP
health_check()  # Returns comprehensive status report
```

## Error Examples

### Before (Original):
```
Error searching markets: Request failed
```

### After (Enhanced):
```json
{
  "error": "Error in search_markets: ConnectionError: Unable to connect to server",
  "timestamp": "2026-03-12T15:30:45.123456",
  "context": {
    "function": "search_markets",
    "duration": "2.456s",
    "args": ["bitcoin", 20],
    "stack_trace": "Traceback (most recent call last)...",
    "retry_attempts": 3
  }
}
```

## Log Output Examples

### Console:
```
15:30:45 | INFO  | Searching markets for query: 'bitcoin', limit: 20
15:30:46 | ERROR | Request to /markets failed (attempt 1/4): ConnectionError: Connection refused. Retrying in 1.23s
15:30:47 | INFO  | Completed search_markets in 2.456s
```

### File (kalshi_mcp.log):
```
2026-03-12 15:30:45.123 | kalshi-mcp | INFO     | search_markets:125 | Searching markets for query: 'bitcoin', limit: 20
2026-03-12 15:30:46.456 | kalshi-mcp | WARNING  | _request:156 | Request to /markets failed (attempt 1/4): ConnectionError: Connection refused. Retrying in 1.23s
2026-03-12 15:30:47.789 | kalshi-mcp | DEBUG    | search_markets:140 | Retrieved 150 markets from API
2026-03-12 15:30:47.890 | kalshi-mcp | INFO     | search_markets:150 | Found 12 markets matching query 'bitcoin'
```

## Retry Behavior

1. **Connection Errors**: Immediate retry with exponential backoff
2. **Rate Limits (429)**: Enhanced backoff (was 0.5s, now configurable)
3. **Server Errors (500-504)**: New retry logic
4. **Timeouts**: Automatic retry with longer timeout on retry
5. **Authentication Errors**: No retry (fail fast)

## Performance Improvements

- **Connection pooling**: 20 max connections, 10 keep-alive
- **Request batching**: Multiple calls reuse connections
- **Timeout optimization**: Separate connect/read/write timeouts
- **Memory efficient**: Rotating logs prevent disk overflow

## Migration Notes

- **Backward compatible**: All existing tools work the same
- **Enhanced errors**: More detailed but same JSON structure
- **New logs directory**: `~/.claude/logs/` created automatically
- **Health check**: New tool for monitoring (optional)
- **Environment variables**: All optional, sensible defaults

The enhanced version maintains full compatibility while providing significantly better debugging capabilities and reliability.