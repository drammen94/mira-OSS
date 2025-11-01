"""
User data management module for per-user SQLite databases with session-based encryption.
"""

import base64
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from clients.sqlite_client import get_sqlite_client

logger = logging.getLogger(__name__)


class UserDataManager:
    """Manages per-user SQLite databases with session-based encryption."""
    
    def __init__(self, user_id: UUID, session_key: Optional[bytes] = None):
        self.user_id = user_id
        self.session_key = session_key
        self.fernet = self._create_fernet() if session_key else None
        self.db_path = self._get_user_db_path()
        self._ensure_database()
        self.db_client = get_sqlite_client(str(self.db_path), str(user_id))
    
    def _create_fernet(self) -> Fernet:
        """Create Fernet cipher from session key."""
        key = base64.urlsafe_b64encode(self.session_key[:32])  # Ensure 32 bytes
        return Fernet(key)
    
    @property
    def base_dir(self) -> Path:
        user_dir = Path("data/users") / str(self.user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir
    
    def _get_user_db_path(self) -> Path:
        return self.base_dir / "userdata.db"
    
    def _ensure_database(self):
        """Create database file if it doesn't exist and set up tool schemas."""
        is_new_database = not self.db_path.exists()
        if is_new_database:
            self.db_path.touch()
            logger.info(f"Created user database: {self.db_path}")
            # Set up all tool schemas for new user
            self._initialize_tool_schemas()
    
    def _initialize_tool_schemas(self):
        """Initialize database schemas for all tools."""
        logger.info(f"Initializing tool schemas for user {self.user_id}")
        
        # Get database client
        db_client = get_sqlite_client(str(self.db_path), self.user_id)
        
        # Initialize PagerTool schema
        self._init_pager_schema(db_client)
        
        logger.info("Tool schemas initialized successfully")
    
    def _init_pager_schema(self, db_client):
        """Initialize PagerTool database schema."""
        # Pager devices table
        devices_sql = """
        CREATE TABLE IF NOT EXISTS pager_devices (
            id TEXT PRIMARY KEY,
            user_id UUID NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            device_secret TEXT NOT NULL,
            device_fingerprint TEXT NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE
        )
        """
        
        # Pager trust table
        trust_sql = """
        CREATE TABLE IF NOT EXISTS pager_trust (
            id TEXT PRIMARY KEY,
            user_id UUID NOT NULL,
            trusting_device_id TEXT NOT NULL,
            trusted_device_id TEXT NOT NULL,
            trusted_fingerprint TEXT NOT NULL,
            trusted_name TEXT,
            first_seen TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_verified TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            trust_status TEXT NOT NULL DEFAULT 'trusted',
            UNIQUE(trusting_device_id, trusted_device_id)
        )
        """
        
        # Pager messages table
        messages_sql = """
        CREATE TABLE IF NOT EXISTS pager_messages (
            id TEXT PRIMARY KEY,
            user_id UUID NOT NULL,
            sender_id TEXT NOT NULL,
            recipient_id TEXT NOT NULL,
            content TEXT NOT NULL,
            original_content TEXT,
            ai_distilled BOOLEAN NOT NULL DEFAULT FALSE,
            priority INTEGER NOT NULL DEFAULT 0,
            location TEXT,
            sent_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP WITH TIME ZONE,
            read_at TIMESTAMP WITH TIME ZONE,
            delivered BOOLEAN NOT NULL DEFAULT TRUE,
            read BOOLEAN NOT NULL DEFAULT FALSE,
            message_signature TEXT,
            sender_fingerprint TEXT
        )
        """
        
        # Create indexes
        indexes_sql = [
            "CREATE INDEX IF NOT EXISTS idx_pager_devices_user_id ON pager_devices(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_pager_devices_active ON pager_devices(active)",
            "CREATE INDEX IF NOT EXISTS idx_pager_trust_user_id ON pager_trust(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_pager_trust_trusting_device ON pager_trust(trusting_device_id)",
            "CREATE INDEX IF NOT EXISTS idx_pager_messages_user_id ON pager_messages(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_pager_messages_sender ON pager_messages(sender_id)",
            "CREATE INDEX IF NOT EXISTS idx_pager_messages_recipient ON pager_messages(recipient_id)",
            "CREATE INDEX IF NOT EXISTS idx_pager_messages_expires ON pager_messages(expires_at)"
        ]
        
        # Execute schema creation
        db_client.execute_query(devices_sql)
        db_client.execute_query(trust_sql)
        db_client.execute_query(messages_sql)
        
        # Create indexes
        for index_sql in indexes_sql:
            db_client.execute_query(index_sql)
    
    def _encrypt_value(self, value: Any) -> str:
        """
        Encrypt a value for storage.

        Raises:
            RuntimeError: If encryption key is not available
        """
        if self.fernet is None:
            raise RuntimeError(
                "No encryption key available. Cannot store encrypted data without encryption key. "
                "Ensure session_key is provided to UserDataManager."
            )

        json_str = json.dumps(value)
        encrypted_token = self.fernet.encrypt(json_str.encode())
        return encrypted_token.decode()  # Fernet returns base64-encoded bytes
    
    def _decrypt_value(self, encrypted_str: str) -> Any:
        """
        Decrypt a value from storage.

        Raises:
            RuntimeError: If encryption key is not available
        """
        if self.fernet is None:
            raise RuntimeError(
                "No encryption key available. Cannot decrypt data without encryption key. "
                "Ensure session_key is provided to UserDataManager."
            )

        try:
            decrypted_bytes = self.fernet.decrypt(encrypted_str.encode())
            return json.loads(decrypted_bytes.decode())
        except Exception:
            # Fallback for unencrypted data (migration scenario)
            try:
                return json.loads(encrypted_str)
            except json.JSONDecodeError:
                return encrypted_str
    
    def _encrypt_dict(self, data: Dict[str, Any]) -> Dict[str, str]:
        """Encrypt fields with encrypted__ prefix (prefix is kept in column name)."""
        result = {}
        for key, value in data.items():
            if key.startswith('encrypted__'):
                # Encrypt value but keep the encrypted__ prefix in the column name
                result[key] = self._encrypt_value(value) if value is not None else None
            else:
                # Store as-is
                result[key] = str(value) if value is not None else None
        return result
    
    def _decrypt_dict(self, data: Dict[str, str]) -> Dict[str, Any]:
        """Decrypt fields by attempting decryption on each value."""
        result = {}
        for key, value in data.items():
            if value is not None:
                try:
                    # Try to decrypt - if it works, it was encrypted
                    decrypted = self._decrypt_value(value)
                    result[key] = decrypted
                except Exception:
                    # Not encrypted or decryption failed, use as-is
                    result[key] = value
            else:
                result[key] = value
        return result
    
    def execute(self, query: str, params: Optional[Dict] = None) -> List[Dict]:
        """Execute SQL query and return results."""
        return self.db_client.execute_query(query, params)
    
    def fetchone(self, query: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Execute query and return single row."""
        results = self.execute(query, params)
        return results[0] if results else None
    
    def fetchall(self, query: str, params: Optional[Dict] = None) -> List[Dict]:
        """Execute query and return all rows."""
        return self.execute(query, params)
    
    def create_table(self, table_name: str, schema: str):
        """Create table with given schema."""
        query = f"CREATE TABLE IF NOT EXISTS {table_name} ({schema})"
        self.db_client.execute_query(query)
    
    def insert(self, table_name: str, data: Dict[str, Any]) -> str:
        """Insert encrypted data and return row ID."""
        encrypted_data = self._encrypt_dict(data)
        columns = list(encrypted_data.keys())
        placeholders = [f":{col}" for col in columns]
        query = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({', '.join(placeholders)})"
        return self.db_client.execute_insert(query, encrypted_data)
    
    def select(self, table_name: str, where: str = None, params: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """Select and decrypt rows from table."""
        query = f"SELECT * FROM {table_name}"
        if where:
            query += f" WHERE {where}"
        rows = self.fetchall(query, params)
        return [self._decrypt_dict(row) for row in rows]
    
    def update(self, table_name: str, data: Dict[str, Any], where: str, params: Optional[Dict] = None) -> int:
        """Update rows with encrypted data."""
        encrypted_data = self._encrypt_dict(data)
        set_clauses = [f"{col} = :{col}" for col in encrypted_data.keys()]
        query = f"UPDATE {table_name} SET {', '.join(set_clauses)} WHERE {where}"
        
        all_params = encrypted_data.copy()
        if params:
            all_params.update(params)
        
        return self.db_client.execute_update(query, all_params)
    
    def delete(self, table_name: str, where: str, params: Optional[Dict] = None) -> int:
        """Delete rows from table."""
        query = f"DELETE FROM {table_name} WHERE {where}"
        return self.db_client.execute_delete(query, params)
    
    @property
    def conversations_dir(self) -> Path:
        path = self.base_dir / "conversations"
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    @property
    def tool_feedback_dir(self) -> Path:
        path = self.base_dir / "tool_feedback"
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    def get_tool_data_dir(self, tool_name: str) -> Path:
        path = self.base_dir / "tools" / tool_name
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    @property
    def config_path(self) -> Path:
        return self.base_dir / "config.json"
    
    def _ensure_credentials_table(self):
        """Helper method to ensure credentials table exists (used by UserCredentialService)."""
        schema = """
            id TEXT PRIMARY KEY,
            credential_type TEXT NOT NULL,
            service_name TEXT NOT NULL,
            credential_value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(credential_type, service_name)
        """
        self.create_table('credentials', schema)


def derive_session_key(user_id: str) -> bytes:
    """Derive persistent encryption key from user ID."""
    import hashlib
    # Create deterministic key from user UUID
    # This key remains constant for the user's lifetime
    key_material = f"userdata_encryption_{user_id}".encode()
    return hashlib.sha256(key_material).digest()


def get_user_data_manager(user_id: UUID) -> UserDataManager:
    """Get a UserDataManager instance with automatic encryption key derivation."""
    session_key = derive_session_key(str(user_id))
    return UserDataManager(user_id, session_key)

