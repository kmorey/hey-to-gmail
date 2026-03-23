"""Tests for importer execute mode functionality."""
import mailbox
import pytest
from pathlib import Path
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

from hey_to_gmail.checkpoint_store import CheckpointStore, MessageStatus
from hey_to_gmail.importer import MboxImporter


@pytest.fixture
def sample_mbox_file(tmp_path):
    """Create a sample MBOX file with test messages."""
    mbox_path = tmp_path / "test.mbox"
    
    # Message 1: Normal message with Message-ID
    msg1 = EmailMessage()
    msg1["From"] = "sender1@example.com"
    msg1["To"] = "recipient@example.com"
    msg1["Subject"] = "Test message 1"
    msg1["Message-ID"] = "<msg1-abc@example.com>"
    msg1["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg1.set_content("Body of message 1")
    
    # Message 2: Another normal message
    msg2 = EmailMessage()
    msg2["From"] = "sender2@example.com"
    msg2["To"] = "recipient@example.com"
    msg2["Subject"] = "Test message 2"
    msg2["Message-ID"] = "<msg2-def@example.com>"
    msg2["Date"] = "Mon, 02 Jan 2024 12:00:00 +0000"
    msg2.set_content("Body of message 2")
    
    # Write MBOX format
    with open(mbox_path, "wb") as f:
        f.write(b"From sender1@example.com Mon Jan 01 12:00:00 2024\n")
        f.write(msg1.as_bytes())
        f.write(b"\n\n")
        f.write(b"From sender2@example.com Mon Jan 02 12:00:00 2024\n")
        f.write(msg2.as_bytes())
        f.write(b"\n\n")
    
    return mbox_path


@pytest.fixture
def mocked_gmail_client():
    """Create a mocked Gmail client."""
    client = MagicMock()
    client.import_message.return_value = "gmail_msg_id_123"
    client.message_exists_by_rfc822msgid.return_value = False
    return client


@pytest.fixture
def mocked_label_manager():
    """Create a mocked label manager."""
    manager = MagicMock()
    manager.ensure_label.return_value = "label_123"
    return manager


@pytest.fixture
def curated_trial_execute_mbox(tmp_path):
    """Create MBOX fixture with deterministic curated trial candidates."""

    def _message(
        *,
        message_id,
        subject,
        add_forwarded_headers=False,
        add_attachment=False,
        gmail_origin=False,
        plain_text_only=False,
    ):
        msg = EmailMessage()
        msg["From"] = "sender@example.com"
        msg["To"] = "recipient@example.com"
        msg["Subject"] = subject
        msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
        msg["Message-ID"] = message_id

        if add_forwarded_headers:
            msg["X-Forwarded-For"] = "gmail@example.com hey@example.com"
            msg["X-Forwarded-To"] = "hey@example.com"
            msg["Delivered-To"] = "gmail@example.com"

        if gmail_origin:
            msg["X-Gm-Message-State"] = "state"
            msg["Received"] = "from mail-lf1-f66.google.com by gmail"

        if add_attachment:
            msg.set_content("Plain text with attachment")
            msg.add_attachment(
                b"attachment-bytes",
                maintype="application",
                subtype="octet-stream",
                filename="file.bin",
            )
        elif plain_text_only:
            msg.set_content("Plain text body")
        else:
            msg.set_content("<html><body>html only</body></html>", subtype="html")
        return msg

    mbox_path = tmp_path / "curated-trial-execute.mbox"
    messages = [
        _message(
            message_id="<fwd-0@example.com>",
            subject="Forwarded candidate",
            add_forwarded_headers=True,
        ),
        _message(
            message_id="<att-1@example.com>",
            subject="Attachment candidate",
            add_attachment=True,
        ),
        _message(
            message_id="<gm-2@mail.gmail.com>",
            subject="Gmail origin candidate",
            gmail_origin=True,
            plain_text_only=True,
        ),
        _message(
            message_id="<pt-3@example.com>",
            subject="Plain text candidate",
            plain_text_only=True,
        ),
        _message(
            message_id="<fb-4@example.com>",
            subject="Fallback candidate",
        ),
        _message(
            message_id="<fb-5@example.com>",
            subject="Extra fallback candidate",
        ),
    ]

    mbox = mailbox.mbox(mbox_path)
    try:
        for msg in messages:
            mbox.add(msg)
        mbox.flush()
    finally:
        mbox.close()

    return mbox_path


@pytest.fixture
def sparse_trial_execute_mbox(tmp_path):
    """Create MBOX fixture with enough messages for sparse trial selection."""

    mbox_path = tmp_path / "sparse-trial-execute.mbox"
    mbox = mailbox.mbox(mbox_path)
    try:
        for index in range(25):
            msg = EmailMessage()
            msg["From"] = "sender@example.com"
            msg["To"] = "recipient@example.com"
            msg["Subject"] = f"Sparse test message {index}"
            msg["Message-ID"] = f"<sparse-{index}@example.com>"
            msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
            msg.set_content(f"Body {index}")
            mbox.add(msg)
        mbox.flush()
    finally:
        mbox.close()

    return mbox_path


class TestExecuteRecoversImportedUnlabeled:
    """Tests for recovery of imported_unlabeled messages."""
    
    def test_execute_recovers_imported_unlabeled_to_imported(
        self, tmp_path, mocked_gmail_client, sample_mbox_file
    ):
        """Messages in imported_unlabeled state should be retried for labeling."""
        db_path = tmp_path / "test.db"
        csv_path = tmp_path / "report.csv"
        
        store = CheckpointStore(db_path)
        store.initialize()
        
        # Setup: Create a previous run with an imported_unlabeled message
        run_id = store.create_run(mode="execute", total_files=1)
        file_id = store.create_file(
            run_id=run_id,
            file_path=str(sample_mbox_file),
            file_size=sample_mbox_file.stat().st_size,
            file_mtime=sample_mbox_file.stat().st_mtime,
            ordinal=0
        )
        
        # Create message in imported_unlabeled state
        message_id = store.create_message(
            run_id=run_id,
            file_id=file_id,
            message_index=0,
            message_id_header="<msg1-abc@example.com>",
            fingerprint="hash123"
        )
        store.update_message_status(
            message_id, 
            MessageStatus.IMPORTED_UNLABELED,
            label_retries=0
        )
        
        # Mock label manager to succeed this time
        mock_label_manager = MagicMock()
        mock_label_manager.ensure_label.return_value = "label_123"
        
        # Mock Gmail client to return message that needs labeling
        # _apply_label_to_message uses list() to search, not get()
        mocked_gmail_client.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            "messages": [{"id": "gmail_msg_id_123"}]
        }
        mocked_gmail_client.users.return_value.messages.return_value.modify.return_value.execute.return_value = {
            "id": "gmail_msg_id_123",
            "labelIds": ["INBOX", "label_123"]
        }
        
        importer = MboxImporter(
            mbox_path=sample_mbox_file,
            db_path=db_path,
            csv_path=csv_path,
            mode="execute",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
            gmail_client=mocked_gmail_client,
            label_manager=mock_label_manager
        )
        
        result = importer.run()
        
        # Verify the imported_unlabeled message was recovered
        status = store.get_message_status(message_id)
        assert status == MessageStatus.IMPORTED
        
        # Verify modify was called to add label
        mocked_gmail_client.users.return_value.messages.return_value.modify.assert_called()


