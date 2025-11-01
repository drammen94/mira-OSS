# ADR: OSS Single-User Mode

**Status:** Approved
**Date:** 2025-10-04
**Decision Makers:** Taylor

---

## Context

MIRA is being prepared for open-source release. The current implementation includes a complete web interface (login, signup, chat UI, settings, memories viewer) and sophisticated multi-user authentication system (magic links, WebAuthn, sessions, CSRF protection, email integration).

For the OSS release, we want to prevent easy deployment of a competing hosted service while maintaining full programmatic functionality for developers and self-hosters.

## Decision

The OSS version will be **single-user, API-only** with the following characteristics:

### Authentication
- Single static API token generated on first startup
- Token saved to `data/.api_token` file
- All API requests require `Authorization: Bearer <token>` header
- No sessions, cookies, magic links, email verification, or WebAuthn
- No user signup or login endpoints

### User Management
- Exactly one user allowed in the database
- On first startup: auto-create user if none exists
- On subsequent startups: load and use existing single user
- Error and refuse to start if multiple users detected
- User context set globally at startup (not per-request)

### Interface
- Complete removal of web interface (HTML/CSS/JS frontend)
- All interactive endpoints removed (WebSocket chat, page routes)
- Programmatic API endpoints fully preserved:
  - POST /api/chat (primary endpoint)
  - GET/POST /api/data
  - POST /api/actions
  - GET /api/health
- Single introspection endpoint preserved: GET /auth/session

### Removed Components
- Web directory and all frontend assets (~3000 lines)
- WebSocket chat endpoint
- Email service (SendGrid integration)
- WebAuthn biometric authentication
- Magic link authentication flow
- Multi-user session management
- CSRF token system
- User signup/login/logout endpoints

## Rationale

### Moat Strategy
Removing the web interface and multi-user authentication creates a significant barrier to deploying MIRA as a competing SaaS:

1. **Frontend Development Burden:** Competitors must build entire chat UI, authentication flow, and user management interface from scratch (~3000+ lines of production-quality code)

2. **Authentication Complexity:** Multi-user systems require session management, email verification, security hardening, rate limiting, distributed locking - all removed

3. **Operational Complexity:** Running a multi-tenant service requires user isolation, data privacy controls, billing integration, support infrastructure - none of which OSS version supports

4. **Time-to-Market:** Estimated 4-8 weeks of full-time development to rebuild removed components to production quality

### Developer Experience
For legitimate self-hosting and development use cases:

1. **Simple Setup:** Single command startup, no email configuration, no authentication setup beyond saving one token
2. **Standard API Access:** Familiar Bearer token pattern, works with curl, Postman, SDKs
3. **Full Functionality:** Zero feature loss in core AI capabilities, tools, memory systems
4. **Easy Integration:** Programmatic API suitable for custom frontends, CLI tools, scripts, integrations

### Security Posture
Even in single-user mode, basic security maintained:

1. **Authentication Required:** Static token prevents accidental open exposure
2. **Token Protection:** Saved to file (not logged), rotatable by deleting and restarting
3. **Standard Pattern:** Industry-familiar Bearer token approach
4. **Minimal Attack Surface:** Removed endpoints eliminate entire classes of vulnerabilities (CSRF, session fixation, email enumeration, timing attacks on login)

## Consequences

### Positive
- Strong moat against competitive deployment
- Simplified codebase (remove ~4000 lines including web assets)
- Reduced dependencies (no SendGrid, WebAuthn, SSE libraries)
- Lower operational complexity for self-hosters
- Standard programmatic access patterns
- Preserved full AI/memory/tool capabilities

### Negative
- No built-in UI for OSS users (must build their own or use API directly)
- Single user limit may require workarounds for family/team self-hosting
- Database schema remains multi-user capable (slight inefficiency)
- Some existing documentation assumes web interface exists

### Neutral
- Requires fork maintenance if we add features to commercial web version
- OSS users can build custom frontends (web, CLI, mobile, etc.)
- Token reset requires file deletion + restart (acceptable for single-user scenario)

---

# Blueprint: OSS Single-User Implementation

This blueprint provides step-by-step instructions for converting MIRA to single-user, API-only mode.

## Prerequisites

- Familiarity with MIRA codebase structure
- Understanding of FastAPI routing and dependency injection
- Knowledge of Python contextvars for user context management
- Access to test environment for validation

---

## Phase 1: Authentication System Modification

### Task 1.1: Implement Static Token Generation

**File:** `main.py` - modify `lifespan()` function

