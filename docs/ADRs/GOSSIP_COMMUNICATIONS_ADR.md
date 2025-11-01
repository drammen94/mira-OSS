<artifact identifier="pager-federation-adr" type="application/vnd.ant.myst" title="ADR: MIRA Pager Federation Architecture">
# Architecture Decision Record: MIRA Pager Federation System

**Status:** Proposed  
**Date:** 2025-10-15  
**Decision Makers:** Architecture Team  
**Consulted:** Product, Engineering  

## Context and Problem Statement

MIRA's pager tool currently operates as a single-server messaging system where users can only communicate with others on the same deployment. As MIRA evolves toward supporting both hosted and open-source deployments, we need a federation mechanism that allows:

### Implementation Scope
The v1.0 implementation prioritizes simplicity and reliability:
- Basic server discovery via gossip protocol
- Cross-server message delivery with acknowledgments
- Manual security controls (blocklists)
- Single-instance user focus
- Extension points for future enhancements

Complex features are deliberately deferred to ensure a stable foundation.

### Core Requirements

1. Independent MIRA deployments to discover and communicate with each other
2. Users to send messages across server boundaries (e.g., `taylor@server-a` → `alex@server-b`)
3. Each deployment to remain valuable in isolation (no network effects dependency)
4. Future extensibility for inter-AI communication and additional distributed features

### Requirements

**Functional Requirements:**
- Enable cross-server pager messaging with transparent user experience
- Support server discovery without permanent central authority
- Maintain security and trust relationships between servers
- Allow graceful degradation when federation is unavailable
- Provide foundation for future distributed MIRA features

**Non-Functional Requirements:**
- Simple implementation that avoids over-engineering
- Protection against spam and malicious actors
- Privacy-preserving (no centralized user directories)
- Extensible protocol for future capabilities
- Operational simplicity for self-hosted deployments

## Decision Drivers

1. **Adoption Strategy**: Each deployment must be valuable standalone; federation is enhancement not requirement
2. **Simplicity**: Avoid distributed systems complexity where possible
3. **Security**: Prevent impersonation, spam, and network abuse
4. **Extensibility**: Support future features beyond paging (AI-to-AI communication, etc.)
5. **Decentralization**: Minimize permanent centralized dependencies
6. **Developer Experience**: Clear separation of concerns for maintainability

## Considered Options

### Option 1: Full Distributed Hash Table (DHT) with Kademlia
**Pros:**
- True decentralization with no central points
- Proven technology (BitTorrent, IPFS)
- Automatic routing and discovery

**Cons:**
- Significant implementation complexity
- Over-engineered for simple server announcements
- Challenging to debug and monitor
- Bootstrap problem still requires initial servers

### Option 2: Existing Federation Protocols (Matrix/ActivityPub)
**Pros:**
- Battle-tested protocols
- Rich ecosystems and tooling
- Solve many hard problems

**Cons:**
- Designed for persistent social graphs, not ephemeral paging
- Heavy protocol overhead
- Significant adaptation work required
- Doesn't match MIRA's use case well

### Option 3: Custom Gossip-Based Federation (Selected)
**Pros:**
- Right complexity level for our needs
- Natural fit for server discovery
- Simple to implement and debug
- Extensible for future features
- Fault-tolerant by design

**Cons:**
- Custom protocol requires maintenance
- Information propagation has latency
- Still needs bootstrap mechanism

## Decision Outcome

**Chosen option:** Custom gossip-based federation with modular architecture

### Architecture Overview
```
┌─────────────────────────────────────────────┐
│           MIRA Deployment A                  │
│  ┌──────────────┐        ┌───────────────┐  │
│  │ Pager Tool   │◄──────►│  Federation   │  │
│  │ (Existing)   │        │   Adapter     │  │
│  └──────────────┘        └───────┬───────┘  │
│                                   │          │
│  ┌────────────────────────────────▼───────┐  │
│  │     Discovery & Routing Daemon         │  │
│  │  - Server announcements                │  │
│  │  - Gossip protocol                     │  │
│  │  - Reputation system                   │  │
│  │  - Domain routing                      │  │
│  └────────────────┬───────────────────────┘  │
└───────────────────┼──────────────────────────┘
					│ Gossip Protocol
					▼
		 ┌──────────────────────┐
		 │  Other MIRA Servers  │
		 └──────────────────────┘
```