class TestExecuteLabelRetryExhaustion:
    """Tests for label retry exhaustion handling."""
    
    def test_execute_marks_failed_on_label_retry_exhaustion(
        self, tmp_path, mocked_gmail_client, sample_mbox_file
    ):
        """Messages exceeding max label retries should be marked failed."""
        db_path = tmp_path / "test.db"
        csv_path = tmp_path / "report.csv"
        
        store = CheckpointStore(db_path)
        store.initialize()
        
        # Setup: Create a previous run with an imported_unlabeled message at max retries
        run_id = store.create_run(mode="execute", total_files=1)
        file_id = store.create_file(
            run_id=run_id,
            file_path=str(sample_mbox_file),
            file_size=sample_mbox_file.stat().st_size,
            file_mtime=sample_mbox_file.stat().st_mtime,
            ordinal=0
        )
        
        # Create message at max label retries (3)
        message_id = store.create_message(
            run_id=run_id,
            file_id=file_id,
            message_index=0,
            message_id_header="<msg1-abc@example.com>",
            fingerprint="hash123"
        )
        store.update_message_status(
            message_id,
            MessageStatus.IMPORTED_UNLABELED,
            label_retries=3  # Max retries reached
        )
        
        importer = MboxImporter(
            mbox_path=sample_mbox_file,
            db_path=db_path,
            csv_path=csv_path,
            mode="execute",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
            gmail_client=mocked_gmail_client,
            max_label_retries=3
        )
        
        result = importer.run()
        
        # Verify the message was marked failed
        status = store.get_message_status(message_id)
        assert status == MessageStatus.FAILED
        
        # Verify reason was recorded
        conn = store._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT reason FROM messages WHERE id = ?", (message_id,))
        row = cursor.fetchone()
        assert "retry" in row["reason"].lower() or "exhausted" in row["reason"].lower()
        conn.close()