**Location:** After orchestrator initialization (around line 76), before "MIRA startup complete" log

**Instructions:**
1. Add token generation/loading logic after all service initialization
2. Check if `data/.api_token` file exists
3. If NOT exists:
   - Generate cryptographically secure token using `secrets.token_urlsafe(32)`
   - Create `data/` directory if needed (use `Path.mkdir(parents=True, exist_ok=True)`)
   - Write token to file
   - Log prominent warning message showing token (user must save this)
   - Include file path in warning message
4. If exists:
   - Read token from file
   - Strip whitespace
   - Log info message confirming token loaded
5. Store token in `app.state.api_token` for later validation access

**Edge Cases:**
- Handle file read/write errors gracefully (fail fast with clear message)
- Ensure file has restrictive permissions (600) after creation
- Validate token is not empty after reading

### Task 1.2: Implement Single-User Startup Logic

**File:** `main.py` - modify `lifespan()` function

**Location:** After token generation, before "MIRA startup complete" log

**Instructions:**
1. Import `AuthDatabase` from `auth.database`
2. Create instance of `AuthDatabase`
3. Use session manager to query users table: `SELECT id, email FROM users LIMIT 2`
4. Branch on result count:
   - **Zero users:**
     - Log "Creating default single user"
     - Call `auth_db.create_user("user@localhost")`
     - Store returned user_id
     - Call `auth_db.prepopulate_new_user(user_id, "user@localhost")`
     - Log success with user_id
   - **One user:**
     - Log "Single-user mode: Using existing user {email}"
     - Extract user_id and email from result
   - **Multiple users:**
     - Raise `RuntimeError` with clear message explaining single-user limitation
     - Include user count in error
     - Suggest remediation (remove extra users or use multi-user version)
5. After user resolution (whether created or loaded):
   - Import `set_current_user_id` and `set_current_user_data` from `utils.user_context`
   - Call `set_current_user_id(user_id)` to set global context
   - Call `set_current_user_data({"user_id": user_id, "email": email})` for full context

**Edge Cases:**
- Database connection failures should propagate (fail startup)
- Prepopulation script failures should be logged but not block startup
- User context must be set even if prepopulation fails

### Task 1.3: Simplify Auth Dependency

**File:** `auth/api.py`

**Function:** `get_current_user()` (lines 132-202)

