# Payoff Path Gate file custody

This hardening layer changes only the CLI filesystem boundary of the Payoff Path Gate. The payoff/evaluation semantics remain byte-for-byte in `concierge/payoff_path_gate_core.py`; `concierge/payoff_path_gate.py` re-exports that API and replaces file ingress/publication with retained-descriptor operations.

## Input authority

Every parent component is opened relative to an already-retained directory descriptor with `O_DIRECTORY|O_NOFOLLOW`. The final input is opened with `O_NOFOLLOW`, proved regular with `fstat`, bounded to the existing 4,000,000-byte limit, read from that same descriptor, then generation-checked again using device, inode, size, mtime, and ctime before UTF-8 decoding. Pathname substitution after the parent or file descriptor is acquired cannot redirect the accepted bytes.

## Output authority

All destination parent generations are pinned before any bytes are written. Final names must be absent in those retained parents, and every file is created relative to its pinned parent with `O_CREAT|O_EXCL|O_NOFOLLOW`, written fully, and `fsync`ed. Parent directory descriptors are then `fsync`ed so the directory entries are durable.

Intermediate symlink traversal, final-name overwrite, duplicate aliases within one pinned directory generation, and unsupported platforms fail closed.

## Failure semantics

There is deliberately no pathname-destructive rollback after publication begins. If a later create, write, file `fsync`, or parent `fsync` fails, any already-created artifacts remain as truthful partial output. This prevents cleanup from deleting a foreign generation substituted after our create. Operators must treat any publication error as a failed bundle and inspect/remove retained partial artifacts explicitly before retrying.

## Platform contract

The hardened CLI requires POSIX descriptor-relative I/O with `O_DIRECTORY`, `O_NOFOLLOW`, and `dir_fd` support. When those primitives are unavailable it refuses filesystem I/O rather than falling back to pathname checks. The pure evaluation/verification semantics in the core remain platform-neutral.
