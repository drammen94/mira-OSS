# MIRA Single-User API Mode - Implementation Guide

## Executive Summary

Convert MIRA to single-user API-only service. After these changes, MIRA operates exclusively in single-user mode with bearer token authentication.

**Core Strategy:**
- Add single-user validation at startup
- Auto-create user if none exists
- Modify auth to auto-inject the single user
- Delete web interface while preserving useful auth infrastructure

**Time Estimate:** 2-3 hours
**Risk Level:** Low - mostly deletions, minimal logic changes

## Prerequisites

Before implementing single-user mode, ensure HashiCorp Vault is configured:

```bash
# Run the Vault setup script
bash scripts/setup_vault.sh
```

This will:
- Initialize Vault with persistent file storage
- Configure AppRole authentication
- Prompt for required API keys (Anthropic, OpenRouter)
- Optionally prompt for Kagi and Letta keys
- Set up default database and Valkey credentials
- Generate `.vault_keys` file with all credentials

**Note**: The script is idempotent - safe to run multiple times.

## Architecture Overview

### Key Insight
MIRA already uses context-based user isolation throughout. In single-user mode:
- Request arrives with bearer token → Validate token → Inject single user's context → Everything works
- Background tasks reference the single user ID from app state
- No changes needed to tools, memory systems, or core logic

### What Changes
1. **Startup:** Check for exactly one user, display bearer token
2. **Auth:** Auto-inject single user when valid bearer token provided
3. **Deleted:** Web interface, session management, complex auth flows

### What Stays the Same
- User context propagation (still works perfectly)
- Database schema (including users table)
- All API endpoints
- All tools and memory systems
- Background tasks (they just process one user)

## Implementation Steps

### Step 1: Add Single-User Startup Check

**File:** `main.py`

Add this function before the `lifespan` context manager (around line 40):

```python
def ensure_single_user(app: FastAPI) -> None:
    """Ensure exactly one user exists and set up bearer token."""
    from auth.database import AuthDatabase
    from auth.service import AuthService
    from utils.user_context import set_current_user_id, set_current_user_data

    auth_db = AuthDatabase()
    auth_service = AuthService()

    # Count users
    with auth_db.session_manager.get_admin_session() as session:
        result = session.execute_single("SELECT COUNT(*) as count FROM users")
        user_count = result['count']

        if user_count == 0:
            print("\n" + "="*60)
            print("🚀 MIRA Single-User Setup")
            print("="*60)
            print("No user found. Creating default user...")

            # Create user using existing auth infrastructure
            default_email = "user@localhost"
            user_id = auth_db.create_user(default_email)
            print(f"✅ Created user: {default_email}")

            # Set context so we can create API token
            set_current_user_id(user_id)
            set_current_user_data({"user_id": user_id})

            # Create API token using existing method
            token_name = "MIRA Single-User Token"
            token = auth_service.create_api_token(token_name, expires_in_days=36500)  # 100 years

            # Store user ID
            app.state.single_user_id = str(user_id)

            # Display credentials
            print("\n" + "="*60)
            print("✅ MIRA Ready - Single-User API Mode")
            print("="*60)
            print(f"User: {default_email}")
            print(f"Bearer Token: {token}")
            print("\nExample usage:")
            print(f'  curl -H "Authorization: Bearer {token}" \\')
            print('    -X POST http://localhost:4201/v0/api/chat \\')
            print('    -H "Content-Type: application/json" \\')
            print('    -d \'{"message": "Hello MIRA"}\'')
            print("="*60 + "\n")
            return

        elif user_count > 1:
            print(f"\n❌ ERROR: Found {user_count} users")
            print("MIRA now operates in single-user mode only.")
            print("Please keep only one user in the database.")
            sys.exit(1)

        # Get the single user
        user = session.execute_single("SELECT id, email FROM users LIMIT 1")
        app.state.single_user_id = str(user['id'])

        # Check for existing API tokens using valkey index
        from clients.valkey_client import get_valkey
        valkey = get_valkey()
        token_index_key = f"api_tokens_index:{user['id']}"
        existing_tokens = valkey.hgetall(token_index_key)

        if existing_tokens:
            # Get first valid token
            for token_id, token_data in existing_tokens.items():
                import json
                data = json.loads(token_data)
                if data.get('revoked_at') is None:
                    # Found active token, retrieve the actual session
                    session_key = f"session:{data['token']}"
                    if valkey.exists(session_key):
                        print(f"\n✅ MIRA Ready - Single-User API Mode")
                        print(f"User: {user['email']}")
                        print(f"Bearer Token: {data['token']}")
                        return

        # No valid token found, create new one
        set_current_user_id(user['id'])
        set_current_user_data({"user_id": user['id']})
        token = auth_service.create_api_token("MIRA Single-User Token", expires_in_days=36500)

        # Display credentials
        print("\n" + "="*60)
        print("✅ MIRA Ready - Single-User API Mode")
        print("="*60)
        print(f"User: {user['email']}")
        print(f"Bearer Token: {token}")
        print("\nExample usage:")
        print(f'  curl -H "Authorization: Bearer {token}" \\')
        print('    -X POST http://localhost:4201/v0/api/chat \\')
        print('    -H "Content-Type: application/json" \\')
        print('    -d \'{"message": "Hello MIRA"}\'')
        print("="*60 + "\n")
```