### Core Components

#### 1. Discovery & Routing Daemon (New)
Standalone service that handles federation concerns:

**Server Discovery:**
- Announces local server to network via gossip protocol
- Maintains list of known peer servers
- Exchanges capability information with neighbors
- Implements reputation scoring for peer servers

**Routing Service:**
- Resolves domain names to server endpoints (e.g., `mirah` → `https://mirah.example.com`)
- Implements query flooding for unknown domains
- Caches successful routing lookups
- Provides REST API for local tools to query

**Bootstrap Management:**
- Connects to well-known bootstrap servers on startup
- Learns additional peers through gossip
- Can function as bootstrap for others
- Handles bootstrap server migrations

**API Endpoints:**
```
POST /api/v1/announce          # Announce local server
GET  /api/v1/peers             # Get known peer servers
POST /api/v1/route/{domain}    # Resolve domain to endpoint
POST /api/v1/reputation/report # Report bad actor
GET  /api/v1/reputation/{server} # Get reputation score
```

#### 2. Federation Adapter (New)
Bridges between local pager tool and remote servers:

**Message Translation:**
- Converts local pager messages to federation protocol
- Handles incoming federated messages
- Manages cross-server authentication
- Routes messages based on domain

**Protocol:**
- Simple JSON-based message format
- Cryptographically signed with server keys
- Includes sender verification
- Extensible for future message types

**Authentication:**
- Each server has RSA keypair
- Messages signed with private key
- Recipients verify with public key
- Trust established on first contact (TOFU)

#### 3. Pager Tool (Modified)
Minimal changes to existing code:

**Address Format:**
- Support `user@domain` format
- Fallback to local-only if no `@` present
- Parse and route based on domain

**API Changes:**
- `send_message` accepts `user@domain` recipients
- Federation adapter handles remote delivery
- Local messages bypass federation entirely

### Protocol Specification

#### Server Announcement Message
```json
{
  "version": "1.0",
  "server_id": "mirah.example.com",
  "public_key": "-----BEGIN PUBLIC KEY-----...",
  "capabilities": {
	"paging": true,
	"ai_messaging": false,
	"supported_versions": ["1.0"]
  },
  "endpoints": {
	"federation": "https://mirah.example.com/federation",
	"discovery": "https://mirah.example.com/discovery"
  },
  "timestamp": "2025-10-15T10:30:00Z",
  "signature": "base64_signature..."
}
```

#### Federated Message Format
```json
{
  "version": "1.0",
  "message_id": "MSG-12345678",
  "message_type": "pager",
  "from": "taylor@mirah.example.com",
  "to": "alex@other-server.com",
  "content": "Message content here",
  "priority": 1,
  "timestamp": "2025-10-15T10:35:00Z",
  "sender_fingerprint": "ABC123...",
  "signature": "base64_signature..."
}
```

#### Message Delivery Acknowledgment
```json
{
  "version": "1.0",
  "ack_type": "message_received",
  "message_id": "MSG-12345678",
  "status": "delivered",
  "recipient_server": "other-server.com",
  "timestamp": "2025-10-15T10:35:01Z",
  "signature": "base64_signature..."
}
```

**Retry Logic:**
- Wait for acknowledgment with 30-second timeout
- Retry up to 2 additional times (3 total attempts)
- 30-second delay between retry attempts
- Log delivery failures for monitoring

#### Domain Resolution Request
```json
{
  "query_id": "QUERY-87654321",
  "domain": "other-server.com",
  "requester": "mirah.example.com",
  "max_hops": 5,
  "timestamp": "2025-10-15T10:36:00Z"
}
```

### Local Message Routing Architecture

Before discussing federation between servers, we must establish how messages route between users on the same MIRA instance. This local routing layer provides the foundation for the federation system.

#### Design Principles
- **Privacy by Default**: No browsing user directories; addresses exchanged out-of-band
- **Contact-Based Messaging**: Users must add contacts before messaging (like phone contacts)
- **Strong User Isolation**: Personal data remains user-scoped; only routing is shared
- **Single Device per User**: Each user has one pager device, simplifying routing
- **Trust on First Use**: Local and federated contacts use same trust model

#### Global Username Registry
Each MIRA server maintains a server-wide username registry to prevent collisions and enable local routing:

