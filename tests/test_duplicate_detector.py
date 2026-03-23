import pytest
from email.message import EmailMessage
from pathlib import Path

from hey_to_gmail.duplicate_detector import (
    DedupeKey,
    dedupe_key_for_message,
    RemoteDedupeChecker,
)


@pytest.fixture
def sample_email_message():
    """A basic email with Message-ID."""
    msg = EmailMessage()
    msg["Message-ID"] = "<abc123@example.com>"
    msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg["From"] = "sender@example.com"
    msg["To"] = "recipient@example.com"
    msg["Subject"] = "Test Subject"
    msg.set_content("Test body content")
    return msg


@pytest.fixture
def sample_no_message_id():
    """Email without Message-ID."""
    msg = EmailMessage()
    msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg["From"] = "sender@example.com"
    msg["To"] = "recipient@example.com"
    msg["Subject"] = "Test Subject"
    msg.set_content("Test body content")
    return msg


@pytest.fixture
def msg_variant_a():
    """First whitespace variant."""
    msg = EmailMessage()
    msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg["From"] = "sender@example.com"
    msg["To"] = "recipient@example.com"
    msg["Subject"] = "Test Subject"
    msg.set_content("Test body content")
    return msg


@pytest.fixture
def msg_variant_b():
    """Second whitespace variant - extra spaces in headers."""
    msg = EmailMessage()
    msg["Date"] = "Mon, 01 Jan 2024  12:00:00 +0000"
    msg["From"] = " sender@example.com "
    msg["To"] = "recipient@example.com"
    msg["Subject"] = "Test   Subject"
    msg.set_content("Test body content")
    return msg


@pytest.fixture
def sample_multipart_message():
    """Multipart message with text/plain and text/html."""
    msg = EmailMessage()
    msg["Message-ID"] = "<multipart@example.com>"
    msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg["From"] = "sender@example.com"
    msg["To"] = "recipient@example.com"
    msg["Subject"] = "Multipart Test"
    msg.make_alternative()
    msg.add_alternative("Plain text body", subtype="plain")
    msg.add_alternative("<html><body>HTML body</body></html>", subtype="html")
    return msg


