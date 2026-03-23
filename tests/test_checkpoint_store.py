"""Tests for checkpoint store with file metadata validation and status machine."""
import sqlite3
from datetime import datetime
import pytest
from hey_to_gmail.checkpoint_store import CheckpointStore, MessageStatus


class TestCheckpointCompatibility:
    """Tests for checkpoint file metadata validation."""

    def test_checkpoint_requires_matching_size_and_mtime(self, tmp_path):
        """Checkpoint is only compatible when size and mtime match exactly."""
        store = CheckpointStore(tmp_path / "state.db")
        store.initialize()
        store.upsert_checkpoint(
            file_id=1,
            file_size=100,
            file_mtime=1000.0,
            message_index=3,
            message_fingerprint="abc"
        )
        
        # Matching metadata should be compatible
        assert store.is_checkpoint_compatible(
            file_id=1, file_size=100, file_mtime=1000.0
        )
        
        # Different size should be incompatible
        assert not store.is_checkpoint_compatible(
            file_id=1, file_size=200, file_mtime=1000.0
        )
        
        # Different mtime should be incompatible
        assert not store.is_checkpoint_compatible(
            file_id=1, file_size=100, file_mtime=2000.0
        )
        
        # Both different should be incompatible
        assert not store.is_checkpoint_compatible(
            file_id=1, file_size=200, file_mtime=2000.0
        )


class TestStatusTransitions:
    """Tests for message status machine transitions."""

    def test_status_transition_imported_unlabeled_recovery(self, tmp_path):
        """imported_unlabeled can transition to imported or failed."""
        store = CheckpointStore(tmp_path / "state.db")
        store.initialize()
        
        # Create a run and file first
        run_id = store.create_run(mode="test", total_files=1)
        file_id = store.create_file(
            run_id=run_id,
            file_path="/test.mbox",
            file_size=100,
            file_mtime=1000.0,
            ordinal=0
        )
        
        # Create a message in pending state
        message_id = store.create_message(
            run_id=run_id,
            file_id=file_id,
            message_index=0,
            message_id_header="<test@example.com>",
            fingerprint="abc123"
        )
        
        # pending -> imported_unlabeled
        store.update_message_status(message_id, MessageStatus.IMPORTED_UNLABELED)
        status = store.get_message_status(message_id)
        assert status == MessageStatus.IMPORTED_UNLABELED
        
        # imported_unlabeled -> imported
        store.update_message_status(message_id, MessageStatus.IMPORTED)
        status = store.get_message_status(message_id)
        assert status == MessageStatus.IMPORTED
        
        # Create another message for the failed transition
        message_id2 = store.create_message(
            run_id=run_id,
            file_id=file_id,
            message_index=1,
            message_id_header="<test2@example.com>",
            fingerprint="def456"
        )
        store.update_message_status(message_id2, MessageStatus.IMPORTED_UNLABELED)
        
        # imported_unlabeled -> failed
        store.update_message_status(
            message_id2, MessageStatus.FAILED, reason="Label retry exhausted"
        )
        status = store.get_message_status(message_id2)
        assert status == MessageStatus.FAILED

    def test_all_valid_transitions_from_pending(self, tmp_path):
        """pending can transition to all terminal states."""
        store = CheckpointStore(tmp_path / "state.db")
        store.initialize()
        
        run_id = store.create_run(mode="test", total_files=1)
        file_id = store.create_file(
            run_id=run_id,
            file_path="/test.mbox",
            file_size=100,
            file_mtime=1000.0,
            ordinal=0
        )
        
        transitions = [
            (MessageStatus.SKIPPED_FORWARDED, "Forwarded message"),
            (MessageStatus.SKIPPED_DUPLICATE, "Duplicate message"),
            (MessageStatus.IMPORTED, None),
            (MessageStatus.IMPORTED_UNLABELED, None),
            (MessageStatus.FAILED, "Import failed"),
        ]
        
        for idx, (new_status, reason) in enumerate(transitions):
            message_id = store.create_message(
                run_id=run_id,
                file_id=file_id,
                message_index=idx,
                message_id_header=f"<test{idx}@example.com>",
                fingerprint=f"hash{idx}"
            )
            
            store.update_message_status(message_id, new_status, reason=reason)
            status = store.get_message_status(message_id)
            assert status == new_status, f"Failed transition to {new_status}"