```sql
CREATE TABLE global_usernames (
    username TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE
);

CREATE INDEX idx_username_lookup ON global_usernames(username);
CREATE INDEX idx_userid_lookup ON global_usernames(user_id);
```

**Username Registration Flow:**
1. User must register username on first pager use
2. Usernames are unique per server (e.g., "taylor", "sally")
3. Registration validates against existing usernames
4. Username maps to internal user_id for routing

**Implementation:**
```python
def _register_username(self, username: str) -> Dict[str, Any]:
    """Register a pager username for the current user."""
    # Validate format (alphanumeric, 3-20 chars)
    if not re.match(r'^[a-z0-9_]{3,20}$', username.lower()):
        raise ValueError("Username must be 3-20 alphanumeric characters")

    # Check for collisions in global registry
    existing = self.db.execute(
        "SELECT username FROM global_usernames WHERE username = :username",
        {"username": username.lower()}
    )
    if existing:
        raise ValueError(f"Username '{username}' is already taken")

    # Register username
    self.db.execute(
        "INSERT INTO global_usernames (username, user_id, created_at, active) "
        "VALUES (:username, :user_id, :created_at, TRUE)",
        {
            "username": username.lower(),
            "user_id": self.user_id,
            "created_at": utc_now().isoformat()
        }
    )

    return {"username": username, "user_id": self.user_id}
```

#### Contact Schema Extension
The existing `contacts_tool` is extended to support pager addresses as a distinct field:

```sql
ALTER TABLE contacts ADD COLUMN pager_address TEXT;
CREATE INDEX idx_contacts_pager ON contacts(pager_address);
```

**Contact Storage:**
- **name**: Friendly display name (e.g., "Sally Smith")
- **email**: Email address (existing field, unrelated to paging)
- **phone**: Phone number (existing field)
- **pager_address**: Pager address (e.g., "sally" or "sally@remote.com")

**Adding a Contact with Pager Address:**
```python
contacts_tool.run(
    "add_contact",
    name="Sally Smith",
    email="sally@example.com",
    pager_address="sally"  # Local user
)

contacts_tool.run(
    "add_contact",
    name="Alex Johnson",
    email="alex@example.com",
    pager_address="alex@otherdomain.com"  # Remote user
)
```

#### Address Resolution Flow
When a user sends a pager message, the system resolves the recipient through multiple strategies:

```
User Input: "Page Sally with message 'Meeting at 3pm'"
↓
1. Check if "Sally" is a contact name
   → Lookup in contacts: "Sally" → pager_address: "sally"
↓
2. Parse pager_address for routing
   → "sally" (no @) = local routing
   → "sally@remote.com" (has @) = federation routing
↓
3a. Local Routing Path:
    - Query global_usernames: "sally" → user_id
    - Create message in sender's pager_messages table
    - Create message in recipient's pager_messages table
    - Both users see message in their isolated contexts
↓
3b. Federation Routing Path:
    - Extract domain: "remote.com"
    - Query Discovery Daemon for server endpoint
    - Route through Federation Adapter
    - Send via gossip protocol
```

**Implementation:**
```python
def _resolve_recipient(self, recipient_identifier: str) -> str:
    """Resolve recipient identifier to a routable address."""
    # Already a full address or device ID?
    if '@' in recipient_identifier or recipient_identifier.startswith('PAGER-'):
        return recipient_identifier

    # Try to resolve from contacts
    contact = self._lookup_contact(recipient_identifier)
    if contact and contact.get('pager_address'):
        return contact['pager_address']

    # Fallback: assume it's a local username
    return recipient_identifier

def _lookup_contact(self, name: str) -> Optional[Dict[str, Any]]:
    """Lookup contact by name using contacts_tool."""
    contacts_tool = ContactsTool()
    contacts_tool.user_id = self.user_id  # Inherit user context

    result = contacts_tool.run("get_contact", identifier=name)
    if result.get('success'):
        return result['contact']
    return None
```

#### Cross-User Message Delivery
When sending a message to another user on the same server, the system creates message records in both users' isolated databases:

```python
def _send_local_message(self, sender_id: str, recipient_username: str,
                       content: str, **kwargs) -> Dict[str, Any]:
    """Send message to another user on the same server."""

    # Resolve recipient username to user_id
    recipient_user_id = self._resolve_local_username(recipient_username)
    if not recipient_user_id:
        raise ValueError(f"Username '{recipient_username}' not found on this server")

    # Generate message ID
    message_id = f"MSG-{uuid.uuid4().hex[:8].upper()}"

    # Create message data
    message_data = {
        "id": message_id,
        "sender_id": sender_id,
        "recipient_id": recipient_username,  # Store username for display
        "content": content,
        "sent_at": utc_now().isoformat(),
        # ... other fields
    }

    # Insert into sender's database (user-scoped via self.db)
    self.db.insert('pager_messages', message_data)

    # Insert into recipient's database (requires cross-user operation)
    recipient_db = self._get_user_database(recipient_user_id)
    recipient_db.insert('pager_messages', message_data)

    return {"message": message_data, "status": "delivered"}

def _resolve_local_username(self, username: str) -> Optional[str]:
    """Resolve username to user_id from global registry."""
    result = self.db.execute(
        "SELECT user_id FROM global_usernames WHERE username = :username AND active = TRUE",
        {"username": username.lower()}
    )
    return result[0]['user_id'] if result else None
```

#### User Experience Flow

**First Time Setup:**
```
User: "Register my pager username as taylor"
System: "Registered pager username 'taylor'"

User: "Add Sally to contacts with pager address sally"
System: "Added contact Sally Smith (pager: sally)"
```

**Sending Messages:**
```
User: "Page Sally: Meeting moved to 3pm"
System resolves: "Sally" → contacts → pager_address: "sally"
System routes: "sally" (no @) → local delivery
System delivers: Creates message in both user contexts
Result: "Message sent to Sally Smith"

User: "Page Alex: Running late"
System resolves: "Alex" → contacts → pager_address: "alex@remote.com"
System routes: "alex@remote.com" (@present) → federation
System delivers: Routes through Federation Adapter
Result: "Message sent to alex@remote.com"
```

**Privacy & Blocking:**
- Users can delete contacts to stop receiving messages
- No server-wide user directory browsing
- Addresses exchanged out-of-band (just like phone numbers)
- Trust established on first message (TOFU model)

#### Database Schema Summary

**Server-Wide Tables (PostgreSQL):**
```sql
-- Username registry for routing
CREATE TABLE global_usernames (
    username TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE
);
```

**User-Scoped Tables (SQLite per user):**
```sql
-- Contacts with pager addresses
CREATE TABLE contacts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    pager_address TEXT,  -- NEW: "username" or "username@domain"
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Pager devices (unchanged)
CREATE TABLE pager_devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    -- existing fields
);

-- Pager messages (unchanged)
CREATE TABLE pager_messages (
    id TEXT PRIMARY KEY,
    sender_id TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    content TEXT NOT NULL,
    -- existing fields
);
```

### Gossip Protocol Details

**Neighbor Selection:**
- Each server maintains 5-10 active neighbors
- Periodically exchanges neighbor lists
- Random selection for neighbor choices

**Information Propagation:**
- Servers gossip every 30 seconds + random 0-5 second jitter
- Share: server announcements, routing info
- Include timestamps to detect stale information
- Deduplicate based on message IDs
- Scheduled system service handles gossip timing

**Convergence:**
- Network-wide convergence in O(log N) gossip rounds
- Eventual consistency model
- Stale data expires after 24 hours without refresh

### Security Mechanisms

#### 1. Cryptographic Trust
- **Server Identity**: RSA-2048 keypair per server
- **Message Signing**: All federated messages signed
- **Trust on First Use**: Accept fingerprint on first contact
- **Fingerprint Verification**: Detect impersonation attempts

#### 2. Rate Limiting
```python
# Per-source rate limits
QUERY_LIMIT = 100 per hour
MESSAGE_LIMIT = 1000 per hour
GOSSIP_LIMIT = 120 per hour  # 2 per minute

# Exponential backoff for violations
BACKOFF = min(3600, 60 * 2^violations)
```

#### 3. Reputation System
**v1.0 Approach:**
- Basic rate limiting only
- Manual blocklist for bad actors
- Focus on getting federation working reliably

**Note:** Advanced reputation features deferred to future versions.

