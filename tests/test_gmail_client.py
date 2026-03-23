"""Tests for Gmail client and label manager."""
import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest


class TestGmailClientImport:
    """Tests for GmailClient.import_message method."""
    
    def test_import_uses_label_ids(self, gmail_service_mock, sample_raw_email):
        """Test that import passes labelIds correctly."""
        from hey_to_gmail.gmail_client import GmailClient
        
        client = GmailClient(service=gmail_service_mock)
        client.import_message(raw_bytes=sample_raw_email, label_ids=["LBL_HEY"])
        
        # Get the actual call arguments
        import_mock = gmail_service_mock.users.return_value.messages.return_value.import_
        call_args = import_mock.call_args
        
        # Verify import_ was called
        assert import_mock.call_count == 1
        
        # Verify labelIds is in the request body
        body = call_args.kwargs.get('body', {})
        assert 'labelIds' in body
        assert body['labelIds'] == ["LBL_HEY"]
        
        # Also verify userId
        assert call_args.kwargs.get('userId') == 'me'

    def test_import_sets_never_mark_spam(self, gmail_service_mock, sample_raw_email):
        """Import requests should set neverMarkSpam to avoid spam auto-labeling."""
        from hey_to_gmail.gmail_client import GmailClient

        client = GmailClient(service=gmail_service_mock)
        client.import_message(raw_bytes=sample_raw_email, label_ids=["LBL_HEY"])

        import_mock = gmail_service_mock.users.return_value.messages.return_value.import_
        body = import_mock.call_args.kwargs.get("body", {})
        assert body.get("neverMarkSpam") is True


class TestGmailClientRemoteDedupe:
    """Tests for GmailClient.message_exists_by_rfc822msgid method."""
    
    def test_remote_dedupe_queries_rfc822msgid(self, gmail_service_mock):
        """Test that message_exists_by_rfc822msgid queries Gmail correctly."""
        from hey_to_gmail.gmail_client import GmailClient
        
        client = GmailClient(service=gmail_service_mock)
        client.message_exists_by_rfc822msgid("<abc@example.com>")
        
        gmail_service_mock.users.return_value.messages.return_value.list.assert_called_once()


class TestGmailClientAuth:
    """Tests for GmailClient authentication and token management."""
    
    def test_token_file_permission_is_0600_via_client(self, tmp_path):
        """Test that _save_token creates file with 0600 permissions."""
        from hey_to_gmail.gmail_client import GmailClient
        
        # Create a temporary token file path
        token_path = tmp_path / "token.json"
        
        # Create client with mocked service to bypass auth
        client = GmailClient(service=MagicMock(), token_path=token_path)
        
        # Create mock credentials
        mock_creds = MagicMock()
        mock_creds.token = "fake_token"
        mock_creds.refresh_token = "fake_refresh"
        mock_creds.token_uri = "https://oauth2.googleapis.com/token"
        mock_creds.client_id = "fake_client_id"
        mock_creds.client_secret = "fake_secret"
        mock_creds.scopes = ["https://www.googleapis.com/auth/gmail.modify"]
        
        # Call _save_token which should set permissions
        client._save_token(mock_creds)
        
        # Verify file was created with 0600 permissions
        assert token_path.exists()
        stat_info = os.stat(token_path)
        assert stat.S_IMODE(stat_info.st_mode) == 0o600
    
    def test_save_token_calls_ensure_token_permissions(self, tmp_path):
        """Test that _save_token calls _ensure_token_permissions."""
        from hey_to_gmail.gmail_client import GmailClient
        
        token_path = tmp_path / "token.json"
        client = GmailClient(service=MagicMock(), token_path=token_path)
        
        mock_creds = MagicMock()
        mock_creds.token = "fake_token"
        mock_creds.refresh_token = "fake_refresh"
        mock_creds.token_uri = "https://oauth2.googleapis.com/token"
        mock_creds.client_id = "fake_client_id"
        mock_creds.client_secret = "fake_secret"
        mock_creds.scopes = ["https://www.googleapis.com/auth/gmail.modify"]
        
        # Patch _ensure_token_permissions to verify it's called
        with patch.object(client, '_ensure_token_permissions') as mock_ensure:
            client._save_token(mock_creds)
            mock_ensure.assert_called_once()
    
    def test_directory_created_with_0700_permissions(self, tmp_path):
        """Test that config directory is created with 0700 permissions."""
        from hey_to_gmail.gmail_client import GmailClient
        
        # Use a nested path to test directory creation
        config_dir = tmp_path / "config" / "subdir"
        token_path = config_dir / "token.json"
        
        client = GmailClient(service=MagicMock(), token_path=token_path)
        
        mock_creds = MagicMock()
        mock_creds.token = "fake_token"
        mock_creds.refresh_token = "fake_refresh"
        mock_creds.token_uri = "https://oauth2.googleapis.com/token"
        mock_creds.client_id = "fake_client_id"
        mock_creds.client_secret = "fake_secret"
        mock_creds.scopes = ["https://www.googleapis.com/auth/gmail.modify"]
        
        # Call _save_token which should create directory with 0700
        client._save_token(mock_creds)
        
        # Verify directory was created with 0700 permissions
        assert config_dir.exists()
        stat_info = os.stat(config_dir)
        assert stat.S_IMODE(stat_info.st_mode) == 0o700