In the `lifespan` function, add this line after logging setup (around line 50):

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Initialize logging
    logger.info("  Starting MIRA...\n\n\n")
    logger.info("====================")

    # Add this line - validate single user
    ensure_single_user(app)

    # Rest remains unchanged
    # Configure FastAPI thread pool
    from anyio import to_thread
    to_thread.current_default_thread_limiter().total_tokens = 100
    # ...
```

### Step 2: Modify Authentication

**File:** `auth/api.py`

In the `get_current_user` function (around line 140), add this check at the very beginning:

```python
async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    auth_service: AuthService = Depends(get_auth_service)
) -> dict:
    """Get current user from session or API token."""
    try:
        # ===== ADD THIS BLOCK FOR SINGLE-USER MODE =====
        if hasattr(request.app.state, 'single_user_id') and credentials and credentials.credentials:
            # Single-user mode: validate token and inject user
            from utils.database_session_manager import get_shared_session_manager

            session_manager = get_shared_session_manager()
            with session_manager.get_admin_session() as session:
                token_result = session.execute_single(
                    "SELECT 1 FROM api_tokens WHERE token = %(token)s AND is_active = true",
                    {'token': credentials.credentials}
                )

            if token_result:
                # Valid token - inject the single user
                user_id = request.app.state.single_user_id
                session_data = {
                    "user_id": user_id,
                    "session_id": f"api-{user_id}",
                    "is_api_token": True
                }

                # Set context for downstream code
                set_current_user_id(user_id)
                set_current_user_data(session_data)

                return session_data
        # ===== END SINGLE-USER BLOCK =====

        # Original auth flow continues unchanged below...
        # Prefer Authorization header when provided
        if credentials and credentials.credentials:
            token = credentials.credentials
            # ... rest of existing code ...
```

### Step 3: Remove Web Interface

**From `main.py`, delete these sections:**

1. **Imports** (lines ~16-30):
```python
# DELETE THESE LINES:
from fastapi.staticfiles import StaticFiles
from cns.api import websocket_chat
from auth.api import get_current_user_for_pages
```

2. **All web routes** (lines ~346-394):
```python
# DELETE ALL OF THESE:
@app.get("/chat")
@app.get("/chat/")
@app.get("/settings")
@app.get("/settings/")
@app.get("/memories")
@app.get("/memories/")
@app.get("/")
@app.get("/login")
@app.get("/login/")
@app.get("/signup")
@app.get("/signup/")
@app.get("/verify-magic-link")
@app.get("/verify-magic-link/")
@app.get("/apple-touch-icon.png")
@app.get("/favicon.ico")
@app.get("/manifest.json")
```

3. **Router registrations** (around line 338):
```python
# DELETE THESE LINES:
app.include_router(websocket_chat.router, prefix="/v0", tags=["websocket"])
app.mount("/assets", StaticFiles(directory="web/assets"), name="assets")
```

**Delete these entire directories:**
```bash
rm -rf web/
rm -rf static/
rm -rf templates/
```

**Delete this file:**
```bash
rm cns/api/websocket_chat.py
```

### Step 4: Clean Up Imports in main.py

**File:** `main.py`

Remove security middleware import and registration:

```python
# Line 24 - DELETE THIS IMPORT:
from auth.security_middleware import SecurityHeadersMiddleware