class TestSchema:
    """Tests for database schema requirements."""

    def test_messages_schema_has_required_columns(self, tmp_path):
        """messages table must have reason, retries, and hash_version columns."""
        store = CheckpointStore(tmp_path / "state.db")
        store.initialize()
        
        run_id = store.create_run(mode="test", total_files=1)
        file_id = store.create_file(
            run_id=run_id,
            file_path="/test.mbox",
            file_size=100,
            file_mtime=1000.0,
            ordinal=0
        )
        
        message_id = store.create_message(
            run_id=run_id,
            file_id=file_id,
            message_index=0,
            message_id_header="<test@example.com>",
            fingerprint="abc123"
        )
        
        # Update with all the required fields
        store.update_message_status(
            message_id,
            MessageStatus.FAILED,
            reason="Import error",
            import_retries=3,
            label_retries=2,
            hash_version=1
        )
        
        # Verify columns exist by querying them
        conn = sqlite3.connect(str(tmp_path / "state.db"))
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT reason, import_retries, label_retries, hash_version 
            FROM messages 
            WHERE id = ?
        """, (message_id,))
        
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "Import error"
        assert row[1] == 3
        assert row[2] == 2
        assert row[3] == 1
        
        conn.close()

    def test_runs_table_schema(self, tmp_path):
        """runs table must have correct columns."""
        store = CheckpointStore(tmp_path / "state.db")
        store.initialize()
        
        run_id = store.create_run(mode="strict", total_files=42)
        
        conn = sqlite3.connect(str(tmp_path / "state.db"))
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, started_at, mode, total_files 
            FROM runs 
            WHERE id = ?
        """, (run_id,))
        
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == run_id
        assert isinstance(row[1], str)  # started_at timestamp
        assert row[2] == "strict"
        assert row[3] == 42
        
        conn.close()

    def test_files_table_schema(self, tmp_path):
        """files table must track path, size, mtime, and ordinal."""
        store = CheckpointStore(tmp_path / "state.db")
        store.initialize()
        
        run_id = store.create_run(mode="test", total_files=1)
        file_id = store.create_file(
            run_id=run_id,
            file_path="/path/to/test.mbox",
            file_size=2048,
            file_mtime=1234567890.5,
            ordinal=5
        )
        
        conn = sqlite3.connect(str(tmp_path / "state.db"))
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, run_id, file_path, file_size, file_mtime, ordinal 
            FROM files 
            WHERE id = ?
        """, (file_id,))
        
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == file_id
        assert row[1] == run_id
        assert row[2] == "/path/to/test.mbox"
        assert row[3] == 2048
        assert row[4] == 1234567890.5
        assert row[5] == 5
        
        conn.close()


class TestStatusTransitionsInvalid:
    """Tests for invalid status transitions that should be rejected."""

    @pytest.fixture
    def store_with_message(self, tmp_path):
        """Create a store with a pending message for testing transitions."""
        store = CheckpointStore(tmp_path / "state.db")
        store.initialize()
        run_id = store.create_run(mode="test", total_files=1)
        file_id = store.create_file(
            run_id=run_id,
            file_path="/test.mbox",
            file_size=100,
            file_mtime=1000.0,
            ordinal=0
        )
        message_id = store.create_message(
            run_id=run_id,
            file_id=file_id,
            message_index=0,
            message_id_header="<test@example.com>",
            fingerprint="abc123"
        )
        return store, message_id

    def test_invalid_transition_imported_to_pending(self, store_with_message):
        """imported -> pending should be rejected."""
        store, message_id = store_with_message
        store.update_message_status(message_id, MessageStatus.IMPORTED)
        
        with pytest.raises(ValueError) as exc_info:
            store.update_message_status(message_id, MessageStatus.PENDING)
        assert "Invalid status transition" in str(exc_info.value)
        assert "imported -> pending" in str(exc_info.value)

    def test_invalid_transition_skipped_to_anything(self, store_with_message):
        """skipped states should not transition to any other state."""
        store, message_id = store_with_message
        store.update_message_status(message_id, MessageStatus.SKIPPED_FORWARDED)
        
        with pytest.raises(ValueError) as exc_info:
            store.update_message_status(message_id, MessageStatus.IMPORTED)
        assert "Invalid status transition" in str(exc_info.value)

    def test_invalid_transition_failed_to_anything(self, store_with_message):
        """failed should not transition to any other state."""
        store, message_id = store_with_message
        store.update_message_status(message_id, MessageStatus.FAILED, reason="Error")
        
        with pytest.raises(ValueError) as exc_info:
            store.update_message_status(message_id, MessageStatus.IMPORTED)
        assert "Invalid status transition" in str(exc_info.value)

    def test_invalid_transition_imported_to_unlabeled(self, store_with_message):
        """imported -> imported_unlabeled should be rejected."""
        store, message_id = store_with_message
        store.update_message_status(message_id, MessageStatus.IMPORTED)
        
        with pytest.raises(ValueError) as exc_info:
            store.update_message_status(message_id, MessageStatus.IMPORTED_UNLABELED)
        assert "Invalid status transition" in str(exc_info.value)

    def test_invalid_transition_unlabeled_to_pending(self, store_with_message):
        """imported_unlabeled -> pending should be rejected."""
        store, message_id = store_with_message
        store.update_message_status(message_id, MessageStatus.IMPORTED_UNLABELED)
        
        with pytest.raises(ValueError) as exc_info:
            store.update_message_status(message_id, MessageStatus.PENDING)
        assert "Invalid status transition" in str(exc_info.value)

    def test_invalid_transition_unlabeled_to_skipped(self, store_with_message):
        """imported_unlabeled -> skipped states should be rejected."""
        store, message_id = store_with_message
        store.update_message_status(message_id, MessageStatus.IMPORTED_UNLABELED)
        
        with pytest.raises(ValueError) as exc_info:
            store.update_message_status(message_id, MessageStatus.SKIPPED_DUPLICATE)
        assert "Invalid status transition" in str(exc_info.value)


class TestStatusEnum:
    """Tests for the status enum values."""

    def test_all_status_values_exist(self):
        """All required status enum values must be defined."""
        expected_statuses = [
            "pending",
            "imported",
            "imported_unlabeled",
            "skipped_forwarded",
            "skipped_duplicate",
            "failed",
        ]
        
        for status in expected_statuses:
            assert hasattr(MessageStatus, status.upper()), f"Missing status: {status}"
            enum_value = getattr(MessageStatus, status.upper())
            assert enum_value.value == status
