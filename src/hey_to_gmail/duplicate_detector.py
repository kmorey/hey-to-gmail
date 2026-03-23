"""Duplicate detection logic for email messages."""

import hashlib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Literal


@dataclass(frozen=True)
class DedupeKey:
    """A deduplication key for an email message.
    
    kind: "message_id" uses the RFC Message-ID header
          "content_hash" uses a stable hash of message content
    value: the normalized key value
    hash_version: "v1" for content_hash, None for message_id
    """
    kind: Literal["message_id", "content_hash"]
    value: str
    hash_version: str | None


# Headers used for hash v1, in order
_HASH_V1_HEADERS = [
    "Date",
    "From",
    "To",
    "Cc",
    "Subject",
    "In-Reply-To",
    "References",
]


def _normalize_header_name(name: str) -> str:
    """Normalize header name: lowercase."""
    return name.lower()


def _normalize_header_value(value: str) -> str:
    """Normalize header value: unfold continuation, trim, collapse whitespace."""
    # Unfold continuation lines (replace newlines with space)
    unfolded = value.replace("\r\n", " ").replace("\n", " ")
    # Trim leading/trailing whitespace
    trimmed = unfolded.strip()
    # Collapse internal whitespace to single space
    collapsed = " ".join(trimmed.split())
    return collapsed


def _decode_payload_with_charset(part) -> bytes:
    """Decode payload bytes using declared charset, fallback to utf-8 with replacement.
    
    Returns UTF-8 encoded bytes of the decoded text.
    """
    payload = part.get_payload(decode=True)
    if payload is None:
        return b""
    
    # Get declared charset from Content-Type header
    charset = part.get_content_charset()
    
    if charset:
        try:
            # Try to decode using declared charset
            text = payload.decode(charset)
        except (LookupError, UnicodeDecodeError):
            # Invalid charset or decode error - fallback to utf-8 with replacement
            text = payload.decode("utf-8", errors="replace")
    else:
        # No charset declared - use utf-8 with replacement
        text = payload.decode("utf-8", errors="replace")
    
    # Re-encode as UTF-8 for consistent hashing
    return text.encode("utf-8")


def _extract_body_for_hash(msg: EmailMessage) -> bytes:
    """Extract body for hashing: prefer text/plain, then text/html, then raw."""
    # Try text/plain first
    if msg.get_content_type() == "text/plain":
        try:
            return _decode_payload_with_charset(msg)
        except Exception:
            pass
    
    # If multipart, try to find text/plain first
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return _decode_payload_with_charset(part)
                except Exception:
                    continue
        
        # Then try text/html
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    return _decode_payload_with_charset(part)
                except Exception:
                    continue
    
    # Try text/html on the main message
    if msg.get_content_type() == "text/html":
        try:
            return _decode_payload_with_charset(msg)
        except Exception:
            pass
    
    # Fallback to raw body
    try:
        payload = msg.get_payload(decode=True)
        if payload is not None:
            # Try charset-aware decoding
            return _decode_payload_with_charset(msg)
    except Exception:
        pass
    
    return b""


def _normalize_newlines(data: bytes) -> bytes:
    """Normalize CRLF/CR to LF."""
    # First convert CRLF to LF, then convert remaining CR to LF
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _compute_hash_v1(msg: EmailMessage) -> str:
    """Compute hash v1 for a message without Message-ID.
    
    Hash input format: v1\n<header-name>:<value>...\n\n<body>
    """
    parts = ["v1"]
    
    # Add headers in specified order
    for header_name in _HASH_V1_HEADERS:
        if header_name in msg:
            values = msg.get_all(header_name, [])
            for value in values:
                normalized_name = _normalize_header_name(header_name)
                normalized_value = _normalize_header_value(str(value))
                parts.append(f"{normalized_name}:{normalized_value}")
    
    # Get and normalize body
    body_bytes = _extract_body_for_hash(msg)
    body_bytes = _normalize_newlines(body_bytes)
    
    # Combine: headers joined by newline, then blank line, then body
    header_part = "\n".join(parts)
    hash_input = header_part.encode("utf-8") + b"\n\n" + body_bytes
    
    # SHA-256 hex digest
    return hashlib.sha256(hash_input).hexdigest()


def _normalize_message_id(message_id: str) -> str:
    """Normalize Message-ID by stripping angle brackets."""
    normalized = message_id.strip()
    if normalized.startswith("<") and normalized.endswith(">"):
        normalized = normalized[1:-1]
    return normalized


def dedupe_key_for_message(msg: EmailMessage) -> DedupeKey:
    """Generate a deduplication key for an email message.
    
    Uses Message-ID if present, otherwise falls back to content hash v1.
    """
    # Check for Message-ID header
    message_id = msg.get("Message-ID")
    if message_id:
        normalized_id = _normalize_message_id(str(message_id))
        return DedupeKey(
            kind="message_id",
            value=normalized_id,
            hash_version=None
        )
    
    # Fall back to content hash
    hash_value = _compute_hash_v1(msg)
    return DedupeKey(
        kind="content_hash",
        value=hash_value,
        hash_version="v1"
    )


class RemoteDedupeChecker:
    """Protocol/contract for remote deduplication checks.
    
    This establishes the interface for Gmail-side duplicate lookup.
    Concrete implementation will be provided in Task 5 (gmail_client.py).
    """
    
    def should_query_remote(self, message_id: str | None) -> bool:
        """Determine if remote lookup should be performed.
        
        Returns True if message_id is provided, False otherwise.
        """
        return message_id is not None
