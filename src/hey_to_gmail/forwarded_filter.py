"""Filter functions for detecting forwarded emails from Gmail."""

from email.message import EmailMessage
from email.utils import getaddresses


def is_forwarded_from_gmail(
    message: EmailMessage,
    gmail_addr: str,
    hey_addr: str,
    mode: str = "strict"
) -> bool:
    """Determine if a message was forwarded from Gmail to HEY.
    
    Args:
        message: The email message to analyze
        gmail_addr: The Gmail address configured for forwarding
        hey_addr: The HEY address that receives forwarded mail
        mode: Detection mode - "strict" or "strict_plus"
        
    Returns:
        True if the message appears to be forwarded from Gmail
    """
    if mode == "strict":
        return _check_strict_mode(message, gmail_addr, hey_addr)
    elif mode == "strict_plus":
        return _check_strict_plus_mode(message, gmail_addr, hey_addr)
    else:
        raise ValueError(f"Unknown mode: {mode}")


def _check_strict_mode(message: EmailMessage, gmail_addr: str, hey_addr: str) -> bool:
    """Strict mode: all three predicates must match."""
    gmail_addr_norm = _normalize_addr(gmail_addr)
    hey_addr_norm = _normalize_addr(hey_addr)

    # Predicate 1: X-Forwarded-For contains both addresses
    x_forwarded_for = message.get("X-Forwarded-For", "")
    pred1 = _header_contains_addr(x_forwarded_for, gmail_addr_norm) and _header_contains_addr(
        x_forwarded_for,
        hey_addr_norm,
    )

    # Predicate 2: X-Forwarded-To equals hey_addr
    pred2 = _header_matches_addr(message.get("X-Forwarded-To", ""), hey_addr_norm)

    # Predicate 3: Delivered-To equals gmail_addr
    pred3 = _header_matches_addr(message.get("Delivered-To", ""), gmail_addr_norm)

    return pred1 and pred2 and pred3


def _check_strict_plus_mode(message: EmailMessage, gmail_addr: str, hey_addr: str) -> bool:
    """Strict plus mode: strict match OR at least 2 of 4 predicates."""
    # First check if strict mode matches
    if _check_strict_mode(message, gmail_addr, hey_addr):
        return True
    
    # Otherwise count matching predicates (at least 2 needed)
    predicates_matched = _count_predicates(message, gmail_addr, hey_addr)
    return predicates_matched >= 2


def _count_predicates(message: EmailMessage, gmail_addr: str, hey_addr: str) -> int:
    """Count how many of the 4 predicates match."""
    count = 0
    gmail_addr_norm = _normalize_addr(gmail_addr)
    hey_addr_norm = _normalize_addr(hey_addr)

    # Predicate 1: X-Forwarded-For includes both addresses
    x_forwarded_for = message.get("X-Forwarded-For", "")
    if _header_contains_addr(x_forwarded_for, gmail_addr_norm) and _header_contains_addr(
        x_forwarded_for,
        hey_addr_norm,
    ):
        count += 1

    # Predicate 2: X-Forwarded-To equals hey_addr
    if _header_matches_addr(message.get("X-Forwarded-To", ""), hey_addr_norm):
        count += 1

    # Predicate 3: Delivered-To equals gmail_addr
    if _header_matches_addr(message.get("Delivered-To", ""), gmail_addr_norm):
        count += 1

    # Predicate 4: To equals gmail_addr AND Received indicates Google path
    if _header_matches_addr(message.get("To", ""), gmail_addr_norm):
        received = message.get("Received", "")
        if "google" in received.lower() or "gmail" in received.lower():
            count += 1

    return count


def _normalize_addr(value: str) -> str:
    """Normalize an address-like value to lowercase mailbox form."""
    parsed = [addr.strip().lower() for _, addr in getaddresses([value]) if addr.strip()]
    if parsed:
        return parsed[0]
    return str(value).strip().lower()


def _extract_addrs(value: str) -> set[str]:
    """Extract all normalized mailbox addresses from a header value."""
    return {addr.strip().lower() for _, addr in getaddresses([value]) if addr.strip()}


def _header_contains_addr(value: str, target_addr: str) -> bool:
    """Return whether header contains target mailbox address."""
    extracted = _extract_addrs(value)
    if extracted:
        return target_addr in extracted
    return target_addr in str(value).lower()


def _header_matches_addr(value: str, target_addr: str) -> bool:
    """Return whether header value resolves to target mailbox address."""
    extracted = _extract_addrs(value)
    if extracted:
        return target_addr in extracted
    return _normalize_addr(value) == target_addr
