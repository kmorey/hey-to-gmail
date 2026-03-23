"""MBOX file reader with streaming support."""
import mailbox
import re
from email.message import Message
from pathlib import Path
from typing import Iterator, Tuple


class MboxReader:
    """Reads messages from MBOX files with metadata extraction."""
    
    def __init__(self, mbox_path: Path):
        """Initialize reader with MBOX file path."""
        self.mbox_path = Path(mbox_path)
    
    def stream_messages(self) -> Iterator[Tuple[int, Message, bytes]]:
        """Stream messages from MBOX file.
        
        Yields tuples of (message_index, email_message, raw_bytes).
        
        Args:
            None
            
        Yields:
            Tuple of (index, Message, raw_bytes)
        """
        mbox = mailbox.mbox(str(self.mbox_path))
        
        try:
            for index, msg in enumerate(mbox):
                # Unfold headers in the message to avoid issues with folded headers
                self._unfold_message_headers(msg)
                
                # Get raw bytes for import
                raw_bytes = self._get_raw_bytes(msg)
                
                yield index, msg, raw_bytes
        finally:
            mbox.close()
    
    def _unfold_message_headers(self, msg: Message) -> None:
        """Unfold folded headers in a message.
        
        RFC 5322 allows headers to be folded across multiple lines using
        CRLF followed by whitespace. This method unfolds them by replacing
        CRLF + whitespace with a single space.
        
        Args:
            msg: The message to unfold headers in (modified in place)
        """
        for key in list(msg.keys()):
            values = msg.get_all(key, [])
            # Remove all occurrences of this header
            del msg[key]
            # Re-add with unfolded values
            for value in values:
                # Unfold header: replace CRLF + whitespace with single space
                unfolded = re.sub(r'\r\n[ \t]+', ' ', value)
                msg.add_header(key, unfolded)
    
    def _get_raw_bytes(self, msg: Message) -> bytes:
        """Extract raw bytes from mailbox message."""
        # Try to get raw message as bytes
        try:
            # Convert message to string and encode
            msg_str = msg.as_string()
            return msg_str.encode('utf-8', errors='replace')
        except Exception:
            # Fallback: reconstruct from parts
            lines = []
            for key in msg.keys():
                for value in msg.get_all(key, []):
                    lines.append(f"{key}: {value}")
            lines.append("")
            
            payload = msg.get_payload()
            if isinstance(payload, str):
                lines.append(payload)
            elif isinstance(payload, bytes):
                lines.append(payload.decode('utf-8', errors='replace'))
            
            return "\n".join(lines).encode('utf-8')
    
    def get_file_metadata(self) -> Tuple[int, float]:
        """Get file metadata (size, mtime) for checkpointing.
        
        Returns:
            Tuple of (file_size, file_mtime)
        """
        stat = self.mbox_path.stat()
        return stat.st_size, stat.st_mtime
    
    def count_messages(self) -> int:
        """Count total messages in MBOX.
        
        Returns:
            Total number of messages
        """
        mbox = mailbox.mbox(str(self.mbox_path))
        try:
            count = len(mbox)
            return count
        finally:
            mbox.close()
