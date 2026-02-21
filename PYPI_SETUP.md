# PyPI Account Setup & Publication

## Step 1: Create PyPI Account

1. Go to https://pypi.org/account/register/
2. Fill in:
   - Username: `yakub268` (or your preferred username)
   - Email: Your email address
   - Password: Strong password
3. Verify email address (check inbox)

## Step 2: Enable 2FA (Required for Publishing)

1. Go to https://pypi.org/manage/account/
2. Click "Add 2FA" under Security
3. Scan QR code with authenticator app (Google Authenticator, Authy, etc.)
4. Enter verification code

## Step 3: Create API Token

1. Go to https://pypi.org/manage/account/token/
2. Click "Add API token"
3. Fill in:
   - **Token name**: `kalshi-mcp-publish`
   - **Scope**: "Project: kalshi-mcp" (create after first upload) OR "Entire account" (for now)
4. **COPY THE TOKEN** - it starts with `pypi-` and is shown only once
5. Save it somewhere safe

## Step 4: Configure Local Authentication

Paste your token when prompted, or create `~/.pypirc`:

```ini
[pypi]
username = __token__
password = pypi-YOUR_TOKEN_HERE
```

## Step 5: Upload Package (Run This When Ready)

```bash
cd C:/Users/yakub/Desktop/kalshi-mcp
python -m twine upload dist/*
```

**First time only**: You'll be prompted for username/password:
- Username: `__token__`
- Password: `pypi-...` (paste your token)

---

## Quick Start URLs

- Register: https://pypi.org/account/register/
- Account settings: https://pypi.org/manage/account/
- Create token: https://pypi.org/manage/account/token/

**Estimated time**: 5-10 minutes
