# SPDX-License-Identifier: MIT
"""Optional private page cache; entries are usable only after provider revalidation."""

from collections.abc import Iterator
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

_MAX_BYTES = 8 * 1024 * 1024
# RFC 9110 section 8.8.3 permits an empty opaque tag.
_ETAG = re.compile(r'(?:W/)?"[!#-~]{0,1000}"')


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


def _entry_chunks(entry: dict) -> Iterator[bytes]:
    """Yield canonical entry bytes, batching text-heavy issue pages."""
    issues = entry["issues"]
    # JSON's ASCII escaping can expand a non-BMP code point to 12 bytes.
    # This shallow estimate selects an optimization, not cache admission.
    text_size = sum(
        len(value) for issue in issues if type(issue) is dict
        for value in issue.values() if type(value) is str
    ) if type(issues) is list and len(issues) > 8 else 0
    if text_size < _MAX_BYTES // 12:
        yield _bytes(entry)
        return

    prefix = _bytes({"etag": entry["etag"], "has_next": entry["has_next"]})[:-1]
    yield prefix + b',"issues":['
    for offset in range(0, len(issues), 8):
        if offset:
            yield b","
        yield _bytes(issues[offset:offset + 8])[1:-1]
    yield b'],"key":' + _bytes(entry["key"]) + b'}'


def _entry_parts(entry: dict) -> list[bytes] | None:
    """Encode within the page bound, still validating all oversized values.

    This is not a bound on individual or deeply nested values. Store and load
    use the same canonical chunks so existing checksums remain compatible.
    """
    limit = _MAX_BYTES - len(b'{"entry":,"sha256":""}') - 64
    size = 0
    parts = []
    for chunk in _entry_chunks(entry):
        size += len(chunk)
        if size > limit:
            parts.clear()
        else:
            parts.append(chunk)
    if size > limit:
        return None
    return parts


def _entry_sha256(entry: dict, file_size: int) -> str:
    """Verify a large page without retaining its entire re-encoded buffer."""
    # Avoid even the shallow issue scan for an ordinary serialized page.
    # File size only selects an optimization; it never admits cached data.
    if file_size < _MAX_BYTES // 12:
        return hashlib.sha256(_bytes(entry)).hexdigest()
    digest = hashlib.sha256()
    for chunk in _entry_chunks(entry):
        digest.update(chunk)
    return digest.hexdigest()


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
            # Match json.loads' byte decoding, but release the input bytes
            # before parsing allocates the issue dictionaries and strings.
            raw = raw.decode(json.detect_encoding(raw), 'surrogatepass')
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
                    or record["sha256"] != _entry_sha256(entry, info.st_size)):
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
            # Validate before touching the filesystem, retaining no aggregate copies.
            parts = _entry_parts(entry)
            if parts is None:
                return "skipped"
            digest = hashlib.sha256()
            for part in parts:
                digest.update(part)
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            # Readers retain their own entry while another process replaces it.
            # A failed writer can only remove its own temporary file.
            with tempfile.NamedTemporaryFile(dir=self.root, prefix=".bounty-page-", delete=False) as stream:
                temporary = stream.name
                stream.write(b'{"entry":')
                stream.writelines(parts)
                stream.write(b',"sha256":"' + digest.hexdigest().encode("ascii") + b'"}')
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
