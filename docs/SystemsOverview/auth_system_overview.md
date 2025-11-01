# Auth System Overview

## What is the Auth System?

MIRA's authentication system provides secure, lean magic-link authentication with complete user isolation. It manages user accounts, sessions, credentials, and implements Row Level Security (RLS) throughout the application. The system is designed for multi-tenant operation with strict data separation between users.

## Architecture Overview

### Core Components
```
auth/
├── service.py              # Main authentication service orchestrator
├── database.py             # Database operations for auth tables
├── session.py              # Valkey-based session management
├── user_credentials.py     # Per-user credential encryption
├── api.py                  # FastAPI endpoints
├── types.py                # Pydantic models and type definitions
├── email_service.py        # Email sending for magic links
├── webauthn_service.py     # WebAuthn/passkey support
├── rate_limiter.py         # Request rate limiting
├── security_logger.py      # Security event logging
├── security_middleware.py  # CORS and security headers
├── distributed_lock.py     # Distributed locking for operations
└── exceptions.py           # Auth-specific exceptions
```

## Core Concepts

### 1. **Magic Link Authentication**

The primary authentication method using email-based magic links:

**Flow**:
1. User requests magic link with email
2. System generates secure token and sends email
3. User clicks link with token
4. System validates token and creates session
5. Session token returned in secure cookie

**Security Features**:
- Cryptographically secure token generation (32 bytes)
- SHA-256 token hashing for storage
- Short expiry window (15 minutes default)
- One-time use enforcement
- Rate limiting per email address

### 2. **User Management** (`database.py`)

**User Record Structure**:
```python
class UserRecord:
    id: str                    # UUID
    email: str                 # Unique email
    tenant_id: Optional[str]   # Multi-tenant support
    is_active: bool           # Account status
    created_at: datetime      # Registration time
    last_login_at: Optional[datetime]
    webauthn_credentials: Dict  # Passkey data
    memory_consolidation_enabled: bool
    timezone: str             # User timezone
```

**Key Operations**:
- `create_user()`: Register new user with email validation
- `get_user_by_email()`: Lookup for authentication
- `update_user_login()`: Track last login
- `get_users_with_memory_enabled()`: For memory system

### 3. **Session Management** (`session.py`)

Valkey-based session storage with dual timeout model:

**Session Structure**:
```python
class SessionData:
    user_id: str
    email: str
    created_at: str      # ISO format
    last_activity: str   # ISO format
    max_expiry: str      # Absolute expiry
```

**Timeout Model**:
- **Idle Timeout**: 30 minutes of inactivity (extends on activity)
- **Max Lifetime**: 24 hours absolute limit
- Sessions stored in Valkey with automatic expiry
- CSRF tokens tied to session lifecycle

**Key Features**:
- Activity-based extension
- Bulk revocation support
- Scan-based user session discovery
- Automatic CSRF token cleanup

### 4. **User Isolation & RLS**

Complete data isolation at multiple levels:

**Database Level**:
- Auth tables use shared connection (no user data)
- User data tables use RLS-enabled connections
- Each database client automatically scopes to current user
- `SET app.current_user_id` on every connection

**Application Level**:
- User context stored in context variables
- `get_current_user_id()` available throughout request
- Tools and services automatically use user context
- No cross-user data access possible

**Storage Level**:
- User-specific directories: `/data/users/{user_id}/`
- Per-user SQLite databases for credentials
- Encrypted credential storage with user-specific keys

### 5. **Credential Management** (`user_credentials.py`)

Secure storage for user-specific credentials:

**Storage Architecture**:
- Each user has dedicated SQLite database
- Encryption at rest using user-specific key
- Automatic encryption/decryption on access
- Supports multiple credential types

**Credential Types**:
- API keys for external services
- Email passwords for IMAP/SMTP
- OAuth tokens
- Custom service credentials

**Usage Pattern**:
```python
# Store API key
store_api_key_for_current_user("openai", "sk-...")

# Retrieve API key
key = get_api_key_for_current_user("openai")

# Store complex config
store_email_config_for_current_user({
    "host": "smtp.gmail.com",
    "port": 587,
    "username": "user@gmail.com"
})
```

### 6. **API Endpoints** (`api.py`)

RESTful endpoints following standard response format:

**Endpoints**:
- `POST /v0/auth/signup`: Create new user account
- `POST /v0/auth/request-magic-link`: Request authentication link
- `POST /v0/auth/verify-magic-link`: Exchange token for session
- `POST /v0/auth/logout`: Revoke current session
- `POST /v0/auth/logout-all`: Revoke all user sessions
- `GET /v0/auth/me`: Get current user profile
- `GET /v0/auth/health`: Check auth service status

**Response Format**:
```json
{
  "success": true,
  "data": {...},
  "meta": {
    "request_id": "uuid",
    "timestamp": "2024-01-01T00:00:00Z",
    "http_status": 200
  }
}
```

**Error Handling**:
- Structured error responses
- Specific error codes (rate_limit_exceeded, invalid_token, etc.)
- Security-conscious error messages
- Request ID tracking

### 7. **Rate Limiting** (`rate_limiter.py`)

Protection against brute force and abuse:

**Implementation**:
- Valkey-based sliding window counter
- Per-email rate limiting for magic links
- 5 requests per 15-minute window (default)
- Exponential backoff on violations

**Features**:
- Returns time until next allowed request
- Configurable limits and windows
- Automatic cleanup of old entries
- Bypass for testing environments

### 8. **Security Features**

**Security Logging** (`security_logger.py`):
- Structured security event logging
- Events: login, logout, rate limits, token usage
- Includes IP address and user agent
- Separate log stream for audit trail

**Security Middleware** (`security_middleware.py`):
- CORS configuration
- Security headers (CSP, X-Frame-Options, etc.)
- Request/response logging
- Error sanitization

**Distributed Locks** (`distributed_lock.py`):
- Prevents race conditions in critical operations
- Valkey-based with automatic expiry
- Used for session creation, token validation

## Data Flow

### Authentication Flow
```
User enters email → Rate limit check → Generate magic link
    ↓
Send email → Store hashed token in database
    ↓
User clicks link → Validate token → Create session
    ↓
Set session cookie → Update last login → Return user profile
```

### Session Validation Flow
```
Request with session cookie → Extract token
    ↓
Lookup in Valkey → Check expiry times
    ↓
Extend activity timeout → Set user context
    ↓
Process request with user isolation
```

## Integration Points

### With Database Layer
- Auth service uses shared connection (no RLS)
- Sets user context for RLS-enabled connections
- Automatic user_id propagation to all queries

### With Tools System
- Tools access `get_current_user_id()` for context
- User-specific data directories created automatically
- Credential storage through UserCredentialService

### With Memory Systems
- User isolation at database level
- Memory consolidation flags in user record
- Timezone support for scheduled operations

### With Web Framework
- FastAPI dependency injection for auth
- Automatic session validation on protected routes
- Standard error response format

## Configuration

Key settings in `auth/config.py`:
- `MAGIC_LINK_EXPIRY`: Token validity (900 seconds)
- `SESSION_IDLE_TIMEOUT`: Inactivity limit (1800 seconds)
- `SESSION_MAX_LIFETIME`: Absolute session limit (86400 seconds)
- `MAX_REQUESTS_PER_WINDOW`: Rate limit threshold (5)
- `RATE_LIMIT_WINDOW`: Rate limit window (900 seconds)

## Security Considerations

1. **Token Security**:
   - Never store plain tokens
   - Use cryptographically secure generation
   - Single use enforcement
   - Short expiry windows

2. **Session Security**:
   - HTTPOnly, Secure, SameSite cookies
   - CSRF protection with double-submit
   - Activity tracking and timeouts
   - Bulk revocation capability

3. **Data Isolation**:
   - RLS at database level
   - User context validation
   - No shared state between users
   - Encrypted credential storage

4. **Rate Limiting**:
   - Prevents brute force attacks
   - Per-user limiting
   - Exponential backoff
   - Security event logging

## Benefits

1. **Simplicity**: Magic links eliminate password complexity
2. **Security**: No passwords to leak or crack
3. **User Experience**: Seamless authentication flow
4. **Isolation**: Complete user data separation
5. **Auditability**: Comprehensive security logging
6. **Scalability**: Valkey-based sessions scale horizontally
7. **Flexibility**: Supports WebAuthn for passwordless future