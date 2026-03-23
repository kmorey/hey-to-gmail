"""SQLite checkpoint store with file metadata validation and status machine."""
import sqlite3
from enum import Enum
from pathlib import Path
from typing import Optional


class MessageStatus(Enum):
    """Authoritative message status enum."""
    PENDING = "pending"
    IMPORTED = "imported"
    IMPORTED_UNLABELED = "imported_unlabeled"
    SKIPPED_FORWARDED = "skipped_forwarded"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    FAILED = "failed"


class CheckpointStore:
    """SQLite store for checkpoint persistence and message status tracking."""

    def __init__(self, db_path: Path):
        """Initialize store with database path."""
        self.db_path = Path(db_path)

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with row factory."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        """Initialize database schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        conn = self._get_connection()
        cursor = conn.cursor()

        # Create runs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                mode TEXT NOT NULL,
                total_files INTEGER NOT NULL,
                processing_order TEXT
            )
        """)

        # Create files table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                file_mtime REAL NOT NULL,
                ordinal INTEGER NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(id)
            )
        """)

        # Create messages table with all required columns
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                file_id INTEGER NOT NULL,
                message_index INTEGER NOT NULL,
                message_id_header TEXT,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                reason TEXT,
                import_retries INTEGER DEFAULT 0,
                label_retries INTEGER DEFAULT 0,
                hash_version INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (run_id) REFERENCES runs(id),
                FOREIGN KEY (file_id) REFERENCES files(id)
            )
        """)

        # Create checkpoints table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                file_id INTEGER PRIMARY KEY,
                file_size INTEGER NOT NULL,
                file_mtime REAL NOT NULL,
                message_index INTEGER NOT NULL,
                message_fingerprint TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (file_id) REFERENCES files(id)
            )
        """)

        # Create index on messages for faster lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_file_id 
            ON messages(file_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_status 
            ON messages(status)
        """)

        conn.commit()
        conn.close()

    def create_run(self, mode: str, total_files: int) -> int:
        """Create a new run record and return its ID."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO runs (mode, total_files)
            VALUES (?, ?)
        """, (mode, total_files))
        
        run_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return run_id

    def create_file(
        self,
        run_id: int,
        file_path: str,
        file_size: int,
        file_mtime: float,
        ordinal: int
    ) -> int:
        """Create a new file record and return its ID."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO files (run_id, file_path, file_size, file_mtime, ordinal)
            VALUES (?, ?, ?, ?, ?)
        """, (run_id, file_path, file_size, file_mtime, ordinal))
        
        file_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return file_id

    def create_message(
        self,
        run_id: int,
        file_id: int,
        message_index: int,
        message_id_header: Optional[str],
        fingerprint: str
    ) -> int:
        """Create a new message record and return its ID."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO messages 
            (run_id, file_id, message_index, message_id_header, fingerprint, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            run_id, file_id, message_index, message_id_header, 
            fingerprint, MessageStatus.PENDING.value
        ))
        
        message_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return message_id

    # Valid status transitions according to spec
    VALID_TRANSITIONS = {
        MessageStatus.PENDING: {
            MessageStatus.SKIPPED_FORWARDED,
            MessageStatus.SKIPPED_DUPLICATE,
            MessageStatus.IMPORTED,
            MessageStatus.IMPORTED_UNLABELED,
            MessageStatus.FAILED,
        },
        MessageStatus.IMPORTED_UNLABELED: {
            MessageStatus.IMPORTED,  # Label retry success
            MessageStatus.FAILED,    # Label retry exhaustion
        },
    }

    def update_message_status(
        self,
        message_id: int,
        status: MessageStatus,
        reason: Optional[str] = None,
        import_retries: Optional[int] = None,
        label_retries: Optional[int] = None,
        hash_version: Optional[int] = None
    ) -> None:
        """Update message status and optional fields.
        
        Validates transitions according to the state machine:
        - pending -> skipped_forwarded, skipped_duplicate, imported, imported_unlabeled, failed
        - imported_unlabeled -> imported, failed
        """
        # First, get the current status to validate the transition
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT status FROM messages WHERE id = ?
        """, (message_id,))
        
        row = cursor.fetchone()
        if row is None:
            conn.close()
            raise ValueError(f"Message {message_id} not found")
        
        current_status = MessageStatus(row["status"])
        
        # Validate the transition
        if current_status != status:
            allowed_transitions = self.VALID_TRANSITIONS.get(current_status, set())
            if status not in allowed_transitions:
                raise ValueError(
                    f"Invalid status transition: {current_status.value} -> {status.value}. "
                    f"Allowed transitions from {current_status.value}: "
                    f"{[s.value for s in allowed_transitions] if allowed_transitions else 'none'}"
                )
        
        # Build dynamic update query
        fields = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
        values = [status.value]
        
        if reason is not None:
            fields.append("reason = ?")
            values.append(reason)
        
        if import_retries is not None:
            fields.append("import_retries = ?")
            values.append(import_retries)
        
        if label_retries is not None:
            fields.append("label_retries = ?")
            values.append(label_retries)
        
        if hash_version is not None:
            fields.append("hash_version = ?")
            values.append(hash_version)
        
        values.append(message_id)
        
        query = f"""
            UPDATE messages 
            SET {', '.join(fields)}
            WHERE id = ?
        """
        
        cursor.execute(query, values)
        conn.commit()
        conn.close()

    def get_message_status(self, message_id: int) -> MessageStatus:
        """Get the current status of a message."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT status FROM messages WHERE id = ?
        """, (message_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row is None:
            raise ValueError(f"Message {message_id} not found")
        
        return MessageStatus(row["status"])

    def upsert_checkpoint(
        self,
        file_id: int,
        file_size: int,
        file_mtime: float,
        message_index: int,
        message_fingerprint: str
    ) -> None:
        """Create or update a checkpoint for a file."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO checkpoints 
            (file_id, file_size, file_mtime, message_index, message_fingerprint, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(file_id) DO UPDATE SET
                file_size = excluded.file_size,
                file_mtime = excluded.file_mtime,
                message_index = excluded.message_index,
                message_fingerprint = excluded.message_fingerprint,
                updated_at = CURRENT_TIMESTAMP
        """, (file_id, file_size, file_mtime, message_index, message_fingerprint))
        
        conn.commit()
        conn.close()

    def is_checkpoint_compatible(
        self,
        file_id: int,
        file_size: int,
        file_mtime: float
    ) -> bool:
        """Check if file metadata matches the stored checkpoint."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT file_size, file_mtime 
            FROM checkpoints 
            WHERE file_id = ?
        """, (file_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row is None:
            return False
        
        return row["file_size"] == file_size and row["file_mtime"] == file_mtime
