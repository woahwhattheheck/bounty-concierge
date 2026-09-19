# SPDX-License-Identifier: MIT
"""Load the existing claim/economic engine into one private source generation.

This is dependency isolation, not a sandbox for a compromised interpreter.
The package installation, interpreter and standard library are trusted. Public
business-module rebinding and helper-default mutation after initialization must
not change the installed claim boundary's economic decision.
"""
from __future__ import annotations


def _make_loader():
    import builtins
    import hashlib
    import os
    import stat
    import types
    from pathlib import Path

    # Pins are Git blob identities, including Git's length/type framing.
    # The live claim generation now retains the source-owned dollar-floor
    # ancestor in addition to fleet economics, effort/value, and claim binding.
    sources = (
        ("concierge._fleet_economic_admission_v1", "_fleet_economic_admission_v1.py",
         "48365b3e5726d1899a087d09e126a34206750eb0"),
        ("concierge.fleet_economic_admission", "fleet_economic_admission.py",
         "ca811aa321bd1e7963a8e5c879f5a1563e6b3a15"),
        ("concierge.paid_work_dollar_floor", "paid_work_dollar_floor.py",
         "c38b24c622d23c2fbb4229c45216072f7bc0e95b"),
        ("concierge.paid_work_effort_value_gate", "paid_work_effort_value_gate.py",
         "dd9d706026174658a5b7f2140a69926fac552fb8"),
        ("concierge._claim_economic_admission_impl", "_claim_economic_admission_impl.py",
         "557ba2ebd44a5c91c1674a4f545e4fde7aab0e2c"),
    )
    directory = Path(__file__).resolve().parent
    module_type = types.ModuleType
    import_module = builtins.__import__
    compile_source = builtins.compile
    execute = builtins.exec
    original_builtins = dict(vars(builtins))
    digest = hashlib.sha1
    open_fd, read_fd, close_fd = os.open, os.read, os.close
    fstat, lstat = os.fstat, os.lstat
    is_regular, is_link = stat.S_ISREG, stat.S_ISLNK
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0))
    max_bytes = 256 * 1024
    compare_ctime = os.name == "posix"

    def generation(st):
        identity = (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns)
        return identity + ((st.st_ctime_ns,) if compare_ctime else ())

    def source_bytes(filename, expected):
        path = directory / filename
        before = lstat(path)
        if is_link(before.st_mode) or not is_regular(before.st_mode):
            raise ImportError("economic generation source is not a regular file")
        if not 0 < before.st_size <= max_bytes:
            raise ImportError("economic generation source exceeds its byte bound")
        fd = open_fd(path, flags)
        try:
            opened = fstat(fd)
            if not is_regular(opened.st_mode) or generation(opened) != generation(before):
                raise ImportError("economic generation source changed before reading")
            chunks = []
            count = 0
            while True:
                chunk = read_fd(fd, min(65536, max_bytes + 1 - count))
                if not chunk:
                    break
                chunks.append(chunk)
                count += len(chunk)
                if count > max_bytes:
                    raise ImportError("economic generation source grew past its byte bound")
            if generation(fstat(fd)) != generation(opened):
                raise ImportError("economic generation source changed while reading")
            if generation(lstat(path)) != generation(opened) or count != opened.st_size:
                raise ImportError("economic generation source path changed while reading")
        finally:
            close_fd(fd)
        payload = b"".join(chunks)
        framed = b"blob " + str(len(payload)).encode("ascii") + b"\0" + payload
        if digest(framed).hexdigest() != expected:
            raise ImportError("economic generation does not match the installed source pins")
        return payload, str(path)

    def load(public_error):
        # Verify every member before evaluating any source. No public business
        # module in sys.modules is consulted, and no private member is inserted
        # there. A peer's monkeypatch cannot retarget a later internal import.
        try:
            retained = [(name, *source_bytes(filename, expected))
                        for name, filename, expected in sources]
            modules = {}
            package = module_type("concierge")
            standard_modules = {}

            def private_import(name, globals=None, locals=None, fromlist=(), level=0):
                if level:
                    raise ImportError("relative imports are outside the pinned generation")
                if name == "concierge":
                    if not fromlist or any("concierge." + item not in modules
                                           for item in fromlist):
                        raise ImportError("unretained economic dependency")
                    return package
                if name.startswith("concierge."):
                    if name not in modules or not fromlist:
                        raise ImportError("unretained economic dependency")
                    return modules[name]
                key = (name, tuple(fromlist or ()))
                if key not in standard_modules:
                    imported = import_module(name, globals, locals, fromlist, level)
                    # Snapshot module attributes so rebinding a public module's
                    # json/hashlib/re alias or its top-level functions is inert.
                    snapshot = module_type(imported.__name__)
                    snapshot.__dict__.update(vars(imported))
                    standard_modules[key] = snapshot
                return standard_modules[key]

            for name, payload, filename in retained:
                module = module_type(name)
                module.__file__ = filename
                module.__package__ = "concierge"
                module.__dict__["__builtins__"] = {
                    **original_builtins, "__import__": private_import,
                }
                modules[name] = module
                setattr(package, name.rsplit(".", 1)[1], module)
                execute(compile_source(payload, filename, "exec"), module.__dict__)
            implementation = modules["concierge._claim_economic_admission_impl"]
            private_verify = implementation.verify_claim_economic_receipt
            private_error = implementation.ClaimEconomicAdmissionError
        except (OSError, ImportError, SyntaxError, ValueError) as exc:
            # Never fall back to an already imported public compiler generation.
            raise ImportError("source-pinned claim economics generation unavailable") from exc

        def verify_claim_economic_receipt(
            repo, issue, payoff_proof, request_path, receipt_path, *, decision_as_of
        ):
            try:
                return private_verify(
                    repo, issue, payoff_proof, request_path, receipt_path,
                    decision_as_of=decision_as_of,
                )
            except private_error as exc:
                # Preserve the public exception contract without exposing the
                # independent implementation's exception/helper/module objects.
                raise public_error(exc.code, str(exc)) from None

        return verify_claim_economic_receipt

    return load


load_claim_generation = _make_loader()
del _make_loader
