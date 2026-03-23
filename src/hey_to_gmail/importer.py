"""Core importer logic with dry-run, execute, and resume fallback support."""
import csv
import hashlib
import logging
from email import policy
from email.parser import BytesParser
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from hey_to_gmail.checkpoint_store import CheckpointStore, MessageStatus
from hey_to_gmail.duplicate_detector import dedupe_key_for_message
from hey_to_gmail.forwarded_filter import is_forwarded_from_gmail
from hey_to_gmail.mbox_reader import MboxReader
from hey_to_gmail.reporting import DetectionAuditCounter, REPORT_FIELDNAMES, build_report_row


logger = logging.getLogger(__name__)


class MboxImporter:
    """Import messages from MBOX to Gmail with resume support."""
    
    DEFAULT_CHECKPOINT_INTERVAL = 100
    DEFAULT_LABEL_NAME = "HEY-Imported"
    DEFAULT_MAX_LABEL_RETRIES = 3
    
    def __init__(
        self,
        mbox_path: Path,
        db_path: Path,
        csv_path: Path,
        mode: str,  # "dry-run" or "execute"
        gmail_addr: str,
        hey_addr: str,
        gmail_client=None,
        label_manager=None,
        label_name: str = DEFAULT_LABEL_NAME,
        forwarded_mode: str = "strict",
        enable_remote_dedupe: bool = False,
        checkpoint_interval: int = DEFAULT_CHECKPOINT_INTERVAL,
        max_label_retries: int = DEFAULT_MAX_LABEL_RETRIES,
        trial_sample_size: Optional[int] = None,
        allow_short_trial: bool = False,
        verbose: bool = False
    ):
        """Initialize importer.
        
        Args:
            mbox_path: Path to MBOX file
            db_path: Path to SQLite database
            csv_path: Path to CSV report
            mode: "dry-run" or "execute"
            gmail_addr: Gmail address for forwarded detection
            hey_addr: HEY address for forwarded detection
            gmail_client: Gmail client instance (for execute mode)
            label_manager: Label manager instance (for execute mode)
            label_name: Label to apply to imported messages
            forwarded_mode: "strict" or "strict_plus" for forwarded detection
            enable_remote_dedupe: Whether to query Gmail for duplicates
            checkpoint_interval: Messages between checkpoint writes
            max_label_retries: Max label retry attempts
            trial_sample_size: Optional trial sample size for selected-index processing
            allow_short_trial: Allow a shortened trial when full trial cannot be selected
            verbose: Enable verbose logging
        """
        self.mbox_path = Path(mbox_path)
        self.csv_path = Path(csv_path)
        self.mode = mode
        self.gmail_addr = gmail_addr
        self.hey_addr = hey_addr
        self.gmail_client = gmail_client
        self.label_manager = label_manager
        self.label_name = label_name
        self.forwarded_mode = forwarded_mode
        self.enable_remote_dedupe = enable_remote_dedupe
        self.checkpoint_interval = checkpoint_interval
        self.max_label_retries = max_label_retries
        self.trial_sample_size = trial_sample_size
        self.allow_short_trial = allow_short_trial
        self.verbose = verbose
        
        # Initialize store
        self.store = CheckpointStore(db_path)
        self.store.initialize()
        
        # Initialize reader
        self.reader = MboxReader(mbox_path)
        
        # State tracking
        self.run_id: Optional[int] = None
        self.file_id: Optional[int] = None
        self.message_count = 0
        self.checkpoint_message_index = -1
        self.checkpoint_fingerprint = ""
        
        # Seen message IDs for local dedupe
        self.seen_message_ids: Set[str] = set()
        
        # Detection audit
        self.audit_counter = DetectionAuditCounter()
        
        # Results tracking
        self.results = {
            "processed": 0,
            "imported": 0,
            "imported_unlabeled": 0,
            "skipped_forwarded": 0,
            "skipped_duplicate": 0,
            "failed": 0,
            "detection_audit": None
        }
        
        # CSV report rows
        self.csv_rows: List[Dict] = []
        
        # Label ID (cached after first lookup)
        self._label_id: Optional[str] = None

        # Trial selector state
        self.last_trial_selection_warning: Optional[str] = None
        self._trial_selected = self.trial_sample_size is not None
        self._trial_profile = "curated" if self._trial_selected else ""

    def select_trial_indices(
        self,
        sample_size: int,
        allow_short_trial: bool = False,
    ) -> List[int]:
        """Select deterministic curated trial indices.

        For sample sizes >=2, targets one forwarded candidate and fills remaining
        slots from importable candidates in priority order:
        attachment -> gmail-origin -> plain-text -> fallback.
        
        Uses a two-pass approach with fresh MboxReader instances to avoid
        mutation issues from header unfolding:
        1. First pass: identify all forwarded messages
        2. Second pass: build importable pools only from non-forwarded messages
        """
        if sample_size <= 0:
            raise ValueError("sample_size must be positive")

        logger.info("Trial selection: Pass 1 - identifying forwarded messages...")
        
        # Pass 1: Identify all forwarded messages (use fresh reader)
        forwarded_indices: Set[int] = set()
        total_messages = 0
        
        pass1_reader = MboxReader(self.mbox_path)
        for message_index, email_msg, raw_bytes in pass1_reader.stream_messages():
            total_messages += 1
            is_forwarded = is_forwarded_from_gmail(
                email_msg,
                self.gmail_addr,
                self.hey_addr,
                mode=self.forwarded_mode,
            )
            if is_forwarded:
                forwarded_indices.add(message_index)
        
        logger.info(
            f"Trial selection: Found {len(forwarded_indices)} forwarded messages "
            f"out of {total_messages} total"
        )
        
        # Pass 2: Build importable pools from non-forwarded messages only
        logger.info("Trial selection: Pass 2 - building importable candidate pools...")
        
        forwarded_candidates: List[int] = []
        importable_candidates: List[int] = []
        attachment_candidates: List[int] = []
        gmail_origin_candidates: List[int] = []
        plain_text_candidates: List[int] = []

        terminal_indices = self._load_terminal_local_state_indices()

        # Use fresh reader for pass 2 to avoid mutation issues
        pass2_reader = MboxReader(self.mbox_path)
        for message_index, email_msg, raw_bytes in pass2_reader.stream_messages():
            # Skip if forwarded (already identified in Pass 1)
            if message_index in forwarded_indices:
                forwarded_candidates.append(message_index)
                continue

            if message_index in terminal_indices:
                continue

            if not self._has_parseable_raw_bytes(raw_bytes):
                continue

            importable_candidates.append(message_index)
            if self._has_attachment(email_msg):
                attachment_candidates.append(message_index)
            if self._is_gmail_origin(email_msg):
                gmail_origin_candidates.append(message_index)
            if self._is_plain_text_dominant(email_msg):
                plain_text_candidates.append(message_index)
        
        logger.info(
            f"Trial selection: Importable pools - "
            f"attachment: {len(attachment_candidates)}, "
            f"gmail-origin: {len(gmail_origin_candidates)}, "
            f"plain-text: {len(plain_text_candidates)}, "
            f"fallback: {len(importable_candidates)}"
        )

        # Trial mode: select ONLY from non-forwarded importable messages
        # This ensures trial emails will actually be imported for verification
        selected: List[int] = []
        selected_set: Set[int] = set()
        shortfall_reasons: List[str] = []
        
        importable_target = sample_size

        def _fill_from_pool(pool: List[int], remaining: int) -> int:
            filled = 0
            for index in pool:
                if filled >= remaining:
                    break
                if index in selected_set:
                    continue
                selected.append(index)
                selected_set.add(index)
                filled += 1
            return filled

        importable_needed = importable_target
        for pool in (
            attachment_candidates,
            gmail_origin_candidates,
            plain_text_candidates,
            importable_candidates,
        ):
            if importable_needed <= 0:
                break
            importable_needed -= _fill_from_pool(pool, importable_needed)

        if importable_needed > 0:
            found = importable_target - importable_needed
            shortfall_reasons.append(
                f"importable shortfall: required {importable_target} importable candidates but found {found}"
            )

        selected = sorted(selected)
        if len(selected) < sample_size:
            summary = "; ".join(shortfall_reasons) if shortfall_reasons else "insufficient candidates"
            if not allow_short_trial:
                raise ValueError(
                    f"Unable to satisfy curated trial sample of {sample_size}: {summary}"
                )
            self.last_trial_selection_warning = (
                f"Trial selection shortened from {sample_size} to {len(selected)}: {summary}"
            )
        else:
            self.last_trial_selection_warning = None

        return selected

    def _load_terminal_local_state_indices(self) -> Set[int]:
        """Load message indices in terminal local states for this mbox path."""
        conn = self.store._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT m.message_index
            FROM messages m
            JOIN files f ON m.file_id = f.id
            WHERE f.file_path = ?
              AND m.status IN (?, ?)
            """,
            (
                str(self.mbox_path),
                MessageStatus.IMPORTED.value,
                MessageStatus.SKIPPED_DUPLICATE.value,
            ),
        )
        rows = cursor.fetchall()
        conn.close()
        return {row["message_index"] for row in rows}

    def _has_parseable_raw_bytes(self, raw_bytes: bytes) -> bool:
        """Return whether raw bytes are parseable into a message with headers."""
        if not isinstance(raw_bytes, (bytes, bytearray)):
            return False
        if not bytes(raw_bytes).strip():
            return False
        try:
            parsed = BytesParser(policy=policy.default).parsebytes(bytes(raw_bytes))
        except Exception:
            return False
        return bool(list(parsed.keys()))

    def _has_attachment(self, email_msg: EmailMessage) -> bool:
        """Return whether message has a non-inline attachment part."""
        if not email_msg.is_multipart():
            return False
        for part in email_msg.walk():
            if part.get_content_disposition() == "attachment":
                return True
        return False

    def _is_gmail_origin(self, email_msg: EmailMessage) -> bool:
        """Return whether message appears to have Gmail origin headers."""
        message_id = email_msg.get("Message-ID", "")
        if "@" in message_id and message_id.rstrip(">").endswith("@mail.gmail.com"):
            return True

        if email_msg.get("X-Gm-Message-State"):
            return True

        for hop in email_msg.get_all("Received", []):
            hop_lower = str(hop).lower()
            if "google.com" in hop_lower and "gmail" in hop_lower:
                return True
        return False

    def _is_plain_text_dominant(self, email_msg: EmailMessage) -> bool:
        """Return whether message includes text/plain content."""
        if email_msg.is_multipart():
            for part in email_msg.walk():
                if part.get_content_type() == "text/plain":
                    return True
            return False
        return email_msg.get_content_type() == "text/plain"
    
    def run(self) -> Dict:
        """Run the import process.
        
        Returns:
            Dictionary with import results
        """
        try:
            self._setup_run()
            self._recover_imported_unlabeled()
            self._process_messages()
            self._finalize_run()
            return self.results
        except Exception as e:
            logger.error(f"Import failed: {e}")
            raise
        finally:
            # Always flush checkpoint on shutdown
            self._flush_checkpoint()
    
    def _setup_run(self):
        """Initialize run and file records."""
        file_size, file_mtime = self.reader.get_file_metadata()

        # Create run record
        self.run_id = self.store.create_run(
            mode=self.mode,
            total_files=1
        )

        # Check for existing checkpoint first (before creating file record)
        # Skip checkpoint in trial mode - always start fresh to process trial sample
        checkpoint_info = None
        if not self._trial_selected:
            checkpoint_info = self._find_existing_checkpoint(file_size, file_mtime)

        # Create file record
        self.file_id = self.store.create_file(
            run_id=self.run_id,
            file_path=str(self.mbox_path),
            file_size=file_size,
            file_mtime=file_mtime,
            ordinal=0
        )

        # Apply checkpoint if compatible (not in trial mode)
        if checkpoint_info:
            self.checkpoint_message_index = checkpoint_info["message_index"]
            self.checkpoint_fingerprint = checkpoint_info["message_fingerprint"]
            logger.info(
                f"Resuming from checkpoint: message {self.checkpoint_message_index}"
            )
        else:
            if self._trial_selected:
                logger.info("Trial mode: starting from beginning (checkpoint ignored)")
            # Load persisted outcomes for idempotent skip
            self._load_persisted_outcomes()

        logger.info(f"Starting {self.mode} run {self.run_id} for {self.mbox_path}")
    
    def _find_existing_checkpoint(self, file_size: int, file_mtime: float) -> Optional[Dict]:
        """Find existing checkpoint by file path and verify compatibility.
        
        Args:
            file_size: Current file size
            file_mtime: Current file modification time
            
        Returns:
            Checkpoint data dict if compatible, None otherwise
        """
        conn = self.store._get_connection()
        cursor = conn.cursor()
        
        # Find most recent checkpoint for this file path
        cursor.execute(
            """
            SELECT c.file_id, c.file_size, c.file_mtime, c.message_index, c.message_fingerprint
            FROM checkpoints c
            JOIN files f ON c.file_id = f.id
            WHERE f.file_path = ?
            ORDER BY c.updated_at DESC
            LIMIT 1
            """,
            (str(self.mbox_path),)
        )
        
        row = cursor.fetchone()
        conn.close()
        
        if row is None:
            return None
        
        # Check if metadata matches
        if row["file_size"] != file_size or row["file_mtime"] != file_mtime:
            logger.info("Checkpoint metadata mismatch, performing full rescan")
            return None
        
        return {
            "file_id": row["file_id"],
            "message_index": row["message_index"],
            "message_fingerprint": row["message_fingerprint"]
        }
    
    def _load_persisted_outcomes(self):
        """Load previously processed message outcomes for idempotent skipping."""
        # Query messages from previous runs for this file
        conn = self.store._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT message_id_header, fingerprint, status 
            FROM messages m
            JOIN files f ON m.file_id = f.id
            WHERE f.file_path = ? AND m.status != ?
            """,
            (str(self.mbox_path), MessageStatus.PENDING.value)
        )
        
        for row in cursor.fetchall():
            msg_id = row["message_id_header"]
            if msg_id:
                self.seen_message_ids.add(msg_id)
        
        conn.close()
    
    def _recover_imported_unlabeled(self):
        """Recover messages in imported_unlabeled state."""
        if self.mode != "execute" or not self.gmail_client:
            return
        
        conn = self.store._get_connection()
        cursor = conn.cursor()
        
        # Find messages needing label retry
        cursor.execute(
            """
            SELECT id, message_id_header, label_retries 
            FROM messages
            WHERE status = ? AND file_id IN (
                SELECT id FROM files WHERE file_path = ?
            )
            """,
            (MessageStatus.IMPORTED_UNLABELED.value, str(self.mbox_path))
        )
        
        unlabeled_messages = cursor.fetchall()
        conn.close()
        
        for row in unlabeled_messages:
            message_db_id = row["id"]
            msg_id_header = row["message_id_header"]
            label_retries = row["label_retries"] or 0
            
            if label_retries >= self.max_label_retries:
                # Mark as failed
                self.store.update_message_status(
                    message_db_id,
                    MessageStatus.FAILED,
                    reason=f"Label retry exhausted ({label_retries} attempts)"
                )
                self.results["failed"] += 1
            else:
                # Attempt to apply label
                if self._apply_label_to_message(msg_id_header):
                    self.store.update_message_status(
                        message_db_id,
                        MessageStatus.IMPORTED
                    )
                    self.results["imported"] += 1
                else:
                    # Increment retry count
                    self.store.update_message_status(
                        message_db_id,
                        MessageStatus.IMPORTED_UNLABELED,
                        label_retries=label_retries + 1
                    )
                    self.results["imported_unlabeled"] += 1
    
    def _apply_label_to_message(self, msg_id_header: str) -> bool:
        """Apply label to an existing Gmail message.
        
        Args:
            msg_id_header: Message-ID header value
            
        Returns:
            True if successful, False otherwise
        """
        if not self.gmail_client or not self.label_manager:
            return False
        
        try:
            # Get label ID
            label_id = self._get_label_id()
            if not label_id:
                return False
            
            # Get service - handle both GmailClient wrapper and raw service mock
            # Use getattr with sentinel to detect if _service is actually set
            from hey_to_gmail.gmail_client import GmailClient
            if isinstance(self.gmail_client, GmailClient):
                service = self.gmail_client._service
            else:
                service = self.gmail_client
            
            # Search for message by Message-ID
            query = f"rfc822msgid:{msg_id_header.strip('<>')}"
            result = service.users().messages().list(
                userId='me',
                q=query,
                maxResults=1
            ).execute()
            
            messages = result.get('messages', [])
            if not messages:
                return False
            
            gmail_msg_id = messages[0]['id']
            
            # Apply label
            service.users().messages().modify(
                userId='me',
                id=gmail_msg_id,
                body={'addLabelIds': [label_id]}
            ).execute()
            
            return True
        except Exception as e:
            logger.warning(f"Failed to apply label: {e}")
            return False
    
    def _process_messages(self):
        """Process all messages in MBOX."""
        selected_trial_indices: Optional[Set[int]] = None
        if self.trial_sample_size is not None:
            selected_trial_indices = set(
                self.select_trial_indices(
                    sample_size=self.trial_sample_size,
                    allow_short_trial=self.allow_short_trial,
                )
            )
            logger.info(
                f"Trial mode: selected {len(selected_trial_indices)} message indices: "
                f"{sorted(selected_trial_indices)[:20]}{'...' if len(selected_trial_indices) > 20 else ''}"
            )
            if self.last_trial_selection_warning:
                logger.warning(self.last_trial_selection_warning)

        for message_index, email_msg, raw_bytes in self.reader.stream_messages():
            self.message_count += 1

            if (
                selected_trial_indices is not None
                and message_index not in selected_trial_indices
            ):
                continue
            
            # Skip if before checkpoint
            if message_index < self.checkpoint_message_index:
                continue
            
            # Check fingerprint match for resume
            fingerprint = self._compute_fingerprint(email_msg)
            if (message_index == self.checkpoint_message_index and 
                fingerprint != self.checkpoint_fingerprint):
                logger.warning(f"Fingerprint mismatch at index {message_index}")
            
            # Process the message
            self._process_single_message(
                message_index,
                email_msg,
                raw_bytes,
                fingerprint
            )
            
            # Checkpoint periodically
            if self.message_count % self.checkpoint_interval == 0:
                self._flush_checkpoint()
    
    def _process_single_message(
        self,
        message_index: int,
        email_msg: EmailMessage,
        raw_bytes: bytes,
        fingerprint: str
    ):
        """Process a single message.
        
        Args:
            message_index: Index in MBOX
            email_msg: Parsed EmailMessage
            raw_bytes: Raw message bytes
            fingerprint: Computed fingerprint
        """
        self.results["processed"] += 1
        
        # Extract metadata
        msg_id_header = email_msg.get("Message-ID", "")
        date = email_msg.get("Date", "")
        from_addr = email_msg.get("From", "")
        to_addr = email_msg.get("To", "")
        subject = email_msg.get("Subject", "")
        
        if self.verbose:
            logger.debug(
                f"Processing message {message_index}: "
                f"Message-ID={msg_id_header}, Subject={subject}"
            )
        
        # Create message record
        message_db_id = self.store.create_message(
            run_id=self.run_id,
            file_id=self.file_id,
            message_index=message_index,
            message_id_header=msg_id_header,
            fingerprint=fingerprint
        )
        
        # Check if already processed (idempotent skip)
        if msg_id_header and msg_id_header in self.seen_message_ids:
            self.store.update_message_status(
                message_db_id,
                MessageStatus.SKIPPED_DUPLICATE,
                reason="Previously processed"
            )
            self.results["skipped_duplicate"] += 1
            self._add_csv_row(msg_id_header, "skipped_duplicate", "Previously processed")
            return
        
        # Forwarded detection
        is_forwarded = is_forwarded_from_gmail(
            email_msg,
            self.gmail_addr,
            self.hey_addr,
            mode=self.forwarded_mode
        )
        
        # Count predicates for audit
        from hey_to_gmail.forwarded_filter import _count_predicates
        predicates_matched = _count_predicates(email_msg, self.gmail_addr, self.hey_addr)
        self.audit_counter.record(
            strict_match=is_forwarded and self.forwarded_mode == "strict",
            predicates_matched=predicates_matched
        )
        
        if is_forwarded:
            self.store.update_message_status(
                message_db_id,
                MessageStatus.SKIPPED_FORWARDED,
                reason=f"Forwarded from Gmail ({self.forwarded_mode} mode)"
            )
            self.results["skipped_forwarded"] += 1
            self._add_csv_row(msg_id_header, "skipped_forwarded", f"Forwarded ({self.forwarded_mode})")
            return
        
        # Duplicate detection
        dedupe_key = dedupe_key_for_message(email_msg)
        
        if dedupe_key.kind == "message_id":
            if dedupe_key.value in self.seen_message_ids:
                self.store.update_message_status(
                    message_db_id,
                    MessageStatus.SKIPPED_DUPLICATE,
                    reason="Duplicate Message-ID"
                )
                self.results["skipped_duplicate"] += 1
                self._add_csv_row(msg_id_header, "skipped_duplicate", "Duplicate Message-ID")
                return
        
        # Remote dedupe (if enabled)
        if self.enable_remote_dedupe and self.gmail_client and msg_id_header:
            normalized_id = msg_id_header.strip("<>")
            if self.gmail_client.message_exists_by_rfc822msgid(normalized_id):
                self.store.update_message_status(
                    message_db_id,
                    MessageStatus.SKIPPED_DUPLICATE,
                    reason="Exists in Gmail"
                )
                self.results["skipped_duplicate"] += 1
                self._add_csv_row(msg_id_header, "skipped_duplicate", "Exists in Gmail")
                return
        
        # Add to seen set for local dedupe
        if dedupe_key.kind == "message_id":
            self.seen_message_ids.add(dedupe_key.value)
        
        # Execute import (if not dry-run)
        if self.mode == "execute" and self.gmail_client:
            self._import_message(
                message_db_id,
                email_msg,
                raw_bytes,
                msg_id_header
            )
        else:
            # Dry-run: mark as would-be imported
            self._add_csv_row(msg_id_header, "would_import", "Dry run")
    
    def _import_message(
        self,
        message_db_id: int,
        email_msg: EmailMessage,
        raw_bytes: bytes,
        msg_id_header: str
    ):
        """Import a single message to Gmail.
        
        Args:
            message_db_id: Database ID for the message
            email_msg: Parsed EmailMessage
            raw_bytes: Raw message bytes
            msg_id_header: Message-ID header value
        """
        try:
            # Get label ID
            label_id = self._get_label_id()
            label_ids = [label_id] if label_id else []
            
            # Import message
            gmail_msg_id = self.gmail_client.import_message(
                raw_bytes,
                label_ids=label_ids if label_ids else None
            )
            
            if gmail_msg_id:
                self.store.update_message_status(
                    message_db_id,
                    MessageStatus.IMPORTED
                )
                self.results["imported"] += 1
                self._add_csv_row(msg_id_header, "imported", None)
            else:
                # Import succeeded but no ID returned (shouldn't happen)
                self.store.update_message_status(
                    message_db_id,
                    MessageStatus.IMPORTED_UNLABELED
                )
                self.results["imported_unlabeled"] += 1
                self._add_csv_row(msg_id_header, "imported_unlabeled", "No Gmail ID")
                
        except Exception as e:
            logger.error(f"Import failed: {e}")
            self.store.update_message_status(
                message_db_id,
                MessageStatus.FAILED,
                reason=str(e)
            )
            self.results["failed"] += 1
            self._add_csv_row(msg_id_header, "failed", str(e))
    
    def _get_label_id(self) -> Optional[str]:
        """Get or create label ID.
        
        Returns:
            Label ID or None if not available
        """
        if self._label_id is not None:
            return self._label_id
        
        if not self.label_manager:
            return None
        
        try:
            self._label_id = self.label_manager.ensure_label(self.label_name)
            return self._label_id
        except Exception as e:
            logger.error(f"Failed to get label: {e}")
            return None
    
    def _compute_fingerprint(self, email_msg: EmailMessage) -> str:
        """Compute fingerprint for message.
        
        Args:
            email_msg: Email message
            
        Returns:
            SHA-256 hex digest fingerprint
        """
        # Use Message-ID if available
        msg_id = email_msg.get("Message-ID", "")
        if msg_id:
            return hashlib.sha256(msg_id.encode()).hexdigest()
        
        # Fall back to content hash
        try:
            content = email_msg.as_bytes()
            return hashlib.sha256(content).hexdigest()
        except Exception:
            return hashlib.sha256(b"").hexdigest()
    
    def _flush_checkpoint(self):
        """Write current checkpoint to database."""
        if self.trial_sample_size is not None:
            return

        if self.file_id is None or self.message_count == 0:
            return
        
        file_size, file_mtime = self.reader.get_file_metadata()
        
        # Get last actually processed message index and fingerprint
        conn = self.store._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT message_index, fingerprint FROM messages WHERE file_id = ? ORDER BY message_index DESC LIMIT 1",
            (self.file_id,)
        )
        row = cursor.fetchone()
        conn.close()

        if row is None:
            return

        message_index = row["message_index"]
        fingerprint = row["fingerprint"]
        
        self.store.upsert_checkpoint(
            file_id=self.file_id,
            file_size=file_size,
            file_mtime=file_mtime,
            message_index=message_index,
            message_fingerprint=fingerprint
        )

        logger.debug(f"Checkpoint flushed at message {message_index}")
    
    def _add_csv_row(self, message_id: str, status: str, reason: Optional[str]):
        """Add a row to CSV report.
        
        Args:
            message_id: Message-ID header
            status: Processing status
            reason: Optional reason
        """
        self.csv_rows.append(
            build_report_row(
                message_id=message_id,
                status=status,
                reason=reason,
                trial_selected=self._trial_selected,
                trial_profile=self._trial_profile,
            )
        )
    
    def _finalize_run(self):
        """Finalize run and write reports."""
        # Update detection audit in results
        self.results["detection_audit"] = self.audit_counter.summary()
        
        # Log audit summary
        audit = self.results["detection_audit"]
        logger.info(
            f"Detection audit: {audit['strict_matches']} strict matches, "
            f"{audit['strict_plus_matches']} strict_plus matches, "
            f"{audit['total_processed']} total"
        )
        
        # Write CSV report
        self._write_csv_report()
        
        logger.info(
            f"Run complete: {self.results['processed']} processed, "
            f"{self.results['imported']} imported, "
            f"{self.results['skipped_forwarded']} forwarded skipped, "
            f"{self.results['skipped_duplicate']} duplicates skipped, "
            f"{self.results['failed']} failed"
        )
    
    def _write_csv_report(self):
        """Write CSV report to file."""
        with open(self.csv_path, "w", newline="") as f:
            if self.csv_rows:
                writer = csv.DictWriter(
                    f,
                    fieldnames=REPORT_FIELDNAMES
                )
                writer.writeheader()
                writer.writerows(self.csv_rows)
