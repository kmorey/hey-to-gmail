"""Test fixtures for hey-to-gmail tests."""
from unittest.mock import MagicMock
import pytest


@pytest.fixture
def gmail_service_mock():
    """Mock Gmail API service."""
    service = MagicMock()
    # Setup the chain for users().messages().import_()
    users = MagicMock()
    messages = MagicMock()
    import_method = MagicMock()
    list_method = MagicMock()
    
    service.users.return_value = users
    users.messages.return_value = messages
    messages.import_ = import_method
    messages.list = list_method
    
    return service


@pytest.fixture
def sample_raw_email():
    """Sample raw email bytes for testing."""
    return b"From: test@example.com\r\nTo: recipient@example.com\r\nSubject: Test\r\nMessage-ID: <abc@example.com\u003e\r\n\r\nTest body"


@pytest.fixture
def mock_credentials():
    """Mock Google OAuth credentials."""
    creds = MagicMock()
    creds.valid = True
    creds.token = "fake_token"
    creds.refresh_token = "fake_refresh_token"
    return creds