# Line 323 (or similar) - DELETE THIS MIDDLEWARE REGISTRATION:
app.add_middleware(SecurityHeadersMiddleware)
```

### Step 5: Simplify Auth Module

**Keep these auth files (they contain useful infrastructure):**
```
auth/
├── __init__.py          # Keep - module initialization
├── api.py              # Keep - modified in Step 2, needs cleanup in Step 6
├── service.py          # Keep - AuthService with create_api_token, needs cleanup in Step 7
├── database.py         # Keep - AuthDatabase with create_user
├── session.py          # Keep - session management for tokens
├── exceptions.py       # Keep - error handling
├── distributed_lock.py # Keep - used by REST chat endpoint
├── user_credentials.py # Keep - used by MCP tools
├── types.py            # Keep - type definitions
├── config.py           # Keep - auth configuration
└── security_logger.py  # Keep - security event logging
```

**Delete these auth files:**
```bash
rm auth/webauthn_service.py     # Passkey authentication
rm auth/email_service.py        # Email magic links
rm auth/rate_limiter.py         # Magic link rate limiting
rm auth/security_middleware.py  # Web-specific security headers
```

### Step 6: Clean Up auth/api.py

**File:** `auth/api.py`

Remove WebAuthn import and dependency:

```python
# Line 14 - DELETE THIS IMPORT:
from .webauthn_service import WebAuthnService

# Lines 588-590 (or similar) - DELETE THIS FUNCTION:
def get_webauthn_service() -> WebAuthnService:
    """Dependency for WebAuthn service."""
    return WebAuthnService()
```

**Delete these route handlers** (keep the modified `get_current_user` dependency from Step 2):

Remove all these endpoint functions:
- `@router.post("/signup")` - User registration (lines ~308-350)
- `@router.post("/magic-link")` - Magic link request (lines ~350-392)
- `@router.post("/verify")` - Magic link verification (lines ~392-446)
- `@router.post("/logout")` - Session logout (lines ~446-493)
- `@router.post("/logout-all")` - Logout all sessions (lines ~493-519)
- `@router.get("/session")` - Current session info (lines ~519-530)
- `@router.post("/csrf")` - CSRF token generation (lines ~530-588)
- `@router.post("/webauthn/register/begin")` - WebAuthn registration start (lines ~593-622)
- `@router.post("/webauthn/register/complete")` - WebAuthn registration finish (lines ~622-655)
- `@router.post("/webauthn/login/begin")` - WebAuthn login start (lines ~655-691)
- `@router.post("/webauthn/login/complete")` - WebAuthn login finish (lines ~691-761)
- `@router.delete("/webauthn/credential/{credential_id}")` - Delete passkey (lines ~761-800)
- `@router.get("/webauthn/credentials")` - List passkeys (lines ~800+)

**Optional - Keep for token management:**
- `@router.post("/api-tokens")` - Create API token
- `@router.get("/api-tokens")` - List API tokens
- `@router.delete("/api-tokens/{token_id}")` - Revoke API token

### Step 7: Clean Up auth/service.py

**File:** `auth/service.py`

Remove unused service initialization from `__init__`:

```python
# In AuthService.__init__ (around lines 33-36), DELETE THESE LINES:
from .email_service import EmailService
from .rate_limiter import RateLimiter
self.email_service = EmailService()
self.rate_limiter = RateLimiter()

# The __init__ should look like:
def __init__(self):
    self.db = AuthDatabase()
    self.session_manager = SessionManager()
    self.security_logger = security_logger

    # Configurable timeouts (for testing)
    self.SESSION_IDLE_TIMEOUT = config.SESSION_IDLE_TIMEOUT
    self.SESSION_MAX_LIFETIME = config.SESSION_MAX_LIFETIME