class TestLabelManager:
    """Tests for LabelManager label management."""
    
    def test_ensure_label_reuses_cached_id(self, gmail_service_mock):
        """Test that ensure_label caches and reuses label IDs."""
        from hey_to_gmail.label_manager import LabelManager
        
        # Setup mock to return a label list
        labels_mock = MagicMock()
        labels_list_mock = MagicMock()
        labels_list_mock.execute.return_value = {
            "labels": [
                {"id": "LABEL_123", "name": "Hey.com"}
            ]
        }
        labels_mock.list.return_value = labels_list_mock
        
        gmail_service_mock.users.return_value.labels.return_value = labels_mock
        
        manager = LabelManager(service=gmail_service_mock)
        
        # First call should query the API
        id1 = manager.ensure_label("Hey.com")
        assert id1 == "LABEL_123"
        assert labels_list_mock.execute.call_count == 1
        
        # Second call should use cache (no additional API call)
        id2 = manager.ensure_label("Hey.com")
        assert id2 == "LABEL_123"
        assert labels_list_mock.execute.call_count == 1  # Still 1, not 2


class TestGmailClientRetry:
    """Tests for GmailClient retry/backoff behavior."""
    
    def test_import_retries_on_429(self, gmail_service_mock, sample_raw_email):
        """Test that import retries on 429 rate limit errors."""
        from hey_to_gmail.gmail_client import GmailClient
        
        client = GmailClient(service=gmail_service_mock)
        
        # Setup mock to fail twice with 429, then succeed
        import_mock = gmail_service_mock.users.return_value.messages.return_value.import_
        
        # Create mock error responses
        error_resp1 = MagicMock()
        error_resp1.status = 429
        error_resp2 = MagicMock()
        error_resp2.status = 429
        
        # Create mock exceptions
        class MockHttpError(Exception):
            def __init__(self, resp, content):
                self.resp = resp
                self.content = content
        
        import_mock.side_effect = [
            MockHttpError(error_resp1, b'Rate limit exceeded'),
            MockHttpError(error_resp2, b'Rate limit exceeded'),
            MagicMock()  # Success on third try
        ]
        
        # Should succeed after retries
        result = client.import_message(raw_bytes=sample_raw_email, label_ids=["LBL_HEY"])
        
        # Should have been called 3 times (2 failures + 1 success)
        assert import_mock.call_count == 3


class TestGmailClientOAuth:
    """Tests for GmailClient OAuth configuration."""
    
    def test_uses_correct_oauth_scope(self):
        """Test that client uses gmail.modify scope."""
        from hey_to_gmail.gmail_client import SCOPES
        
        assert "https://www.googleapis.com/auth/gmail.modify" in SCOPES