#### 4. Bootstrap Server Strategy
**v1.0 Approach:**
- Initial hardcoded bootstrap servers (deployment phase)
- Support for peer exchange files shared out-of-band
- Community-maintained lists (similar to torrent tracker lists)
- Configuration option to add custom bootstrap servers

**Peer Exchange File Format:**
```json
{
  "version": "1.0",
  "peers": [
    {
      "server_id": "community1.example.com",
      "endpoints": {
        "discovery": "https://community1.example.com/discovery"
      },
      "last_seen": "2025-10-15T10:00:00Z"
    }
  ],
  "compiled_by": "community-maintainer",
  "compiled_date": "2025-10-15T10:00:00Z"
}
```

### Anti-Spam Measures

1. **Query Flooding Protection:**
   - TTL on routing queries (max 5 hops)
   - Query deduplication by ID
   - Rate limit queries per source

2. **Message Spam Prevention:**
   - Recipient servers can reject based on reputation
   - Per-sender rate limits
   - User-level blocking/filtering

3. **Gossip Spam Protection:**
   - Ignore duplicate announcements
   - Rate limit gossip from single source
   - Validate signatures on all gossip

### Implementation Phases

#### Phase 1: Core Discovery Daemon (MVP)
**Timeline:** 2-3 weeks
- PostgreSQL schema for federation data
- Basic gossip protocol with jitter
- Server announcements
- REST API for local queries
- Simple routing (direct lookups only)
- Vault integration for APP_URL and keypairs

**Deliverables:**
- Discovery daemon integrated with MIRA
- Gossip scheduled service
- Basic logging hooks
- Unit tests for core functionality

#### Phase 2: Federation Adapter
**Timeline:** 2-3 weeks
- Message signing/verification
- Delivery acknowledgment system
- Retry logic (3 attempts, 30s intervals)
- Integration with pager tool
- Error handling

**Deliverables:**
- Adapter service
- Message delivery confirmation
- Integration tests
- API documentation

#### Phase 3: Community Features (v1.0 Complete)
**Timeline:** 1-2 weeks
- Peer exchange file support
- Manual blocklist management
- Basic rate limiting
- Configuration for custom bootstrap servers

**Deliverables:**
- Peer file parser
- Admin commands for blocklist
- Documentation for self-hosters
- Small-scale deployment testing

#### Future Phases
Additional features and enhancements will be planned based on v1.0 deployment experience.

## Implementation Guidelines

### Discovery Daemon Implementation
**Language:** Python 3.10+
**Framework:** FastAPI for REST API
**Storage:** PostgreSQL (shared with MIRA instance)
**Libraries:**
- `cryptography` for signing/verification
- `httpx` for async HTTP
- `pydantic` for data validation
- `asyncpg` for database operations

**Configuration:**
```yaml
# Domain and endpoints derived from Vault APP_URL
# Bootstrap servers initially hardcoded, later community-maintained list

bootstrap_servers:
  - "https://bootstrap1.mirah.com"  # To be added in deployment phase
  - "https://bootstrap2.mirah.com"
  # Future: Support for peer exchange files shared out-of-band

gossip:
  interval_seconds: 30
  interval_jitter: 5  # Random 0-5 second jitter to prevent thundering herd
  neighbor_count: 8

security:
  # Keypair stored in Vault at federation/keypair
  require_signatures: true

rate_limits:
  queries_per_hour: 100
  messages_per_hour: 1000
```

### Federation Adapter Implementation
**Integration Point:** Modify `pager_tool.py` `_send_message()` method
```python
def _send_message(self, sender_id, recipient_id, content, **kwargs):
	# Parse recipient
	if '@' in recipient_id:
		domain = recipient_id.split('@')[1]
		# self.local_domain derived from APP_URL in Vault
		if domain != self.local_domain:
			# Route through federation adapter
			return self._send_federated_message(
				sender_id, recipient_id, content, **kwargs
			)

	# Local delivery (existing code)
	return self._send_local_message(sender_id, recipient_id, content, **kwargs)
```

### Testing Strategy

**Unit Tests:**
- Message signing/verification
- Gossip protocol logic with jitter
- Routing resolution
- Delivery acknowledgment and retry logic

**Integration Tests:**
- Multi-server discovery
- Message delivery across servers
- Bootstrap server connection
- Peer exchange file loading