class TestExecuteModeImport:
    """Tests for execute mode import functionality."""
    
    def test_execute_imports_messages(self, tmp_path, mocked_gmail_client, sample_mbox_file):
        """Execute mode should import messages to Gmail."""
        db_path = tmp_path / "test.db"
        csv_path = tmp_path / "report.csv"
        
        mock_label_manager = MagicMock()
        mock_label_manager.ensure_label.return_value = "label_123"
        
        importer = MboxImporter(
            mbox_path=sample_mbox_file,
            db_path=db_path,
            csv_path=csv_path,
            mode="execute",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
            gmail_client=mocked_gmail_client,
            label_manager=mock_label_manager,
            label_name="HEY-Imported"
        )
        
        result = importer.run()
        
        # Verify import was called
        assert mocked_gmail_client.import_message.call_count == 2
        
        # Verify correct label was used
        mock_label_manager.ensure_label.assert_called_with("HEY-Imported")
    
    def test_execute_creates_label_if_not_exists(self, tmp_path, mocked_gmail_client, sample_mbox_file):
        """Execute mode should create label if it doesn't exist."""
        db_path = tmp_path / "test.db"
        csv_path = tmp_path / "report.csv"
        
        mock_label_manager = MagicMock()
        mock_label_manager.ensure_label.return_value = "new_label_id"
        
        importer = MboxImporter(
            mbox_path=sample_mbox_file,
            db_path=db_path,
            csv_path=csv_path,
            mode="execute",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
            gmail_client=mocked_gmail_client,
            label_manager=mock_label_manager,
            label_name="HEY-Imported"
        )
        
        importer.run()
        
        # Verify ensure_label was called
        mock_label_manager.ensure_label.assert_called_once_with("HEY-Imported")
    
    def test_execute_skips_imported_messages(self, tmp_path, mocked_gmail_client, sample_mbox_file):
        """Execute mode should skip already imported messages."""
        db_path = tmp_path / "test.db"
        csv_path = tmp_path / "report.csv"
        
        store = CheckpointStore(db_path)
        store.initialize()
        
        # Setup: Create a previous run with an already imported message
        run_id = store.create_run(mode="execute", total_files=1)
        file_id = store.create_file(
            run_id=run_id,
            file_path=str(sample_mbox_file),
            file_size=sample_mbox_file.stat().st_size,
            file_mtime=sample_mbox_file.stat().st_mtime,
            ordinal=0
        )
        
        # Create message already imported
        message_id = store.create_message(
            run_id=run_id,
            file_id=file_id,
            message_index=0,
            message_id_header="<msg1-abc@example.com>",
            fingerprint="hash123"
        )
        store.update_message_status(message_id, MessageStatus.IMPORTED)
        
        mock_label_manager = MagicMock()
        mock_label_manager.ensure_label.return_value = "label_123"
        
        importer = MboxImporter(
            mbox_path=sample_mbox_file,
            db_path=db_path,
            csv_path=csv_path,
            mode="execute",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
            gmail_client=mocked_gmail_client,
            label_manager=mock_label_manager
        )
        
        result = importer.run()
        
        # Should only import the second message (index 1)
        assert mocked_gmail_client.import_message.call_count == 1

    def test_execute_trial_processes_only_selected_indices(
        self, tmp_path, mocked_gmail_client, curated_trial_execute_mbox
    ):
        """Execute trial mode should process only selected trial indices."""
        db_path = tmp_path / "test.db"
        csv_path = tmp_path / "report.csv"

        mock_label_manager = MagicMock()
        mock_label_manager.ensure_label.return_value = "label_123"

        importer = MboxImporter(
            mbox_path=curated_trial_execute_mbox,
            db_path=db_path,
            csv_path=csv_path,
            mode="execute",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
            gmail_client=mocked_gmail_client,
            label_manager=mock_label_manager,
            label_name="Hey.com",
            trial_sample_size=5,
        )

        result = importer.run()

        assert result["processed"] == 5
        assert mocked_gmail_client.import_message.call_count == 4

    def test_execute_trial_uses_standard_label_behavior_not_trial_specific(
        self, tmp_path, mocked_gmail_client, curated_trial_execute_mbox
    ):
        """Execute trial mode should keep normal configured label behavior."""
        db_path = tmp_path / "test.db"
        csv_path = tmp_path / "report.csv"

        mock_label_manager = MagicMock()
        mock_label_manager.ensure_label.return_value = "label_hey"

        importer = MboxImporter(
            mbox_path=curated_trial_execute_mbox,
            db_path=db_path,
            csv_path=csv_path,
            mode="execute",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
            gmail_client=mocked_gmail_client,
            label_manager=mock_label_manager,
            label_name="Hey.com",
            trial_sample_size=5,
        )

        importer.run()

        mock_label_manager.ensure_label.assert_called_with("Hey.com")
        assert not any(
            call.args[0] != "Hey.com"
            for call in mock_label_manager.ensure_label.call_args_list
        )

    def test_execute_after_trial_processes_remaining_messages(
        self, tmp_path, mocked_gmail_client, curated_trial_execute_mbox
    ):
        """Full execute after trial should process messages skipped during trial."""
        db_path = tmp_path / "test.db"
        trial_csv_path = tmp_path / "trial-report.csv"
        full_csv_path = tmp_path / "full-report.csv"

        mock_label_manager = MagicMock()
        mock_label_manager.ensure_label.return_value = "label_123"

        trial_importer = MboxImporter(
            mbox_path=curated_trial_execute_mbox,
            db_path=db_path,
            csv_path=trial_csv_path,
            mode="execute",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
            gmail_client=mocked_gmail_client,
            label_manager=mock_label_manager,
            trial_sample_size=2,
        )

        trial_result = trial_importer.run()
        assert trial_result["processed"] == 2

        mocked_gmail_client.import_message.reset_mock()

        full_importer = MboxImporter(
            mbox_path=curated_trial_execute_mbox,
            db_path=db_path,
            csv_path=full_csv_path,
            mode="execute",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
            gmail_client=mocked_gmail_client,
            label_manager=mock_label_manager,
        )

        full_result = full_importer.run()

        # There are five non-forwarded messages in this fixture.
        # After a trial of two selected indices, a full run must still process
        # at least the four remaining importable messages.
        assert full_result["processed"] >= 5
        assert mocked_gmail_client.import_message.call_count >= 4

    def test_execute_sparse_trial_does_not_advance_checkpoint_unsafely(
        self, tmp_path, mocked_gmail_client, sparse_trial_execute_mbox
    ):
        """Sparse trial selection must not cause full runs to skip unprocessed ranges."""
        db_path = tmp_path / "test.db"
        trial_csv_path = tmp_path / "trial-report.csv"
        full_csv_path = tmp_path / "full-report.csv"

        mock_label_manager = MagicMock()
        mock_label_manager.ensure_label.return_value = "label_123"

        trial_importer = MboxImporter(
            mbox_path=sparse_trial_execute_mbox,
            db_path=db_path,
            csv_path=trial_csv_path,
            mode="execute",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
            gmail_client=mocked_gmail_client,
            label_manager=mock_label_manager,
            trial_sample_size=2,
        )

        with patch.object(MboxImporter, "select_trial_indices", return_value=[1, 20]):
            trial_result = trial_importer.run()
        assert trial_result["processed"] == 2

        mocked_gmail_client.import_message.reset_mock()

        full_importer = MboxImporter(
            mbox_path=sparse_trial_execute_mbox,
            db_path=db_path,
            csv_path=full_csv_path,
            mode="execute",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
            gmail_client=mocked_gmail_client,
            label_manager=mock_label_manager,
        )

        full_result = full_importer.run()

        assert full_result["processed"] == 25
        assert mocked_gmail_client.import_message.call_count == 23


