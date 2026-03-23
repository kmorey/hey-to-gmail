"""Label manager for Gmail with in-process caching."""


class LabelManager:
    """Manages Gmail labels with caching."""
    
    def __init__(self, service=None):
        """Initialize label manager.
        
        Args:
            service: Gmail API service instance
        """
        self._service = service
        self._label_cache = {}  # name -> id mapping
    
    def ensure_label(self, name):
        """Ensure a label exists, creating it if necessary.
        
        Uses in-process cache to avoid repeated API calls.
        
        Args:
            name: Label name to ensure exists
            
        Returns:
            Label ID
        """
        if self._service is None:
            raise RuntimeError("Gmail service not initialized")
        
        # Check cache first
        if name in self._label_cache:
            return self._label_cache[name]
        
        # Query existing labels
        labels = self._service.users().labels().list(userId='me').execute()
        
        # Search for existing label
        for label in labels.get('labels', []):
            if label['name'] == name:
                self._label_cache[name] = label['id']
                return label['id']
        
        # Label doesn't exist, create it
        label_body = {
            'name': name,
            'labelListVisibility': 'labelShow',
            'messageListVisibility': 'show'
        }
        
        result = self._service.users().labels().create(
            userId='me',
            body=label_body
        ).execute()
        
        label_id = result['id']
        self._label_cache[name] = label_id
        return label_id
    
    def clear_cache(self):
        """Clear the label cache."""
        self._label_cache.clear()
