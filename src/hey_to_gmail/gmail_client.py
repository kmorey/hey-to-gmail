"""Gmail API client with OAuth authentication and retry logic."""
import base64
import json
import os
import time
from pathlib import Path

# OAuth scope for Gmail API - allows reading, sending, and modifying labels
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

# Google imports - made optional for testing
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from google.auth.exceptions import RefreshError
    _GOOGLE_AVAILABLE = True
except ImportError:
    _GOOGLE_AVAILABLE = False


class GmailClient:
    """Client for interacting with Gmail API."""
    
    DEFAULT_TOKEN_PATH = Path.home() / ".config" / "hey-to-gmail" / "token.json"
    DEFAULT_CREDENTIALS_PATH = Path.home() / ".config" / "hey-to-gmail" / "credentials.json"
    MAX_RETRIES = 5
    RETRY_DELAY_BASE = 1  # seconds
    
    def __init__(self, service=None, token_path=None, credentials_path=None):
        """Initialize Gmail client.
        
        Args:
            service: Pre-authenticated Gmail API service (for testing)
            token_path: Path to store OAuth token (defaults to ~/.config/hey-to-gmail/token.json)
            credentials_path: Path to OAuth client secrets JSON file
        """
        self._token_path = token_path or self.DEFAULT_TOKEN_PATH
        self._credentials_path = credentials_path or self.DEFAULT_CREDENTIALS_PATH
        
        if service is not None:
            self._service = service
        else:
            self._service = self._authenticate()
    
    def _authenticate(self):
        """Authenticate with Gmail API using OAuth.
        
        Returns:
            Authenticated Gmail API service
            
        Raises:
            RuntimeError: If authentication fails and cannot be refreshed
        """
        if not _GOOGLE_AVAILABLE:
            raise RuntimeError(
                "Google API libraries not available. "
                "Install with: pip install google-auth google-auth-oauthlib google-api-python-client"
            )
        
        creds = None
        
        # Load existing token if available
        if self._token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self._token_path), SCOPES)
            except Exception as e:
                print(f"Warning: Could not load existing token: {e}")
        
        # If credentials are valid, use them
        if creds and creds.valid:
            return build('gmail', 'v1', credentials=creds)
        
        # If credentials expired but have refresh token, try to refresh
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self._save_token(creds)
                return build('gmail', 'v1', credentials=creds)
            except RefreshError as e:
                print(f"Token refresh failed: {e}")
                creds = None
        
        # If no valid credentials, run OAuth flow
        if not creds:
            if not self._credentials_path.exists():
                raise RuntimeError(
                    f"OAuth credentials file not found: {self._credentials_path}\n"
                    "Please download your client secrets JSON from Google Cloud Console "
                    "and save it to the above path."
                )
            
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self._credentials_path), SCOPES
                )
                creds = flow.run_local_server(port=0)
                self._save_token(creds)
                return build('gmail', 'v1', credentials=creds)
            except Exception as e:
                self._print_auth_failure_instructions()
                raise RuntimeError(f"Authentication failed: {e}")
        
        return build('gmail', 'v1', credentials=creds)
    
    def _save_token(self, creds):
        """Save credentials to token file with proper permissions."""
        # Ensure parent directory exists with restricted permissions
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_path.parent.chmod(0o700)
        
        # Save token data
        token_data = {
            'token': creds.token,
            'refresh_token': creds.refresh_token,
            'token_uri': creds.token_uri,
            'client_id': creds.client_id,
            'client_secret': creds.client_secret,
            'scopes': creds.scopes
        }
        
        with open(self._token_path, 'w') as f:
            json.dump(token_data, f)
        
        # Enforce permissions
        self._ensure_token_permissions()
    
    def _ensure_token_permissions(self):
        """Ensure token file has 0600 permissions."""
        if self._token_path.exists():
            os.chmod(self._token_path, 0o600)
    
    def _print_auth_failure_instructions(self):
        """Print instructions for re-authentication when auth fails."""
        print("\n" + "=" * 60)
        print("AUTHENTICATION FAILED")
        print("=" * 60)
        print("\nPlease run again to re-authenticate:")
        print(f"1. Delete {self._token_path}")
        print("2. Run the command again")
        print("3. Complete the browser OAuth consent flow")
        print("=" * 60 + "\n")
    
    def _execute_with_retry(self, operation, *args, **kwargs):
        """Execute an operation with exponential backoff retry.
        
        Retries on 429 (rate limit) and 5xx server errors.
        
        Args:
            operation: Callable to execute
            *args, **kwargs: Arguments to pass to operation
            
        Returns:
            Result of operation
            
        Raises:
            Exception: After max retries exhausted or on non-retryable error
        """
        last_exception = None
        
        for attempt in range(self.MAX_RETRIES):
            try:
                return operation(*args, **kwargs)
            except Exception as e:
                last_exception = e
                
                # Check if it's a retryable error
                status_code = None
                if hasattr(e, 'resp') and hasattr(e.resp, 'status'):
                    status_code = e.resp.status
                elif hasattr(e, 'code'):
                    status_code = e.code
                
                is_retryable = (
                    status_code == 429 or  # Rate limit
                    (status_code and 500 <= status_code < 600)  # Server error
                )
                
                if not is_retryable:
                    raise
                
                if attempt < self.MAX_RETRIES - 1:
                    # Exponential backoff: 1s, 2s, 4s, 8s, 16s
                    delay = self.RETRY_DELAY_BASE * (2 ** attempt)
                    time.sleep(delay)
        
        # Max retries exhausted
        raise last_exception
    
    def import_message(self, raw_bytes, label_ids=None):
        """Import a raw email message to Gmail.
        
        Uses users.messages.import (not insert) to preserve internal date.
        
        Args:
            raw_bytes: Raw email content as bytes
            label_ids: List of label IDs to apply to the message
            
        Returns:
            Gmail message ID on success
        """
        if self._service is None:
            raise RuntimeError("Gmail service not initialized")
        
        # Encode raw bytes to base64url
        encoded = base64.urlsafe_b64encode(raw_bytes).decode('utf-8')
        
        body = {
            'raw': encoded,
            'neverMarkSpam': True,
        }
        
        if label_ids:
            body['labelIds'] = label_ids
        
        def do_import():
            return self._service.users().messages().import_(
                userId='me',
                body=body
            ).execute()
        
        result = self._execute_with_retry(do_import)
        return result.get('id')
    
    def message_exists_by_rfc822msgid(self, rfc822msgid):
        """Check if a message exists by RFC822 Message-ID.
        
        Uses Gmail search query to check for duplicates.
        
        Args:
            rfc822msgid: Message-ID to search for (including angle brackets)
            
        Returns:
            True if message exists, False otherwise
        """
        if self._service is None:
            raise RuntimeError("Gmail service not initialized")
        
        # Build query for rfc822msgid
        query = f"rfc822msgid:{rfc822msgid}"
        
        def do_search():
            return self._service.users().messages().list(
                userId='me',
                q=query,
                maxResults=1
            ).execute()
        
        result = self._execute_with_retry(do_search)
        messages = result.get('messages', [])
        return len(messages) > 0