class TestRemoteDedupe:
    """Tests for remote deduplication."""
    
    def test_remote_dedupe_queries_gmail(self, tmp_path, mocked_gmail_client, sample_mbox_file):
        """Remote dedupe should query Gmail for existing messages."""
        db_path = tmp_path / "test.db"
        csv_path = tmp_path / "report.csv"
        
        # Mock that message already exists in Gmail
        mocked_gmail_client.message_exists_by_rfc822msgid.return_value = True
        
        mock_label_manager = MagicMock()
        mock_label_manager.ensure_label.return_value = "label_123"
        
        importer = MboxImporter(
            mbox_path=sample_mbox_file,
            db_path=db_path,
            csv_path=csv_path,
            mode="execute",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
            gmail_client=mocked_gmail_client,
            label_manager=mock_label_manager,
            enable_remote_dedupe=True
        )
        
        result = importer.run()
        
        # Verify remote query was made
        assert mocked_gmail_client.message_exists_by_rfc822msgid.call_count > 0
        
        # Messages should be marked as duplicates
        assert result["skipped_duplicate"] == 2
        
        # Import should not be called for duplicates
        assert mocked_gmail_client.import_message.call_count == 0


class TestImportWithLabelIds:
    """Tests for import with label IDs."""
    
    def test_import_passes_label_ids(self, tmp_path, mocked_gmail_client, sample_mbox_file):
        """Import should pass labelIds to Gmail API."""
        db_path = tmp_path / "test.db"
        csv_path = tmp_path / "report.csv"
        
        mock_label_manager = MagicMock()
        mock_label_manager.ensure_label.return_value = "label_123"
        
        importer = MboxImporter(
            mbox_path=sample_mbox_file,
            db_path=db_path,
            csv_path=csv_path,
            mode="execute",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
            gmail_client=mocked_gmail_client,
            label_manager=mock_label_manager,
            label_name="HEY-Imported"
        )
        
        importer.run()
        
        # Verify import_message was called with label_ids
        call_args = mocked_gmail_client.import_message.call_args
        assert "label_ids" in call_args.kwargs
        assert "label_123" in call_args.kwargs["label_ids"]

    def test_non_trial_report_rows_include_default_trial_metadata(
        self, tmp_path, mocked_gmail_client, sample_mbox_file
    ):
        """Non-trial execute reports should include default trial metadata values."""
        db_path = tmp_path / "test.db"
        csv_path = tmp_path / "report.csv"

        mock_label_manager = MagicMock()
        mock_label_manager.ensure_label.return_value = "label_123"

        importer = MboxImporter(
            mbox_path=sample_mbox_file,
            db_path=db_path,
            csv_path=csv_path,
            mode="execute",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
            gmail_client=mocked_gmail_client,
            label_manager=mock_label_manager,
        )

        importer.run()

        import csv

        with open(csv_path, "r") as f:
            rows = list(csv.DictReader(f))

        assert rows
        assert all(row["trial_selected"] == "false" for row in rows)
        assert all(row["trial_profile"] == "" for row in rows)
