# SPDX-License-Identifier: MIT
"""Optional private page cache; entries are usable only after provider revalidation."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

_MAX_BYTES = 8 * 1024 * 1024
_ETAG = re.compile(r'(?:W/)?"[!#-~]{1,1000}"')


def valid_etag(value):
    return isinstance(value, str) and _ETAG.fullmatch(value) is not None


def same_validator(left, right):
    """If-None-Match uses weak comparison, including for a strong stored ETag."""
    if not valid_etag(left) or not valid_etag(right):
        return False
    return (left[2:] if left.startswith("W/") else left) == (
        right[2:] if right.startswith("W/") else right
    )


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class PageCache:
    """Atomic, bounded cache of parser inputs, not an offline bounty authority.

    Keys separate repository, page, API representation and authorization. Raw
    credentials, response headers and unrelated GitHub account fields are not
    retained. A checksum detects accidental damage; it is not authentication.
    """

    def __init__(self, directory, token):
        try:
            self.root = Path(directory).expanduser()
        except (TypeError, ValueError) as exc:
            raise ValueError("cache_dir must be a directory path or False") from exc
        self.partition = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()

    def _key(self, repo, page):
        identity = ["github-issues-bounty-open-json-v1", repo.casefold(), page, 100, self.partition]
        return hashlib.sha256(_bytes(identity)).hexdigest()

    def load(self, repo, page):
        """Return (entry, cache_error). A miss/error must cause an ordinary GET."""
        key = self._key(repo, page)
        try:
            flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.root / (key + ".json"), flags)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BYTES:
                    return None, True
                # Size the ordinary read to this file, not the 8 MiB ceiling.
                raw = stream.read(info.st_size + 1)
                if len(raw) > info.st_size:
                    # Preserve bounded reads if an in-place writer grew it
                    # after fstat; cache.store itself uses atomic replacement.
                    raw += stream.read(_MAX_BYTES + 1 - len(raw))
            if len(raw) > _MAX_BYTES:
                return None, True
            record = json.loads(raw)
            del raw  # Parsed values no longer need the serialized input buffer.
            if not isinstance(record, dict) or set(record) != {"entry", "sha256"}:
                return None, True
            entry = record["entry"]
            if (not isinstance(entry, dict)
                    or set(entry) != {"key", "etag", "issues", "has_next"}
                    or entry["key"] != key or not valid_etag(entry["etag"])
                    or type(entry["has_next"]) is not bool
                    or not isinstance(entry["issues"], list) or len(entry["issues"]) > 100
                    or record["sha256"] != hashlib.sha256(_bytes(entry)).hexdigest()):
                return None, True
            return entry, False
        except FileNotFoundError:
            return None, False
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
            return None, True

    def store(self, repo, page, etag, issues, has_next):
        """Return stored/skipped/error; cache failure never loses a live page."""
        if not valid_etag(etag) or len(issues) > 100:
            return "skipped"
        key = self._key(repo, page)
        temporary = None
        try:
            entry = {"key": key, "etag": etag, "issues": issues, "has_next": bool(has_next)}
            # Reuse the canonical entry bytes for both the checksum and envelope.
            entry_data = _bytes(entry)
            data = b"".join((b'{"entry":', entry_data, b',"sha256":"',
                             hashlib.sha256(entry_data).hexdigest().encode("ascii"), b'"}'))
            del entry_data
            if len(data) > _MAX_BYTES:
                return "skipped"
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            # Readers retain their own entry while another process replaces it.
            # A failed writer can only remove its own temporary file.
            with tempfile.NamedTemporaryFile(dir=self.root, prefix=".bounty-page-", delete=False) as stream:
                temporary = stream.name
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.root / (key + ".json"))
            temporary = None
            return "stored"
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
            return "error"
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
