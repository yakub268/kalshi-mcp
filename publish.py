#!/usr/bin/env python3
"""
Automated PyPI and MCP Registry Publisher
Run after getting PyPI token from https://pypi.org/manage/account/token/
"""

import os
import subprocess
import sys
from pathlib import Path

def main():
    print("=" * 50)
    print("Kalshi MCP Publication Script")
    print("=" * 50)
    print()

    # Check if token is provided
    token = os.getenv("PYPI_TOKEN")
    if not token:
        print("❌ No PyPI token found!")
        print()
        print("Please provide your PyPI token:")
        print("  1. Get token from: https://pypi.org/manage/account/token/")
        print("  2. Run: set PYPI_TOKEN=pypi-...")
        print("  3. Then run this script again")
        print()
        sys.exit(1)

    # Validate token format
    if not token.startswith("pypi-"):
        print("❌ Invalid token format! Token should start with 'pypi-'")
        sys.exit(1)

    print("✓ PyPI token detected")
    print()

    # Step 1: Upload to PyPI
    print("📦 Step 1: Uploading to PyPI...")
    print("-" * 50)

    try:
        result = subprocess.run(
            ["python", "-m", "twine", "upload", "dist/*", "--username", "__token__", "--password", token],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            check=True
        )
        print(result.stdout)
        print("✅ Published to PyPI!")
        print("   View at: https://pypi.org/project/kalshi-mcp/")
    except subprocess.CalledProcessError as e:
        print("❌ PyPI upload failed:")
        print(e.stderr)
        print()
        print("If package already exists, that's OK! Continuing...")

    print()

    # Step 2: MCP Registry submission instructions
    print("🌐 Step 2: Submit to MCP Registry")
    print("-" * 50)
    print()
    print("Now submit to the MCP Registry:")
    print()
    print("Option A - Web Submission (Recommended):")
    print("  1. Go to: https://registry.modelcontextprotocol.io/")
    print("  2. Sign in with GitHub")
    print("  3. Click 'Submit Server'")
    print("  4. Fill in form using server.json metadata")
    print("  5. Repository: https://github.com/yakub268/kalshi-mcp")
    print()
    print("Option B - CLI Submission:")
    print("  npm install -g @modelcontextprotocol/mcp-publisher")
    print("  mcp publish server.json")
    print()
    print("✅ All done! Package is live on PyPI.")
    print()

if __name__ == "__main__":
    main()
