import pytest
from email.message import EmailMessage
from pathlib import Path

from hey_to_gmail.forwarded_filter import is_forwarded_from_gmail
from hey_to_gmail.reporting import DetectionAuditCounter


@pytest.fixture
def sample_forwarded_message():
    """Load the strict forwarded fixture."""
    fixture_path = Path(__file__).parent / "fixtures" / "headers" / "forwarded_strict.eml"
    content = fixture_path.read_text()
    msg = EmailMessage()
    for line in content.strip().split("\n"):
        if ": " in line:
            key, value = line.split(": ", 1)
            msg[key] = value
    return msg


@pytest.fixture
def sample_missing_header_forwarded_message():
    """Load the fixture missing one header."""
    fixture_path = Path(__file__).parent / "fixtures" / "headers" / "forwarded_missing_one_header.eml"
    content = fixture_path.read_text()
    msg = EmailMessage()
    for line in content.strip().split("\n"):
        if ": " in line:
            key, value = line.split(": ", 1)
            msg[key] = value
    return msg


@pytest.fixture
def sample_google_received_hop_message():
    """Load a fixture with Google Received hop."""
    msg = EmailMessage()
    msg["X-Forwarded-For"] = "user@gmail.com user@hey.com"
    msg["To"] = "user@gmail.com"
    msg["Received"] = "from mail.google.com by mx.hey.com"
    return msg


@pytest.fixture
def sample_two_predicates_message():
    """Load a fixture matching exactly 2 of 4 predicates."""
    msg = EmailMessage()
    msg["X-Forwarded-For"] = "user@gmail.com user@hey.com"
    msg["X-Forwarded-To"] = "user@hey.com"
    msg["From"] = "sender@example.com"
    msg["To"] = "user@hey.com"
    msg["Subject"] = "Two predicates match"
    return msg


def test_strict_requires_all_three_predicates(sample_forwarded_message):
    """Strict mode requires all three header conditions."""
    assert is_forwarded_from_gmail(
        sample_forwarded_message,
        gmail_addr="user@gmail.com",
        hey_addr="user@hey.com",
        mode="strict"
    )


def test_strict_plus_accepts_two_of_four(sample_missing_header_forwarded_message):
    """Strict_plus accepts messages matching at least 2 of 4 predicates."""
    assert is_forwarded_from_gmail(
        sample_missing_header_forwarded_message,
        gmail_addr="user@gmail.com",
        hey_addr="user@hey.com",
        mode="strict_plus"
    )


def test_strict_plus_predicate_four_received_hop(sample_google_received_hop_message):
    """Strict_plus accepts messages with Google Received hop as 4th predicate."""
    assert is_forwarded_from_gmail(
        sample_google_received_hop_message,
        gmail_addr="user@gmail.com",
        hey_addr="user@hey.com",
        mode="strict_plus"
    )


def test_strict_plus_exactly_two_predicates_boundary(sample_two_predicates_message):
    """Strict_plus boundary test - exactly 2 predicates should match."""
    assert is_forwarded_from_gmail(
        sample_two_predicates_message,
        gmail_addr="user@gmail.com",
        hey_addr="user@hey.com",
        mode="strict_plus"
    )


def test_strict_normalizes_address_header_formats():
    """Strict mode should handle names, angle brackets, and casing."""
    msg = EmailMessage()
    msg["X-Forwarded-For"] = "Example User <user@gmail.com>, Kevin <user@hey.com>"
    msg["X-Forwarded-To"] = "Example User <user@hey.com>"
    msg["Delivered-To"] = "Example User <user@gmail.com>"

    assert is_forwarded_from_gmail(
        msg,
        gmail_addr="user@gmail.com",
        hey_addr="user@hey.com",
        mode="strict",
    )


def test_audit_counter_tracks_rule_hits():
    """DetectionAuditCounter should track rule hits correctly."""
    counter = DetectionAuditCounter()
    counter.record(strict_match=True, predicates_matched=3)
    assert counter.summary()["strict_matches"] == 1


def test_audit_counter_tracks_strict_plus_matches():
    """DetectionAuditCounter should track strict_plus matches separately."""
    counter = DetectionAuditCounter()
    counter.record(strict_match=True, predicates_matched=3)
    counter.record(strict_match=False, predicates_matched=2)
    counter.record(strict_match=False, predicates_matched=3)
    counter.record(strict_match=True, predicates_matched=3)
    summary = counter.summary()
    assert summary["strict_matches"] == 2
    assert summary["strict_plus_matches"] == 2
    assert summary["total_processed"] == 4


def test_audit_counter_strict_plus_only_when_not_strict():
    """Strict_plus counter should only increment when not a strict match."""
    counter = DetectionAuditCounter()
    counter.record(strict_match=True, predicates_matched=3)
    assert counter.summary()["strict_plus_matches"] == 0


def test_audit_counter_strict_plus_requires_two_predicates():
    """Strict_plus counter should only increment with 2+ predicates matched."""
    counter = DetectionAuditCounter()
    counter.record(strict_match=False, predicates_matched=1)
    counter.record(strict_match=False, predicates_matched=0)
    assert counter.summary()["strict_plus_matches"] == 0