```

**Delete these methods** (they use email_service/rate_limiter):

- `request_magic_link()` - Lines ~76-160
- `verify_magic_link()` - Lines ~161-235

**Keep these methods** (used by single-user mode):
- `create_user()` - Lines ~50-75
- `create_api_token()` - Lines ~236-280
- `validate_session()` - Used for token validation
- All other session management methods

### Step 8: Handle Background Tasks

**No changes required!** Background tasks will work automatically because:

1. They can reference `app.state.single_user_id` when needed
2. Methods that iterate "all users" will find exactly one user
3. Explicit user_id parameters continue working as before

**Example:** In `lt_memory/scheduled_tasks.py`, functions like:
```python
def run_consolidation_for_all_users():
    users = auth_db.get_users_with_memory_enabled()
    for user in users:  # Will only have one user
        batching.submit_consolidation_batch(user['id'])
```

These work perfectly in single-user mode without modification.

## Testing

### 1. First Start (No Users)
```bash
# Start MIRA with empty database
python main.py

# Expected output:
# "No user found. Creating default user..."
# "✅ Created user: user@localhost"
# "✅ MIRA Ready - Single-User API Mode"
# "User: user@localhost"
# "Bearer Token: mira_xxxxxxxxxxxxx"
```

### 2. Subsequent Starts
```bash
# Start MIRA again
python main.py

# Expected output:
# "✅ MIRA Ready - Single-User API Mode"
# "User: user@localhost"
# "Bearer Token: mira_xxxxxxxxxxxxx"  # Same token as before
```

### 3. Test API Access
```bash
# Test with valid token
curl -H "Authorization: Bearer mira_xxxxxxxxxxxxx" \
     -X POST http://localhost:4201/v0/api/chat \
     -H "Content-Type: application/json" \
     -d '{"message": "Hello MIRA"}'

# Test without token (should fail)
curl -X POST http://localhost:4201/v0/api/chat \
     -H "Content-Type: application/json" \
     -d '{"message": "Hello"}'
# Expected: 401 Unauthorized
```

### 4. Verify Background Tasks
Check logs to ensure scheduled tasks are running:
- Memory consolidation
- Entity garbage collection
- Temporal score updates

These should all process the single user automatically.

## Summary of Changes

### Code Modifications
1. **main.py**:
   - Add `ensure_single_user()` function (~90 lines)
   - Call it in lifespan
   - Remove SecurityHeadersMiddleware import and registration
   - Remove web route definitions
   - Remove WebSocket router registration

2. **auth/api.py**:
   - Add single-user check in `get_current_user()` (~25 lines)
   - Remove WebAuthn import and dependency
   - Delete web-specific endpoint handlers

3. **auth/service.py**:
   - Remove email_service and rate_limiter from `__init__`
   - Delete magic link methods

### File Deletions
**Directories:**
- `web/` - Entire web frontend
- `static/` - Static assets
- `templates/` - HTML templates

**Individual files:**
- `cns/api/websocket_chat.py` - WebSocket interface
- `auth/webauthn_service.py` - Passkey authentication
- `auth/email_service.py` - Email sending
- `auth/rate_limiter.py` - Rate limiting
- `auth/security_middleware.py` - Web security headers

### Files Preserved (Core Infrastructure)
**Auth module (11 files kept):**
- `api.py` - Request authentication (modified)
- `service.py` - AuthService with token creation (modified)
- `database.py` - User CRUD operations
- `session.py` - Session/token management
- `distributed_lock.py` - Request locking for chat
- `user_credentials.py` - Per-user secrets storage
- `exceptions.py` - Auth error types
- `types.py` - Type definitions
- `config.py` - Auth configuration
- `security_logger.py` - Security event logging
- `__init__.py` - Module init

### What Remains Unchanged
- Database schema
- User context system
- All tools
- All API endpoints
- Background tasks
- Memory systems

## Troubleshooting

### Import Errors After Deletion

**Error:** `ModuleNotFoundError: No module named 'auth.webauthn_service'`
- **Cause:** Forgot to remove import from auth/api.py
- **Fix:** Delete the import line from auth/api.py (Step 6)

**Error:** `ModuleNotFoundError: No module named 'auth.security_middleware'`
- **Cause:** Forgot to remove import from main.py
- **Fix:** Delete the import and middleware registration (Step 4)

**Error:** `AttributeError: 'AuthService' object has no attribute 'email_service'`
- **Cause:** Code is trying to call a deleted method
- **Fix:** Make sure you deleted the magic link methods from auth/service.py (Step 7)

### Runtime Errors

**"Multiple users found" error**
```sql
-- Check users
SELECT id, email FROM users;

