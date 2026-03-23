"""Tests for importer dry-run mode functionality."""
import mailbox
import pytest
import logging
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
    
    # Message 2: Forwarded message
    msg2 = EmailMessage()
    msg2["From"] = "sender2@example.com"
    msg2["To"] = "hey@example.com"
    msg2["Subject"] = "Forwarded message"
    msg2["Message-ID"] = "<msg2-def@example.com>"
    msg2["Date"] = "Mon, 02 Jan 2024 12:00:00 +0000"
    msg2["X-Forwarded-For"] = "gmail@example.com hey@example.com"
    msg2["X-Forwarded-To"] = "hey@example.com"
    msg2["Delivered-To"] = "gmail@example.com"
    msg2.set_content("Forwarded body")
    
    # Message 3: Duplicate of message 1
    msg3 = EmailMessage()
    msg3["From"] = "sender1@example.com"
    msg3["To"] = "recipient@example.com"
    msg3["Subject"] = "Test message 1"
    msg3["Message-ID"] = "<msg1-abc@example.com>"
    msg3["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg3.set_content("Body of message 1")
    
    # Write MBOX format
    with open(mbox_path, "wb") as f:
        f.write(b"From sender1@example.com Mon Jan 01 12:00:00 2024\n")
        f.write(msg1.as_bytes())
        f.write(b"\n\n")
        f.write(b"From sender2@example.com Mon Jan 02 12:00:00 2024\n")
        f.write(msg2.as_bytes())
        f.write(b"\n\n")
        f.write(b"From sender3@example.com Mon Jan 03 12:00:00 2024\n")
        f.write(msg3.as_bytes())
        f.write(b"\n\n")
    
    return mbox_path


@pytest.fixture
def sample_db_path(tmp_path):
    """Create a database path for testing."""
    return tmp_path / "test.db"


@pytest.fixture
def sample_csv_path(tmp_path):
    """Create a CSV report path for testing."""
    return tmp_path / "report.csv"


class TestResumeWithMetadataMismatch:
    """Tests for resume behavior with file metadata validation."""
    
    def test_resume_with_metadata_mismatch_forces_rescan(self, tmp_path, sample_mbox_file):
        """If checkpoint file metadata doesn't match current file, force full rescan."""
        db_path = tmp_path / "test.db"
        csv_path = tmp_path / "report.csv"
        
        # First run - create checkpoint
        store = CheckpointStore(db_path)
        store.initialize()
        
        importer = MboxImporter(
            mbox_path=sample_mbox_file,
            db_path=db_path,
            csv_path=csv_path,
            mode="dry-run",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com"
        )
        
        # Run once to create checkpoint
        result1 = importer.run()
        assert result1["processed"] == 3
        
        # Verify checkpoint exists
        conn = store._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM checkpoints")
        checkpoint = cursor.fetchone()
        assert checkpoint is not None
        original_mtime = checkpoint["file_mtime"]
        conn.close()
        
        # Modify file to change metadata
        import time
        time.sleep(1.0)  # Ensure mtime changes (need >= 1s for some filesystems)
        original_stat = sample_mbox_file.stat()
        with open(sample_mbox_file, "a") as f:
            f.write("\n\nAdditional content to force metadata change\n")
        # Wait and verify file was actually modified
        time.sleep(0.5)
        new_stat = sample_mbox_file.stat()
        assert new_stat.st_mtime != original_stat.st_mtime, "File mtime should have changed"
        
        # Second run - should detect mismatch and rescan
        importer2 = MboxImporter(
            mbox_path=sample_mbox_file,
            db_path=db_path,
            csv_path=csv_path,
            mode="dry-run",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com"
        )
        
        result2 = importer2.run()
        # Should process all messages again (rescan forced)
        assert result2["processed"] == 3
        
        # Verify a new checkpoint was created (2 total checkpoints)
        conn = store._get_connection()
        cursor = conn.cursor()
        
        # Get both checkpoints ordered by creation time
        cursor.execute("SELECT file_id, file_size, file_mtime FROM checkpoints ORDER BY updated_at")
        checkpoints = cursor.fetchall()
        conn.close()
        
        # Should have 2 checkpoints (one from each run)
        assert len(checkpoints) == 2, f"Expected 2 checkpoints, got {len(checkpoints)}"
        
        # Second checkpoint should have different metadata than first
        first_checkpoint = checkpoints[0]
        second_checkpoint = checkpoints[1]
        
        assert first_checkpoint["file_id"] != second_checkpoint["file_id"], "Checkpoints should have different file_ids"
        # Either size or mtime should be different (file was modified)
        assert (
            first_checkpoint["file_size"] != second_checkpoint["file_size"] or
            first_checkpoint["file_mtime"] != second_checkpoint["file_mtime"]
        ), "Second checkpoint should have different file metadata"


class TestShutdownCheckpointFlush:
    """Tests for checkpoint flushing on shutdown."""
    
    def test_shutdown_flushes_checkpoint_below_interval(self, tmp_path, sample_mbox_file):
        """Checkpoint should be flushed on shutdown even if below interval."""
        db_path = tmp_path / "test.db"
        csv_path = tmp_path / "report.csv"
        
        store = CheckpointStore(db_path)
        store.initialize()
        
        importer = MboxImporter(
            mbox_path=sample_mbox_file,
            db_path=db_path,
            csv_path=csv_path,
            mode="dry-run",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
            checkpoint_interval=10  # High interval
        )
        
        # Process less than interval
        result = importer.run()
        
        # Verify checkpoint was still flushed on shutdown
        conn = store._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT message_index FROM checkpoints")
        checkpoint = cursor.fetchone()
        assert checkpoint is not None
        assert checkpoint["message_index"] == 2  # Last processed index
        conn.close()


class TestVerboseOutputSecurity:
    """Tests for verbose output security - never expose raw body."""
    
    def test_verbose_output_never_includes_raw_body(self, tmp_path, sample_mbox_file, caplog):
        """Verbose logging must never include raw message body."""
        db_path = tmp_path / "test.db"
        csv_path = tmp_path / "report.csv"
        
        # Set logging to capture verbose output
        with caplog.at_level(logging.DEBUG):
            importer = MboxImporter(
                mbox_path=sample_mbox_file,
                db_path=db_path,
                csv_path=csv_path,
                mode="dry-run",
                gmail_addr="gmail@example.com",
                hey_addr="hey@example.com",
                verbose=True
            )
            importer.run()
        
        # Check that raw body content is NOT in logs
        log_text = caplog.text
        assert "Body of message" not in log_text
        assert b"Body of message" not in log_text.encode()
        
        # But metadata should be present
        assert "msg1-abc@example.com" in log_text or "Processing" in log_text


class TestDryRunMode:
    """Tests for dry-run mode behavior."""
    
    def test_dry_run_does_not_import(self, tmp_path, sample_mbox_file):
        """Dry-run mode should not call Gmail API import."""
        db_path = tmp_path / "test.db"
        csv_path = tmp_path / "report.csv"
        
        mock_gmail = MagicMock()
        
        importer = MboxImporter(
            mbox_path=sample_mbox_file,
            db_path=db_path,
            csv_path=csv_path,
            mode="dry-run",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
            gmail_client=mock_gmail
        )
        
        result = importer.run()
        
        # Should not call import_message in dry-run mode
        mock_gmail.import_message.assert_not_called()
        assert result["imported"] == 0
    
    def test_dry_run_detects_forwarded(self, tmp_path, sample_mbox_file):
        """Dry-run should detect and skip forwarded messages."""
        db_path = tmp_path / "test.db"
        csv_path = tmp_path / "report.csv"
        
        importer = MboxImporter(
            mbox_path=sample_mbox_file,
            db_path=db_path,
            csv_path=csv_path,
            mode="dry-run",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
            forwarded_mode="strict"
        )
        
        result = importer.run()
        
        # Message 2 is forwarded
        assert result["skipped_forwarded"] == 1
    
    def test_dry_run_detects_duplicates(self, tmp_path, sample_mbox_file):
        """Dry-run should detect duplicates within the same run."""
        db_path = tmp_path / "test.db"
        csv_path = tmp_path / "report.csv"
        
        importer = MboxImporter(
            mbox_path=sample_mbox_file,
            db_path=db_path,
            csv_path=csv_path,
            mode="dry-run",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com"
        )
        
        result = importer.run()
        
        # Message 3 has same Message-ID as message 1
        assert result["skipped_duplicate"] == 1
    
    def test_dry_run_generates_detection_audit(self, tmp_path, sample_mbox_file):
        """Dry-run should generate detection audit report."""
        db_path = tmp_path / "test.db"
        csv_path = tmp_path / "report.csv"
        
        importer = MboxImporter(
            mbox_path=sample_mbox_file,
            db_path=db_path,
            csv_path=csv_path,
            mode="dry-run",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
            forwarded_mode="strict"
        )
        
        result = importer.run()
        
        # Should include audit summary
        assert "detection_audit" in result
        assert result["detection_audit"]["strict_matches"] >= 0


class TestCSVReporting:
    """Tests for CSV report generation."""
    
    def test_csv_contains_expected_columns(self, tmp_path, sample_mbox_file):
        """CSV report should have correct columns."""
        db_path = tmp_path / "test.db"
        csv_path = tmp_path / "report.csv"
        
        importer = MboxImporter(
            mbox_path=sample_mbox_file,
            db_path=db_path,
            csv_path=csv_path,
            mode="dry-run",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com"
        )
        
        importer.run()
        
        # Read CSV and verify columns
        import csv
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        
        # Verify columns exist
        assert len(rows) > 0
        required_columns = ["message_id", "status", "reason", "timestamp"]
        for col in required_columns:
            assert col in rows[0], f"Missing column: {col}"

    def test_trial_report_contains_trial_columns(self, tmp_path, curated_trial_mbox):
        """Trial reports should include trial metadata columns and values."""
        db_path = tmp_path / "trial-state.db"
        csv_path = tmp_path / "trial-report.csv"

        importer = MboxImporter(
            mbox_path=curated_trial_mbox,
            db_path=db_path,
            csv_path=csv_path,
            mode="dry-run",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
            trial_sample_size=2,
        )

        importer.run()

        import csv

        with open(csv_path, "r") as f:
            rows = list(csv.DictReader(f))

        assert rows
        row = rows[0]
        assert "trial_selected" in row
        assert "trial_profile" in row
        assert all(csv_row["trial_selected"] == "true" for csv_row in rows)
        assert all(csv_row["trial_profile"] == "curated" for csv_row in rows)


def _build_email_message(
    *,
    message_id: str,
    subject: str,
    from_addr: str = "sender@example.com",
    to_addr: str = "recipient@example.com",
    add_forwarded_headers: bool = False,
    add_attachment: bool = False,
    gmail_origin: bool = False,
    plain_text_only: bool = False,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg["Message-ID"] = message_id

    if gmail_origin:
        msg["X-Gm-Message-State"] = "state"
        msg["Received"] = "from mail-lf1-f66.google.com by gmail"

    if add_forwarded_headers:
        msg["X-Forwarded-For"] = "gmail@example.com hey@example.com"
        msg["X-Forwarded-To"] = "hey@example.com"
        msg["Delivered-To"] = "gmail@example.com"

    if add_attachment:
        msg.set_content("Plain text with attachment")
        msg.add_attachment(
            b"attachment-bytes",
            maintype="application",
            subtype="octet-stream",
            filename="file.bin",
        )
    elif plain_text_only:
        msg.set_content("Only plain text body")
    else:
        msg.set_content("<html><body>html only</body></html>", subtype="html")

    return msg


def _write_mbox(path: Path, messages: list[EmailMessage]) -> Path:
    mbox = mailbox.mbox(path)
    try:
        for msg in messages:
            mbox.add(msg)
        mbox.flush()
    finally:
        mbox.close()
    return path


@pytest.fixture
def curated_trial_mbox(tmp_path):
    mbox_path = tmp_path / "curated-trial.mbox"
    messages = [
        _build_email_message(
            message_id="<fwd-0@example.com>",
            subject="Forwarded candidate",
            add_forwarded_headers=True,
            to_addr="hey@example.com",
        ),
        _build_email_message(
            message_id="<att-1@example.com>",
            subject="Attachment candidate",
            add_attachment=True,
        ),
        _build_email_message(
            message_id="<gm-2@mail.gmail.com>",
            subject="Gmail origin candidate",
            gmail_origin=True,
            plain_text_only=True,
        ),
        _build_email_message(
            message_id="<pt-3@example.com>",
            subject="Plain text candidate",
            plain_text_only=True,
        ),
        _build_email_message(
            message_id="<fb-4@example.com>",
            subject="Fallback candidate",
        ),
        _build_email_message(
            message_id="<fb-5@example.com>",
            subject="Extra fallback candidate",
        ),
    ]
    return _write_mbox(mbox_path, messages)


class TestCuratedTrialSelector:
    def test_trial_selection_is_deterministic(self, tmp_path, curated_trial_mbox):
        def _build_importer(state_suffix: str) -> MboxImporter:
            return MboxImporter(
                mbox_path=curated_trial_mbox,
                db_path=tmp_path / f"state-{state_suffix}.db",
                csv_path=tmp_path / f"report-{state_suffix}.csv",
                mode="dry-run",
                gmail_addr="gmail@example.com",
                hey_addr="hey@example.com",
            )

        first = _build_importer("a").select_trial_indices(sample_size=5)
        second = _build_importer("b").select_trial_indices(sample_size=5)
        assert first == second

    def test_trial_selection_n5_targets_one_forwarded_and_four_importable(self, tmp_path, curated_trial_mbox):
        importer = MboxImporter(
            mbox_path=curated_trial_mbox,
            db_path=tmp_path / "state.db",
            csv_path=tmp_path / "report.csv",
            mode="dry-run",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
        )

        selected = importer.select_trial_indices(sample_size=5)
        assert selected == [0, 1, 2, 3, 4]

    def test_trial_selector_excludes_terminal_local_state(self, tmp_path, curated_trial_mbox):
        db_path = tmp_path / "state.db"
        importer = MboxImporter(
            mbox_path=curated_trial_mbox,
            db_path=db_path,
            csv_path=tmp_path / "report.csv",
            mode="dry-run",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
        )

        run_id = importer.store.create_run(mode="dry-run", total_files=1)
        file_id = importer.store.create_file(
            run_id=run_id,
            file_path=str(curated_trial_mbox),
            file_size=curated_trial_mbox.stat().st_size,
            file_mtime=curated_trial_mbox.stat().st_mtime,
            ordinal=0,
        )
        imported_id = importer.store.create_message(
            run_id=run_id,
            file_id=file_id,
            message_index=1,
            message_id_header="<att-1@example.com>",
            fingerprint="f1",
        )
        duplicate_id = importer.store.create_message(
            run_id=run_id,
            file_id=file_id,
            message_index=2,
            message_id_header="<gm-2@mail.gmail.com>",
            fingerprint="f2",
        )
        importer.store.update_message_status(imported_id, MessageStatus.IMPORTED)
        importer.store.update_message_status(duplicate_id, MessageStatus.SKIPPED_DUPLICATE)

        selected = importer.select_trial_indices(sample_size=5, allow_short_trial=True)
        assert 1 not in selected
        assert 2 not in selected

    def test_trial_selector_excludes_unparseable_raw_messages(self, tmp_path, curated_trial_mbox):
        importer = MboxImporter(
            mbox_path=curated_trial_mbox,
            db_path=tmp_path / "state.db",
            csv_path=tmp_path / "report.csv",
            mode="dry-run",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
        )

        original_stream = importer.reader.stream_messages

        def _stream_with_bad_raw():
            for index, msg, raw in original_stream():
                if index == 4:
                    yield index, msg, b""
                else:
                    yield index, msg, raw

        with patch.object(importer.reader, "stream_messages", side_effect=_stream_with_bad_raw):
            selected = importer.select_trial_indices(sample_size=5, allow_short_trial=True)
        assert 4 not in selected

    def test_trial_selector_errors_when_forwarded_slot_missing_for_n_ge_2(self, tmp_path):
        mbox_path = tmp_path / "no-forwarded.mbox"
        _write_mbox(
            mbox_path,
            [
                _build_email_message(message_id="<m0@example.com>", subject="A", plain_text_only=True),
                _build_email_message(message_id="<m1@example.com>", subject="B", add_attachment=True),
                _build_email_message(message_id="<m2@example.com>", subject="C"),
            ],
        )
        importer = MboxImporter(
            mbox_path=mbox_path,
            db_path=tmp_path / "state.db",
            csv_path=tmp_path / "report.csv",
            mode="dry-run",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
        )

        with pytest.raises(ValueError, match="forwarded-slot"):
            importer.select_trial_indices(sample_size=3)

    def test_trial_selector_allow_short_trial_when_forwarded_slot_missing(self, tmp_path):
        mbox_path = tmp_path / "no-forwarded.mbox"
        _write_mbox(
            mbox_path,
            [
                _build_email_message(message_id="<m0@example.com>", subject="A", plain_text_only=True),
                _build_email_message(message_id="<m1@example.com>", subject="B", add_attachment=True),
                _build_email_message(message_id="<m2@example.com>", subject="C"),
            ],
        )
        importer = MboxImporter(
            mbox_path=mbox_path,
            db_path=tmp_path / "state.db",
            csv_path=tmp_path / "report.csv",
            mode="dry-run",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
        )

        selected = importer.select_trial_indices(sample_size=3, allow_short_trial=True)
        assert selected == [0, 1]
        assert "forwarded-slot" in importer.last_trial_selection_warning

    def test_attachment_heuristic_selection(self, tmp_path, curated_trial_mbox):
        importer = MboxImporter(
            mbox_path=curated_trial_mbox,
            db_path=tmp_path / "state.db",
            csv_path=tmp_path / "report.csv",
            mode="dry-run",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
        )

        selected = importer.select_trial_indices(sample_size=2)
        assert selected == [0, 1]

    def test_gmail_origin_heuristic_selection(self, tmp_path):
        mbox_path = tmp_path / "gmail-origin-priority.mbox"
        _write_mbox(
            mbox_path,
            [
                _build_email_message(
                    message_id="<fwd@example.com>",
                    subject="Forwarded candidate",
                    add_forwarded_headers=True,
                    to_addr="hey@example.com",
                ),
                _build_email_message(
                    message_id="<fb@example.com>",
                    subject="Fallback candidate",
                ),
                _build_email_message(
                    message_id="<gm@mail.gmail.com>",
                    subject="Gmail origin candidate",
                    gmail_origin=True,
                ),
            ],
        )

        importer = MboxImporter(
            mbox_path=mbox_path,
            db_path=tmp_path / "state.db",
            csv_path=tmp_path / "report.csv",
            mode="dry-run",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
        )

        selected = importer.select_trial_indices(sample_size=2)
        assert selected == [0, 2]

    def test_gmail_origin_prioritized_over_plain_text_with_one_importable_slot(self, tmp_path):
        mbox_path = tmp_path / "gmail-over-plain-priority.mbox"
        _write_mbox(
            mbox_path,
            [
                _build_email_message(
                    message_id="<fwd@example.com>",
                    subject="Forwarded candidate",
                    add_forwarded_headers=True,
                    to_addr="hey@example.com",
                ),
                _build_email_message(
                    message_id="<pt@example.com>",
                    subject="Plain text candidate",
                    plain_text_only=True,
                ),
                _build_email_message(
                    message_id="<gm@mail.gmail.com>",
                    subject="Gmail origin candidate",
                    gmail_origin=True,
                    plain_text_only=True,
                ),
            ],
        )

        importer = MboxImporter(
            mbox_path=mbox_path,
            db_path=tmp_path / "state.db",
            csv_path=tmp_path / "report.csv",
            mode="dry-run",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
        )

        selected = importer.select_trial_indices(sample_size=2)
        assert selected == [0, 2]

    def test_plain_text_prioritized_over_fallback_when_gmail_origin_absent(self, tmp_path):
        mbox_path = tmp_path / "plain-over-fallback-priority.mbox"
        _write_mbox(
            mbox_path,
            [
                _build_email_message(
                    message_id="<fwd@example.com>",
                    subject="Forwarded candidate",
                    add_forwarded_headers=True,
                    to_addr="hey@example.com",
                ),
                _build_email_message(
                    message_id="<fb@example.com>",
                    subject="Fallback candidate",
                ),
                _build_email_message(
                    message_id="<pt@example.com>",
                    subject="Plain text candidate",
                    plain_text_only=True,
                ),
            ],
        )

        importer = MboxImporter(
            mbox_path=mbox_path,
            db_path=tmp_path / "state.db",
            csv_path=tmp_path / "report.csv",
            mode="dry-run",
            gmail_addr="gmail@example.com",
            hey_addr="hey@example.com",
        )

        selected = importer.select_trial_indices(sample_size=2)
        assert selected == [0, 2]