**Instructions:**
1. Replace entire function implementation (keep signature with Request and credentials dependencies)
2. New logic:
   - Check if credentials present and not None
   - If missing: raise HTTPException(401) with message "Authorization header required. Use: 'Authorization: Bearer YOUR_TOKEN'"
   - Extract token from `credentials.credentials`
   - Access FastAPI app state to get stored token (you'll need to access this via request.app.state)
   - Compare provided token with stored token (use constant-time comparison for security)
   - If mismatch: raise HTTPException(401, "Invalid API token")
   - If match: import and call `get_current_user()` from `utils.user_context`
   - Return dict with user_id and email from context
3. Remove all logic related to:
   - Cookie-based authentication
   - CSRF validation
   - Session extension/TTL
   - Token source detection (header vs cookie)

**Security Note:**
Use `secrets.compare_digest()` for token comparison to prevent timing attacks

### Task 1.4: Remove Auth Endpoints

**File:** `auth/api.py`

**Endpoints to DELETE entirely (remove function and decorator):**
- `signup()` - line 309
- `request_magic_link()` - line 351
- `verify_magic_link()` - line 393
- `logout()` - line 447
- `logout_all_devices()` - line 485
- `get_csrf_token()` - line 535
- All WebAuthn endpoints (lines 598-829):
  - `webauthn_register_begin()`
  - `webauthn_register_complete()`
  - `webauthn_login_begin()`
  - `webauthn_login_complete()`
  - `webauthn_remove_credential()`
  - `webauthn_list_credentials()`

**Endpoints to KEEP:**
- `get_session()` - line 524 (useful for token introspection)

**Optional endpoints (decide based on use case):**
- API token management endpoints (lines 217-305) - keep if you want users to create additional tokens, remove if single static token is sufficient

**Instructions:**
1. Delete endpoint functions listed above
2. Delete associated request/response Pydantic models if not used elsewhere
3. Delete helper function `get_webauthn_service()` (line 593)
4. Remove imports that are only used by deleted endpoints (SendGrid, WebAuthn)

---

## Phase 2: Web Interface Removal

### Task 2.1: Remove Web Routes

**File:** `main.py` - function `create_app()`

**Lines to DELETE:** 269-328

**Instructions:**
1. Remove all protected page route handlers:
   - `/chat` and `/chat/` (lines 273-277)
   - `/settings` and `/settings/` (lines 279-283)
   - `/memories` and `/memories/` (lines 285-289)
2. Remove conditional block `if Path("web").exists():` and entire contents (lines 292-328):
   - Root route `/` handler
   - `/login` routes
   - `/signup` routes
   - `/verify-magic-link` routes
   - Browser asset routes (apple-touch-icon, favicon, manifest)
   - Static file mounting for `/assets`
3. Remove import: `get_current_user_for_pages` (line 270)
4. Remove import: `Depends` from fastapi (if not used elsewhere in file)

**Note:** Ensure no orphaned variables or imports remain

### Task 2.2: Remove WebSocket Router

**File:** `main.py` - function `create_app()`

**Line to DELETE:** 258

**Instructions:**
1. Remove: `app.include_router(websocket_chat.router, tags=["websocket"])`
2. Remove import at top: `from cns.api import websocket_chat` (line 27)

### Task 2.3: Delete Web Interface Files

**Directory to DELETE:** `web/`

**Instructions:**
1. Delete entire directory and all contents
2. This removes approximately:
   - 7 HTML pages (~2900 lines)
   - CSS and JavaScript assets
   - Images and icons
   - Blog section
3. Optionally keep minimal files if you want basic favicon support:
   - Can manually copy `favicon.ico` to project root if needed
   - Not required for API-only functionality

---

## Phase 3: Service Layer Cleanup

### Task 3.1: Delete Email Service

**File to DELETE:** `auth/email_service.py`

**Instructions:**
1. Delete entire file
2. Search codebase for imports of `EmailService`:
   - Should only be in `auth/service.py` as lazy-loaded property
3. In `auth/service.py`:
   - Remove `email_service` property (lines 39-50)
   - Remove `email_service.setter` (lines 47-50)
   - Remove lazy import in property getter
   - Any calls to `self.email_service.send_magic_link()` are in removed endpoints, should already be gone

### Task 3.2: Delete WebAuthn Service

**File to DELETE:** `auth/webauthn_service.py`

**Instructions:**
1. Delete entire file
2. Verify no remaining imports in codebase (should only have been in deleted `auth/api.py` endpoints)

### Task 3.3: Delete WebSocket Chat Endpoint

**File to DELETE:** `cns/api/websocket_chat.py`

**Instructions:**
1. Delete entire file (~600 lines)
2. Verify websocket imports removed from `main.py` (already done in Phase 2)

---

## Phase 4: Dependency Cleanup

### Task 4.1: Update Requirements File

**File:** `requirements.txt`

**Dependencies to REMOVE:**
1. `sendgrid` - Email service integration
2. `webauthn` - Biometric authentication
3. `sse-starlette` - Server-sent events (only used by removed websocket)

**Instructions:**
1. Remove these three lines from requirements.txt
2. After implementation complete, run in clean virtual environment:
   - `pip install -r requirements.txt`
   - Verify no import errors on startup
   - Confirm removed packages not inadvertently required by other dependencies

**Dependencies to KEEP:**
- `fastapi`, `starlette` - Core framework (required)
- `hypercorn` - HTTP/2 server (required for streaming)
- All AI/ML dependencies (anthropic, openai, torch, transformers, etc.)
- Database dependencies (psycopg2, pgvector)
- All tool dependencies (caldav, googlemaps, kasa, etc.)

---

## Phase 5: Optional Database Schema Updates

### Task 5.1: Schema Cleanup (Optional)

**Files:** `docs/mira_service_schema.sql`, `scripts/prepopulate_new_user.sql`

**Instructions:**

**Option A: Leave schema as-is (RECOMMENDED)**
- Simplest approach
- Maintains compatibility if ever need to restore multi-user
- Unused tables/columns have minimal performance impact
- No migration required for existing installations

**Option B: Clean unused components**

If choosing Option B, modify schema:

1. **docs/mira_service_schema.sql:**
   - Drop `magic_links` table definition
   - Drop `sessions` table definition (unless keeping API token endpoints)
   - Remove `webauthn_credentials` column from `users` table
   - Keep `users` table with: id, email, is_active, created_at, last_login_at, timezone
   - Keep all other tables (conversations, messages, user_credentials, etc.)

2. **For existing installations:**
   - Create migration script to drop these tables/columns
   - Ensure no foreign key constraints break
   - Backup data before running

3. **scripts/prepopulate_new_user.sql:**
   - No changes needed (does not reference removed tables)

**Recommendation:** Start with Option A. Only pursue Option B if storage optimization becomes priority.

---

## Phase 6: Testing & Validation

### Task 6.1: Startup Testing

**Test Case 1: Fresh Installation (No Users)**

1. Start with empty database (or drop users table data)
2. Run `python main.py`
3. Verify logs show:
   - "FIRST TIME SETUP - Your API Token..."
   - Token displayed clearly
   - "Created default user: {user_id}"
   - "MIRA startup complete"
4. Verify `data/.api_token` file created
5. Verify file contains token matching log output
6. Verify database has exactly one user with email "user@localhost"

**Test Case 2: Existing Single User**

1. Ensure database has exactly one user
2. Ensure `data/.api_token` exists
3. Run `python main.py`
4. Verify logs show:
   - "Loaded API token from data/.api_token"
   - "Single-user mode: Using existing user {email}"
   - "MIRA startup complete"
5. Verify no errors or warnings

**Test Case 3: Multiple Users (Error Condition)**

1. Manually insert second user into database
2. Run `python main.py`
3. Verify:
   - Startup fails with RuntimeError
   - Error message clearly explains single-user limitation
   - Suggests remediation

### Task 6.2: API Authentication Testing

**Test Case 4: Valid Token**

1. Start MIRA and capture API token
2. Make request:
   ```
   POST http://localhost:1993/v0/api/chat
   Header: Authorization: Bearer {valid_token}
   Header: Content-Type: application/json
   Body: {"message": "Hello MIRA"}
   ```
3. Verify:
   - 200 OK response
   - Valid JSON response with continuum_id and response text
   - No authentication errors

**Test Case 5: Missing Token**

1. Make request without Authorization header
2. Verify:
   - 401 Unauthorized response
   - Error message mentions missing Authorization header
   - Includes usage hint about Bearer token format

**Test Case 6: Invalid Token**

1. Make request with wrong token:
   ```
   Header: Authorization: Bearer invalid_token_123
   ```
2. Verify:
   - 401 Unauthorized response
   - Error message indicates invalid token
   - No information leakage about valid token format

**Test Case 7: Token Rotation**

1. Note current token
2. Stop MIRA
3. Delete `data/.api_token` file
4. Start MIRA
5. Verify new token generated (different from previous)
6. Verify old token no longer works
7. Verify new token works

### Task 6.3: Endpoint Coverage Testing

**Test Case 8: Verify Removed Endpoints Return 404**

Test that these endpoints no longer exist:
- POST /auth/signup
- POST /auth/magic-link
- POST /auth/verify
- POST /auth/logout
- POST /auth/logout-all
- POST /auth/csrf
- GET /
- GET /chat
- GET /login
- GET /signup
- GET /settings
- GET /memories
- All /auth/webauthn/* endpoints
- WebSocket endpoint (attempt WebSocket connection)

For each:
1. Make request with valid token
2. Verify 404 Not Found response

**Test Case 9: Verify Kept Endpoints Work**

Test these endpoints still function:
- GET /api/health (should work without token based on current implementation)
- GET /auth/session (with valid token)
- POST /api/chat (with valid token)
- GET /api/data (with valid token)
- POST /api/actions (with valid token)

For each:
1. Make appropriate request with valid token
2. Verify expected response
3. Verify functionality preserved

### Task 6.4: User Context Testing

**Test Case 10: Verify Global User Context**

1. Start MIRA with single user
2. Make API request that uses tools (e.g., reminder creation)
3. Verify in logs that:
   - User context is available to tool execution
   - No "user context not set" errors
   - Tool data stored with correct user_id
4. Query database to confirm:
   - Tool data (e.g., reminder files) stored in correct user directory
   - No cross-user contamination possible

### Task 6.5: Edge Case Testing

**Test Case 11: File System Permissions**

1. Verify `data/.api_token` file permissions are restrictive (600 on Unix systems)
2. Test MIRA behavior if file becomes unreadable (should fail startup with clear error)
3. Test MIRA behavior if `data/` directory doesn't exist (should create it)

**Test Case 12: Concurrent Requests**

1. Send multiple simultaneous requests to /api/chat
2. Verify distributed request lock still works (only one processes at a time)
3. Verify queued requests wait and process successfully
4. Verify no race conditions or context corruption

---

## Phase 7: Documentation Updates

### Task 7.1: Update README

**File:** `README.md` (create if doesn't exist)

**Required Sections:**

1. **Installation:**
   - Prerequisites (Python version, PostgreSQL, Redis/Valkey)
   - Dependency installation
   - Database setup
   - Initial startup instructions

2. **Authentication:**
   - Explain single static token model
   - Show where to find token on first startup
   - Explain token file location
   - How to rotate token (delete file + restart)

3. **API Usage:**
   - Show curl example with Bearer token
   - Document primary endpoint: POST /api/chat
   - Link to full API documentation
   - Example request/response

4. **Configuration:**
   - Environment variables
   - Vault setup for API keys
   - Tool configuration

5. **Limitations:**
   - Single user only
   - No web interface included
   - Must build custom frontend or use programmatically

### Task 7.2: Create API Documentation

**File:** `docs/API_REFERENCE.md` (create)

**Required Content:**

1. Authentication header format
2. Complete endpoint listing:
   - POST /api/chat (primary)
   - GET /api/health
   - GET /auth/session
   - GET/POST /api/data
   - POST /api/actions
3. Request/response schemas for each endpoint
4. Error response formats
5. Rate limiting behavior (if applicable)

### Task 7.3: Update Development Docs

**File:** `CLAUDE.md` (existing)

**Updates Required:**

1. Remove references to web interface development
2. Update authentication section to reflect token-based model
3. Remove references to:
   - Magic link flow
   - Email service
   - WebAuthn
   - Cookie/session management
   - CSRF tokens
4. Add note about single-user constraint
5. Update testing instructions to use API token

---

## Phase 8: Final Validation

### Task 8.1: Complete System Test

**Comprehensive Validation:**

1. Fresh install on clean system:
   - Clone repository
   - Setup database
   - Install dependencies
   - Run first startup
   - Capture and test API token
   - Execute sample conversation via API
   - Verify all expected functionality works

2. Security audit:
   - Verify no endpoints accessible without token
   - Verify token not leaked in logs
   - Verify token file has secure permissions
   - Verify no web interface accessible
   - Verify removed endpoints truly removed

3. Performance check:
   - Verify startup time acceptable
   - Verify API response times unchanged
   - Verify memory usage reasonable
   - Verify global user context doesn't cause issues

4. Documentation review:
   - Follow README from scratch
   - Verify all instructions accurate
   - Verify no references to removed features
   - Verify API documentation matches implementation

### Task 8.2: Create Migration Guide

**File:** `docs/MIGRATION_TO_OSS.md` (create)

**For existing MIRA users who want to migrate to OSS version:**

1. Backup instructions (database, user data)
2. How to export single user's data if coming from multi-user
3. How to preserve API credentials during transition
4. Step-by-step migration procedure
5. Rollback procedure if issues arise
6. FAQ for common migration issues

---

## Success Criteria

The implementation is complete when:

- [ ] MIRA starts successfully with zero or one user in database
- [ ] Static API token generated and saved on first startup
- [ ] All API endpoints require valid Bearer token
- [ ] Invalid/missing tokens return 401 with helpful message
- [ ] All web routes and HTML pages removed
- [ ] WebSocket endpoint removed
- [ ] Email service removed (no SendGrid dependency)
- [ ] WebAuthn service removed
- [ ] All removed dependencies uninstalled
- [ ] Full conversation flow works via POST /api/chat
- [ ] All tools function correctly with global user context
- [ ] Memory systems work (working memory, long-term memory, surfacing)
- [ ] No "user context not set" errors occur
- [ ] Database schema enforces single user on startup
- [ ] Token rotation works (delete file, restart, new token)
- [ ] Documentation complete and accurate
- [ ] All test cases pass

---

## Rollback Plan

If issues arise during implementation:

1. **Immediate rollback:** Git revert all changes
2. **Partial rollback:** Keep auth changes, restore web interface temporarily
3. **Data safety:** All changes are code-only; no data loss risk
4. **Testing:** Test rollback in staging before production

---

## Implementation Sequence

Follow phases in order:

1. **Phase 1** - Authentication (most critical, foundational)
2. **Phase 2** - Web interface removal (visible changes)
3. **Phase 3** - Service cleanup (dependency reduction)
4. **Phase 4** - Dependencies (external cleanup)
5. **Phase 5** - Database (optional optimization)
6. **Phase 6** - Testing (validation)
7. **Phase 7** - Documentation (communication)
8. **Phase 8** - Final validation (ship readiness)

Estimate: 2-3 days for experienced developer familiar with codebase.

---

## Support & Questions

For implementation questions:
- Reference this blueprint
- Check existing MIRA architecture docs
- Review git history for context on removed components
- Test each phase thoroughly before proceeding to next
