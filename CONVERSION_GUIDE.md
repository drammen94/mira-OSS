# MIRA Closed-Source to OSS Conversion Guide

This guide documents how to convert the closed-source MIRA codebase to the single-user, API-only OSS version.

## Overview

The OSS version removes the multi-user authentication system and web interface while preserving full API functionality. This creates a strategic moat: competitors would need to rebuild ~12K lines of frontend code and complex auth infrastructure to offer a competing hosted service.

**What OSS Retains:**
- Full conversation API (`POST /v0/api/chat`)
- All tools and tool configuration
- Complete memory systems (working memory, long-term memory)
- Health and data endpoints

**What OSS Removes:**
- Web interface (chat UI, login, signup, settings, memories pages)
- Multi-user authentication (magic links, WebAuthn, sessions, CSRF)
- WebSocket real-time chat endpoint
- Email service integration

## Architecture Changes

### Authentication: Multi-User → Single-User

**Before (Closed Source):**
- 14 auth files (~2500 lines)
- Magic link email authentication
- WebAuthn biometric support
- Valkey session management
- Per-request user validation
- Rate limiting, security logging

**After (OSS):**
- 2 auth files (~50 lines)
- Static Bearer token stored in Vault
- Single user created on first startup
- User context set once at startup
- No sessions, cookies, or CSRF

### Interface: Web UI → API-Only

**Removed:**
- `web/` directory (94 files, ~12K lines)
- HTML pages: chat, login, signup, settings, memories
- JavaScript: api-client, messaging, history, UI components
- CSS, fonts, images, icons
- Blog system

**Preserved:**
- All `/v0/api/*` endpoints
- Tool configuration API
- Health checks

## Using the Conversion Script

### Prerequisites

- Bash shell
- Python 3
- Target directory must be a valid MIRA codebase (has `main.py` and `cns/` directory)

### Usage

```bash
# Copy the script into the closed-source directory
cp makeoss.sh /path/to/botwithmemory/

# Change to that directory and run
cd /path/to/botwithmemory/
./makeoss.sh
```

The script operates on the current working directory and **deletes itself** when complete.

### What the Script Does

1. **Deletes directories:** `web/`, `docs/`, `junk_drawer/`, `data/users/`
2. **Replaces auth module:** Removes all files, creates minimal Bearer token validator
3. **Cleans scripts/:** Keeps only `setup_vault.sh`
4. **Removes WebSocket:** Deletes `cns/api/websocket_chat.py`
5. **Patches main.py:** Removes web routes, adds single-user startup
6. **Updates requirements.txt:** Removes `sendgrid`, `webauthn`, `sse-starlette`
7. **Self-destructs:** Removes the conversion script

### Verification

After conversion:

```bash
# Check for import errors
python -c "import main"

# Start the server (will create user and display API key on first run)
python main.py

# Test health endpoint
curl -H "Authorization: Bearer YOUR_API_KEY" http://localhost:1993/v0/api/health

# Verify web routes are gone (should return 404)
curl http://localhost:1993/chat
```

## Manual Conversion Reference

For understanding what changes are made, here's the detailed breakdown:

### Files Deleted

**Entire Directories:**
- `web/` - Web interface
- `docs/` - Internal documentation
- `junk_drawer/` - Experimental code
- `data/users/` - User data (fresh start)

**Auth Module (all files):**
- `service.py` - Auth orchestrator
- `database.py` - Magic links, user creation
- `session.py` - Valkey sessions
- `email_service.py` - SendGrid integration
- `webauthn_service.py` - Biometric auth
- `rate_limiter.py` - Rate limiting
- `security_logger.py` - Security events
- `security_middleware.py` - HTTP headers
- `account_gc.py` - Account cleanup
- `types.py`, `config.py`, `exceptions.py`

**Other:**
- `cns/api/websocket_chat.py`
- All scripts except `setup_vault.sh`

### Files Created

**auth/__init__.py:**
```python
"""Single-user authentication module."""
from auth.api import get_current_user
__all__ = ["get_current_user"]
```

**auth/api.py:**
- Bearer token validation against `app.state.api_key`
- Sets user context via `set_current_user_id()` and `set_current_user_data()`

### main.py Modifications

**Imports removed:**
- `from fastapi.staticfiles import StaticFiles`
- `from auth.security_middleware import SecurityHeadersMiddleware`
- `import auth.api as auth`
- Thread monitoring imports

**Imports modified:**
- Remove `FileResponse` from fastapi.responses
- Remove `websocket_chat` from cns.api imports

**Functions added:**
- `ensure_single_user(app)` - Creates user on first startup, loads credentials

**Startup changes:**
- Remove thread monitoring initialization
- Add `ensure_single_user(app)` call

**Shutdown changes:**
- Remove `close_all_connections()` WebSocket cleanup

**Routes removed:**
- `app.include_router(auth.router, ...)`
- `app.include_router(websocket_chat.router, ...)`
- All page routes (`/chat`, `/settings`, `/memories`, `/login`, `/signup`, etc.)
- Static file mount (`/assets`)

**Middleware removed:**
- `SecurityHeadersMiddleware`

## Post-Conversion Notes

### First Startup

On first startup with an empty database, MIRA will:
1. Create a user with email `user@localhost`
2. Generate an API key (format: `mira_<random>`)
3. Store the key in Vault at `mira/api_keys/mira_api`
4. Display the key in the console (only shown once)

### API Key Rotation

To rotate the API key:
1. Delete the key from Vault: `vault kv delete secret/mira/api_keys`
2. Delete the user from database
3. Restart MIRA (new user and key will be created)

### Multiple Users Error

If the database somehow has multiple users, MIRA will refuse to start with an error message. This is intentional - OSS is single-user only.
