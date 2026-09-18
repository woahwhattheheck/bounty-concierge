# SPDX-License-Identifier: MIT
"""Boot-captured POSIX transport for the isolated reinvestment worker."""
from __future__ import annotations

import os
import select
import signal
import time
from typing import Any, Callable, Dict, Tuple

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - callers fail closed without POSIX support.
    _fcntl = None


def make_worker_invoker(
    *,
    error_type: type,
    canonical_json: Callable[[Any], bytes],
    parse_json: Callable[[bytes, str], Any],
    sealed_environment_items: Tuple[Tuple[str, str], ...],
    executable: str,
    worker_path: str,
    max_json_bytes: int,
    max_worker_stderr_bytes: int,
    max_worker_seconds: int,
):
    """Return a launch closure with every process primitive captured now."""
    os_error = OSError
    blocking_io_error = BlockingIOError
    timeout_error = TimeoutError
    posix_spawn = getattr(os, "posix_spawn", None)
    pipe_open = os.pipe
    fd_close = os.close
    fd_write = os.write
    fd_read = os.read
    set_blocking = os.set_blocking
    waitpid = os.waitpid
    waitstatus_to_exitcode = os.waitstatus_to_exitcode
    kill = os.kill
    select_fds = select.select
    monotonic = time.monotonic
    sleep = time.sleep
    # POSIX-only constant; the kill sites below are unreachable on hosts
    # without posix_spawn because the capability gate raises first.
    sigkill = getattr(signal, "SIGKILL", None)
    spawn_dup2 = getattr(os, "POSIX_SPAWN_DUP2", None)
    spawn_close = getattr(os, "POSIX_SPAWN_CLOSE", None)
    wait_nohang = getattr(os, "WNOHANG", None)
    fcntl_call = getattr(_fcntl, "fcntl", None) if _fcntl is not None else None
    dupfd_cloexec = (
        getattr(_fcntl, "F_DUPFD_CLOEXEC", None) if _fcntl is not None else None
    )

    def worker_error(stderr: bytes, returncode: int) -> str:
        bounded = stderr[-max_worker_stderr_bytes:]
        try:
            parsed = parse_json(bounded, "worker error")
            if type(parsed) is dict and type(parsed.get("error")) is str:
                text = parsed["error"].strip()
                if text:
                    return text[:1000]
        except error_type:
            pass
        text = bounded.decode("utf-8", errors="replace").strip()
        if text:
            return text[-1000:]
        return f"isolated worker exited with status {returncode}"

    def invoke_worker(request: Dict[str, Any]) -> Any:
        if posix_spawn is None or spawn_dup2 is None or spawn_close is None:
            raise error_type(
                "isolated authority worker requires POSIX spawn support"
            )
        payload = canonical_json(request)
        in_r = in_w = out_r = out_w = err_r = err_w = -1
        pid = None

        def close_fd(fd: int) -> None:
            if fd >= 0:
                try:
                    fd_close(fd)
                except os_error:
                    pass

        try:
            in_r, in_w = pipe_open()
            out_r, out_w = pipe_open()
            err_r, err_w = pipe_open()

            # A process may enter with one of stdin/stdout/stderr already closed,
            # allowing pipe() to allocate descriptor 0, 1, or 2.  Normalize every
            # pipe endpoint above the standard range before building file actions;
            # otherwise a later close action could accidentally close a dup2 target.
            pipe_fds = [in_r, in_w, out_r, out_w, err_r, err_w]
            if any(fd <= 2 for fd in pipe_fds):
                if fcntl_call is None or dupfd_cloexec is None:
                    raise error_type(
                        "isolated authority worker cannot normalize standard descriptors"
                    )
                normalized = []
                try:
                    for fd in pipe_fds:
                        if fd <= 2:
                            replacement = fcntl_call(fd, dupfd_cloexec, 3)
                            fd_close(fd)
                            normalized.append(replacement)
                        else:
                            normalized.append(fd)
                except os_error:
                    for fd in normalized:
                        close_fd(fd)
                    raise
                in_r, in_w, out_r, out_w, err_r, err_w = normalized

            file_actions = [
                (spawn_dup2, in_r, 0),
                (spawn_dup2, out_w, 1),
                (spawn_dup2, err_w, 2),
                (spawn_close, in_w),
                (spawn_close, out_r),
                (spawn_close, err_r),
                (spawn_close, in_r),
                (spawn_close, out_w),
                (spawn_close, err_w),
            ]
            pid = posix_spawn(
                executable,
                [executable, "-I", worker_path],
                dict(sealed_environment_items),
                file_actions=file_actions,
            )
            close_fd(in_r)
            in_r = -1
            close_fd(out_w)
            out_w = -1
            close_fd(err_w)
            err_w = -1

            deadline = monotonic() + max_worker_seconds
            set_blocking(in_w, False)
            offset = 0
            while offset < len(payload):
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise timeout_error
                _, writable, _ = select_fds([], [in_w], [], remaining)
                if not writable:
                    raise timeout_error
                try:
                    written = fd_write(in_w, payload[offset:])
                except blocking_io_error:
                    continue
                if written <= 0:
                    raise error_type(
                        "isolated authority worker stdin closed early"
                    )
                offset += written
            close_fd(in_w)
            in_w = -1

            set_blocking(out_r, False)
            set_blocking(err_r, False)
            open_fds = {out_r, err_r}
            stdout_chunks: List[bytes] = []
            stderr_tail = b""
            stdout_size = 0

            while open_fds:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise timeout_error
                ready, _, _ = select_fds(list(open_fds), [], [], remaining)
                if not ready:
                    raise timeout_error
                for fd in ready:
                    try:
                        chunk = fd_read(fd, 65_536)
                    except blocking_io_error:
                        continue
                    if not chunk:
                        close_fd(fd)
                        open_fds.remove(fd)
                        if fd == out_r:
                            out_r = -1
                        elif fd == err_r:
                            err_r = -1
                        continue
                    if fd == out_r:
                        stdout_size += len(chunk)
                        if stdout_size > max_json_bytes + 1024:
                            raise error_type(
                                "isolated authority worker response is too large"
                            )
                        stdout_chunks.append(chunk)
                    else:
                        stderr_tail = (stderr_tail + chunk)[
                            -max_worker_stderr_bytes:
                        ]

            if wait_nohang is None:
                raise error_type("isolated authority worker requires waitpid polling")
            status = None
            while status is None:
                waited, candidate_status = waitpid(pid, wait_nohang)
                if waited == pid:
                    status = candidate_status
                    break
                if monotonic() >= deadline:
                    raise timeout_error
                sleep(0.01)
            pid = None
            returncode = waitstatus_to_exitcode(status)
            stdout = b"".join(stdout_chunks)
            if returncode != 0:
                raise error_type(
                    "isolated authority worker rejected request: "
                    + worker_error(stderr_tail, returncode)
                )
            response = parse_json(stdout, "worker response")
            if type(response) is not dict:
                raise error_type(
                    "isolated worker returned a non-object"
                )
            return response
        except timeout_error as exc:
            if pid is not None:
                try:
                    kill(pid, sigkill)
                except os_error:
                    pass
                try:
                    waitpid(pid, 0)
                except os_error:
                    pass
                pid = None
            raise error_type(
                "isolated authority worker exceeded its execution deadline"
            ) from exc
        except error_type:
            if pid is not None:
                try:
                    kill(pid, sigkill)
                except os_error:
                    pass
                try:
                    waitpid(pid, 0)
                except os_error:
                    pass
                pid = None
            raise
        except os_error as exc:
            if pid is not None:
                try:
                    kill(pid, sigkill)
                except os_error:
                    pass
                try:
                    waitpid(pid, 0)
                except os_error:
                    pass
                pid = None
            raise error_type(
                "isolated authority worker could not be started or completed"
            ) from exc
        finally:
            for fd in (in_r, in_w, out_r, out_w, err_r, err_w):
                close_fd(fd)

    return invoke_worker