class TestGmailClientOAuthFlow:
    """Tests for GmailClient OAuth authentication flow."""
    
    def _patch_gmail_client(self, gmail_client, **mocks):
        """Helper to patch gmail_client module attributes."""
        originals = {}
        for name, mock in mocks.items():
            originals[name] = getattr(gmail_client, name, None)
            setattr(gmail_client, name, mock)
        return originals
    
    def _restore_gmail_client(self, gmail_client, originals):
        """Helper to restore gmail_client module attributes."""
        for name, orig in originals.items():
            if orig is None:
                if hasattr(gmail_client, name):
                    delattr(gmail_client, name)
            else:
                setattr(gmail_client, name, orig)
    
    def test_load_existing_valid_token(self, tmp_path):
        """Test loading existing valid token via from_authorized_user_file."""
        import hey_to_gmail.gmail_client as gmail_client
        
        token_path = tmp_path / "token.json"
        token_path.write_text('{"token": "valid_token"}')
        
        # Setup mock credentials as valid
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.expired = False
        mock_creds.refresh_token = "refresh_token"
        mock_creds.token = "test_token"
        mock_creds.token_uri = "https://oauth2.googleapis.com/token"
        mock_creds.client_id = "test_client"
        mock_creds.client_secret = "test_secret"
        mock_creds.scopes = ["https://www.googleapis.com/auth/gmail.modify"]
        
        mock_build = MagicMock()
        mock_creds_class = MagicMock()
        mock_creds_class.from_authorized_user_file.return_value = mock_creds
        
        # Patch module-level names
        originals = self._patch_gmail_client(
            gmail_client,
            Credentials=mock_creds_class,
            build=mock_build,
            _GOOGLE_AVAILABLE=True
        )
        
        try:
            client = gmail_client.GmailClient(token_path=token_path, credentials_path=tmp_path / "creds.json")
            
            # Verify from_authorized_user_file was called
            mock_creds_class.from_authorized_user_file.assert_called_once()
            # Verify build was called with the credentials
            mock_build.assert_called_once_with('gmail', 'v1', credentials=mock_creds)
        finally:
            self._restore_gmail_client(gmail_client, originals)
    
    def test_auto_refresh_on_expiry(self, tmp_path):
        """Test auto-refresh when token is expired but has refresh_token."""
        import hey_to_gmail.gmail_client as gmail_client
        
        token_path = tmp_path / "token.json"
        token_path.write_text('{"token": "expired_token"}')
        
        # Setup mock credentials as expired but refreshable
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh_token"
        mock_creds.token = "new_token"
        mock_creds.token_uri = "https://oauth2.googleapis.com/token"
        mock_creds.client_id = "test_client"
        mock_creds.client_secret = "test_secret"
        mock_creds.scopes = ["https://www.googleapis.com/auth/gmail.modify"]
        
        mock_build = MagicMock()
        mock_request = MagicMock()
        mock_creds_class = MagicMock()
        mock_creds_class.from_authorized_user_file.return_value = mock_creds
        
        originals = self._patch_gmail_client(
            gmail_client,
            Credentials=mock_creds_class,
            build=mock_build,
            Request=mock_request,
            _GOOGLE_AVAILABLE=True
        )
        
        try:
            client = gmail_client.GmailClient(token_path=token_path, credentials_path=tmp_path / "creds.json")
            
            # Verify refresh was called with Request
            mock_creds.refresh.assert_called_once_with(mock_request())
            # Verify build was called after refresh
            assert mock_build.call_count == 1
        finally:
            self._restore_gmail_client(gmail_client, originals)
    
    def test_new_oauth_flow_when_no_token(self, tmp_path):
        """Test new OAuth flow when no token exists."""
        import hey_to_gmail.gmail_client as gmail_client
        
        token_path = tmp_path / "token.json"
        creds_path = tmp_path / "credentials.json"
        creds_path.write_text('{"web": {"client_id": "test"}}')
        
        # Setup mock flow
        mock_new_creds = MagicMock()
        mock_new_creds.token = "new_token"
        mock_new_creds.refresh_token = "refresh"
        mock_new_creds.token_uri = "https://oauth2.googleapis.com/token"
        mock_new_creds.client_id = "test_client"
        mock_new_creds.client_secret = "test_secret"
        mock_new_creds.scopes = ["https://www.googleapis.com/auth/gmail.modify"]
        
        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = mock_new_creds
        mock_flow_class = MagicMock()
        mock_flow_class.from_client_secrets_file.return_value = mock_flow
        
        mock_build = MagicMock()
        
        originals = self._patch_gmail_client(
            gmail_client,
            InstalledAppFlow=mock_flow_class,
            build=mock_build,
            _GOOGLE_AVAILABLE=True
        )
        
        try:
            client = gmail_client.GmailClient(token_path=token_path, credentials_path=creds_path)
            
            # Verify flow was created and run
            mock_flow_class.from_client_secrets_file.assert_called_once()
            mock_flow.run_local_server.assert_called_once_with(port=0)
            # Verify build was called with new credentials
            mock_build.assert_called_once_with('gmail', 'v1', credentials=mock_new_creds)
        finally:
            self._restore_gmail_client(gmail_client, originals)
    
    def test_refresh_failure_triggers_reauth_instructions(self, tmp_path, capsys):
        """Test that refresh failure triggers re-authentication instructions."""
        import hey_to_gmail.gmail_client as gmail_client
        
        token_path = tmp_path / "token.json"
        creds_path = tmp_path / "credentials.json"
        creds_path.write_text('{"web": {"client_id": "test"}}')
        
        # Create a custom RefreshError class
        class MockRefreshError(Exception):
            pass
        
        # Setup mock credentials as expired with refresh token
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh_token"
        mock_creds.refresh.side_effect = MockRefreshError("Token expired")
        
        mock_creds_class = MagicMock()
        mock_creds_class.from_authorized_user_file.return_value = mock_creds
        
        mock_flow = MagicMock()
        mock_flow.run_local_server.side_effect = Exception("Auth failed")
        mock_flow_class = MagicMock()
        mock_flow_class.from_client_secrets_file.return_value = mock_flow
        
        originals = self._patch_gmail_client(
            gmail_client,
            Credentials=mock_creds_class,
            InstalledAppFlow=mock_flow_class,
            RefreshError=MockRefreshError,
            _GOOGLE_AVAILABLE=True
        )
        
        try:
            # Should raise RuntimeError after auth failure
            with pytest.raises(RuntimeError):
                client = gmail_client.GmailClient(token_path=token_path, credentials_path=creds_path)
            
            # Verify auth failure instructions were printed
            captured = capsys.readouterr()
            assert "AUTHENTICATION FAILED" in captured.out
        finally:
            self._restore_gmail_client(gmail_client, originals)


class TestLabelManagerCreate:
    """Tests for LabelManager label creation."""
    
    def test_ensure_label_creates_if_missing(self, gmail_service_mock):
        """Test that ensure_label creates label if it doesn't exist."""
        from hey_to_gmail.label_manager import LabelManager
        
        # Setup mock to return empty label list (label doesn't exist)
        labels_mock = MagicMock()
        labels_list_mock = MagicMock()
        labels_list_mock.execute.return_value = {"labels": []}
        
        # Setup create mock to return new label
        labels_create_mock = MagicMock()
        labels_create_mock.execute.return_value = {"id": "NEW_LABEL_123", "name": "Hey.com"}
        
        labels_mock.list.return_value = labels_list_mock
        labels_mock.create.return_value = labels_create_mock
        
        gmail_service_mock.users.return_value.labels.return_value = labels_mock
        
        manager = LabelManager(service=gmail_service_mock)
        label_id = manager.ensure_label("Hey.com")
        
        assert label_id == "NEW_LABEL_123"
        labels_create_mock.execute.assert_called_once()