**Security Tests:**
- Signature validation
- Rate limiting enforcement
- Basic blocklist functionality

**Performance Tests:**
- Gossip convergence time
- Routing query latency
- Message delivery throughput
- Small-scale testing (5-10 servers initially)

### Observability

**v1.0 Implementation:**
- Structured logging for all federation operations
- Basic metrics collection interfaces
- Extension points for future telemetry

```python
# Example logging structure
logger.info("federation.message_sent", {
    "message_id": message_id,
    "recipient": recipient,
    "retry_count": retry_count
})
```

## Consequences

### Positive

1. **Modularity**: Clean separation enables independent development and testing
2. **Extensibility**: Protocol can evolve to support AI-to-AI messaging and other features
3. **Simplicity**: Avoids over-engineering while meeting requirements
4. **Autonomy**: Each deployment remains functional without federation
5. **Security**: Multi-layered protection against common attacks
6. **Decentralization**: No permanent single points of failure
7. **Gradual Adoption**: Users get value immediately, federation as bonus

### Negative

1. **Custom Protocol**: Requires ongoing maintenance vs. adopting existing standards
2. **Bootstrap Dependency**: Initial servers remain somewhat centralized
3. **Eventual Consistency**: Routing may have latency across large networks
4. **Complexity**: Still adds distributed systems concerns to codebase
5. **Debugging Challenge**: Cross-server issues harder to diagnose than local
6. **Security Surface**: New attack vectors from network exposure

### Mitigations

- **Protocol Versioning**: Support multiple versions for backward compatibility
- **Comprehensive Logging**: Distributed tracing for cross-server debugging
- **Monitoring Tools**: Build dashboards for federation health
- **Documentation**: Clear guides for self-hosters
- **Gradual Rollout**: Test thoroughly before production
- **Security Audits**: Regular reviews of cryptographic implementations

## Future Considerations

### Inter-AI Communication
The protocol designed here supports future AI-to-AI messaging:
- Treat as new message_type: "ai_to_ai"
- Add Llama Guard or similar prompt injection defense
- Monitor emergent behavior patterns
- Start with simple text messages, evolve based on usage

### Additional Distributed Features
- Shared tool capabilities across servers
- Distributed task coordination
- Cross-server memory references (with privacy controls)
- Federated user identity (optional)

### Protocol Evolution
- Version negotiation for breaking changes
- Feature flags for optional capabilities
- Deprecation process for old versions
- Migration paths for protocol updates

## Decision Validation

**Success Criteria:**
- [ ] Two independent MIRA deployments can exchange pager messages
- [ ] Message delivery acknowledgments work reliably (3 attempts)
- [ ] Gossip protocol functions with jitter (no thundering herd)
- [ ] Peer exchange files can bootstrap new servers
- [ ] Manual blocklist prevents bad actors
- [ ] Integration preserves existing MIRA functionality
- [ ] Small-scale deployment (5-10 servers) operates smoothly

**Review Timeline:**
- Initial review: After Phase 3 completion
- Ongoing: Monthly reviews during initial deployment
- Future planning: Based on production experience

## References

- MIRA Pager Tool: `/path/to/pager_tool.py`
- Gossip Protocol: Birman, K. "The Promise, and Limitations, of Gossip Protocols"
- DHT Research: Maymounkov & Mazières, "Kademlia: A Peer-to-Peer Information System"
- Federation Standards: Matrix Protocol Specification, ActivityPub W3C Recommendation
- Security: TOFU model similar to SSH host key verification

## Approval

This ADR requires approval from:
- [ ] Technical Lead
- [ ] Security Team
- [ ] Product Owner
- [ ] DevOps Team

---

**Document History:**
- 2025-10-15: Initial draft based on architecture discussions
- 2025-10-16: Updated for v1.0 implementation focus:
  - Switched from SQLite to PostgreSQL integration
  - Added message delivery acknowledgments
  - Simplified security model for initial deployment
  - Added gossip jitter for thundering herd prevention
  - Added peer exchange file support
  - Focused on single-instance user scenario
  - Reduced emphasis on future features
  - Added Local Message Routing Architecture section:
    - Global username registry design
    - Contact schema extension for pager addresses
    - Address resolution flow for local and federated routing
    - Cross-user message delivery on same server
    - Privacy-preserving contact-based messaging model
</artifact>