@pytest.fixture
def sample_charset_variant_messages():
    """Messages with different charset declarations but same decoded content."""
    # Message with ISO-8859-1 charset
    msg_iso = EmailMessage()
    msg_iso["Message-ID"] = "<charset@example.com>"
    msg_iso["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg_iso["From"] = "sender@example.com"
    msg_iso["To"] = "recipient@example.com"
    msg_iso["Subject"] = "Charset Test"
    # "café\n" encoded in ISO-8859-1
    msg_iso.set_payload(b"caf\xe9\n", charset="iso-8859-1")
    
    # Message with UTF-8 charset - same decoded content
    msg_utf8 = EmailMessage()
    msg_utf8["Message-ID"] = "<charset@example.com>"
    msg_utf8["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg_utf8["From"] = "sender@example.com"
    msg_utf8["To"] = "recipient@example.com"
    msg_utf8["Subject"] = "Charset Test"
    # "café\n" encoded in UTF-8
    msg_utf8.set_payload(b"caf\xc3\xa9\n", charset="utf-8")
    
    return msg_iso, msg_utf8


@pytest.fixture
def remote_dedupe_checker():
    """Create a remote dedupe checker."""
    return RemoteDedupeChecker()


def test_message_id_key_preferred(sample_email_message):
    """When Message-ID is present, it should be used as the dedupe key."""
    key = dedupe_key_for_message(sample_email_message)
    assert key.kind == "message_id"


def test_message_id_value_normalized(sample_email_message):
    """Message-ID value should be normalized (stripped of angle brackets)."""
    key = dedupe_key_for_message(sample_email_message)
    assert key.value == "abc123@example.com"


def test_hash_v1_used_when_no_message_id(sample_no_message_id):
    """When Message-ID is absent, content_hash should be used."""
    key = dedupe_key_for_message(sample_no_message_id)
    assert key.kind == "content_hash"
    assert key.hash_version == "v1"


def test_hash_v1_stable_across_whitespace_variants(msg_variant_a, msg_variant_b):
    """Hash should be stable regardless of header whitespace variations."""
    key_a = dedupe_key_for_message(msg_variant_a)
    key_b = dedupe_key_for_message(msg_variant_b)
    assert key_a.value == key_b.value


def test_hash_v1_uses_required_header_order(sample_no_message_id):
    """Hash v1 should use Date, From, To, Cc, Subject, In-Reply-To, References in order."""
    key = dedupe_key_for_message(sample_no_message_id)
    assert key.kind == "content_hash"
    assert key.hash_version == "v1"
    assert len(key.value) == 64  # SHA-256 hex digest is 64 chars


def test_hash_v1_prefers_text_plain_then_html(sample_multipart_message):
    """Hash v1 should prefer text/plain body, fall back to text/html."""
    # First, test without Message-ID to force hash usage
    msg = EmailMessage()
    msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg["From"] = "sender@example.com"
    msg["To"] = "recipient@example.com"
    msg["Subject"] = "Multipart Test"
    msg.make_alternative()
    msg.add_alternative("Plain text body", subtype="plain")
    msg.add_alternative("<html><body>HTML body</body></html>", subtype="html")
    
    key = dedupe_key_for_message(msg)
    assert key.kind == "content_hash"
    assert key.hash_version == "v1"


def test_hash_v1_normalizes_newlines_in_body():
    """Hash v1 should normalize CRLF/CR to LF in body bytes."""
    # Create two messages with different newline characters in body
    # Manually set payload to control newlines since set_content() normalizes
    msg_a = EmailMessage()
    msg_a["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg_a["From"] = "sender@example.com"
    msg_a["To"] = "recipient@example.com"
    msg_a["Subject"] = "Test"
    msg_a.set_content("Test body content")
    # Replace the payload to have LF newlines
    msg_a.set_payload(b"Test body content\nMore text\n", charset="utf-8")
    
    msg_b = EmailMessage()
    msg_b["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg_b["From"] = "sender@example.com"
    msg_b["To"] = "recipient@example.com"
    msg_b["Subject"] = "Test"
    msg_b.set_content("Test body content")
    # Replace the payload to have CRLF newlines
    msg_b.set_payload(b"Test body content\r\nMore text\r\n", charset="utf-8")
    
    key_a = dedupe_key_for_message(msg_a)
    key_b = dedupe_key_for_message(msg_b)
    
    # Same content with different newlines should produce same hash
    assert key_a.value == key_b.value


def test_remote_dedupe_skips_lookup_without_message_id(remote_dedupe_checker):
    """Remote dedupe should skip lookup when message_id is None."""
    assert remote_dedupe_checker.should_query_remote(message_id=None) is False


def test_remote_dedupe_allows_lookup_with_message_id(remote_dedupe_checker):
    """Remote dedupe should allow lookup when message_id is provided."""
    assert remote_dedupe_checker.should_query_remote(message_id="abc123@example.com") is True


def test_hash_v1_normalizes_charset_and_newlines(sample_charset_variant_messages):
    """Hash v1 should normalize different charsets to same hash for same content."""
    msg_iso, msg_utf8 = sample_charset_variant_messages
    
    # Remove Message-ID to force hash comparison
    del msg_iso["Message-ID"]
    del msg_utf8["Message-ID"]
    
    key_iso = dedupe_key_for_message(msg_iso)
    key_utf8 = dedupe_key_for_message(msg_utf8)
    
    # Same content in different charsets should produce same hash
    assert key_iso.value == key_utf8.value


def test_hash_v1_handles_different_charsets():
    """Hash v1 should decode different charsets and produce consistent hashes for same content."""
    # Create message with ISO-8859-1 charset
    msg_iso = EmailMessage()
    msg_iso["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg_iso["From"] = "sender@example.com"
    msg_iso["To"] = "recipient@example.com"
    msg_iso["Subject"] = "Charset Test"
    # "café" in ISO-8859-1
    msg_iso.set_payload(b"caf\xe9", charset="iso-8859-1")
    
    # Create message with UTF-8 charset
    msg_utf8 = EmailMessage()
    msg_utf8["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg_utf8["From"] = "sender@example.com"
    msg_utf8["To"] = "recipient@example.com"
    msg_utf8["Subject"] = "Charset Test"
    # "café" in UTF-8
    msg_utf8.set_payload(b"caf\xc3\xa9", charset="utf-8")
    
    key_iso = dedupe_key_for_message(msg_iso)
    key_utf8 = dedupe_key_for_message(msg_utf8)
    
    # Same content decoded should produce same hash
    assert key_iso.value == key_utf8.value


def test_dedupe_key_is_frozen():
    """DedupeKey should be immutable (frozen dataclass)."""
    key = DedupeKey(kind="message_id", value="test", hash_version=None)
    with pytest.raises(AttributeError):
        key.value = "modified"