-- Keep only desired user
DELETE FROM users WHERE id != 'desired-user-id';
```

**Token not working**
```sql
-- Check tokens in database
SELECT token, is_active FROM api_tokens WHERE user_id = 'your-user-id';

-- Ensure active
UPDATE api_tokens SET is_active = true WHERE token = 'your-token';

-- Check Valkey for session
# Use redis-cli or valkey-cli
KEYS session:*
GET session:mira_xxxxx
```

**Background tasks not running**
- Check `app.state.single_user_id` is set in startup logs
- Verify scheduler started in lifespan
- Check user has `memory_enabled = true` in database:
  ```sql
  SELECT id, email, memory_enabled FROM users;
  UPDATE users SET memory_enabled = true WHERE id = 'user-id';
  ```

### Startup Issues

**Server won't start**
1. Check for Python syntax errors: `python -m py_compile main.py`
2. Check for import errors: Look at traceback carefully
3. Verify all deleted files are actually deleted
4. Ensure kept files still exist

**User creation fails on first startup**
- Check database connection is working
- Verify `users` table exists
- Check logs for specific error message

## Next Steps

After implementation:
1. Update deployment configs to remove web-specific settings
2. Document the API endpoints and bearer token usage
3. Consider creating a simple CLI client
4. Remove any remaining web-specific configuration

## Implementation Checklist

Use this checklist to track progress:

**Step 1: Startup Check**
- [ ] Add `ensure_single_user()` function to main.py
- [ ] Call `ensure_single_user(app)` in lifespan function
- [ ] Test: Start MIRA with empty DB, verify user auto-creation

**Step 2: Auth Injection**
- [ ] Add single-user block to `get_current_user()` in auth/api.py
- [ ] Test: API call with bearer token works

**Step 3: Remove Web Interface**
- [ ] Delete web route imports from main.py
- [ ] Delete all web route handlers from main.py
- [ ] Remove websocket router registration from main.py
- [ ] Delete `web/`, `static/`, `templates/` directories
- [ ] Delete `cns/api/websocket_chat.py`
- [ ] Test: Server starts without errors

**Step 4: Clean Up main.py Imports**
- [ ] Remove SecurityHeadersMiddleware import
- [ ] Remove SecurityHeadersMiddleware registration
- [ ] Test: Server starts without errors

**Step 5-7: Clean Up Auth Module**
- [ ] Delete auth files: webauthn_service.py, email_service.py, rate_limiter.py, security_middleware.py
- [ ] Remove WebAuthn import from auth/api.py
- [ ] Delete WebAuthn dependency function from auth/api.py
- [ ] Delete web endpoint handlers from auth/api.py
- [ ] Remove email_service/rate_limiter from auth/service.py __init__
- [ ] Delete magic link methods from auth/service.py
- [ ] Test: Server starts, auth works

**Step 8: Verify**
- [ ] Test: Fresh startup creates user automatically
- [ ] Test: Bearer token authentication works
- [ ] Test: Chat API processes messages
- [ ] Test: Background tasks run for single user
- [ ] Check logs for any import errors

## Notes

- MIRA is now a single-user system - the `users` table will only ever have one user
- Context propagation works identically, just with one user context
- API tokens are managed through existing AuthService infrastructure
- Database operations remain synchronous (using psycopg2)
- User creation is automatic on first startup
- All core functionality (tools, memory, API endpoints) works unchanged

---

After these changes, MIRA operates exclusively in single-user mode. The architectural elegance is that 99% of the code continues working unchanged - it simply operates on one user instead of many.