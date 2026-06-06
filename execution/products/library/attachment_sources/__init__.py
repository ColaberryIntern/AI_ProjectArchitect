"""Source adapters for colaberry_attachment_fetch.

Each adapter has a single public function that takes the source-specific
identifiers + an auth token and returns a normalized FetchedAttachment.

  - gmail.fetch(message_id, attachment_id, access_token) -> FetchedAttachment
  - basecamp.fetch(project_id, recording_id, sgid, bc_token) -> FetchedAttachment
  - drive.fetch(drive_file_id, access_token) -> FetchedAttachment  (metadata only;
    no bytes, since Drive passthrough doesn't re-stage)

See directives/colaberry-attachment-fetch.md for the contract + edge cases.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class FetchedAttachment:
    """Normalized return shape from all source adapters."""
    filename: str
    mime_type: str
    size_bytes: int
    sender: str           # human-readable: From: header (Gmail), project name (BC), file owner (Drive)
    data: Optional[bytes] = None   # None for source=drive passthrough -- no re-upload needed
    drive_file_id: Optional[str] = None  # set when source=drive (passthrough returns ref directly)
    drive_url: Optional[str] = None      # set when source=drive
