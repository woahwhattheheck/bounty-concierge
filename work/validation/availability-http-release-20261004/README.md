# Availability reader: retained HTTP error releases its pool slot

The availability reader now closes a failed response obtained from a Requests
exception before wrapping the failure. A response hook can raise while
`Session.get()` is still running, before the reader receives the response or
Requests consumes its body. Keeping the resulting exception for a batch report
previously kept that unread response and its pool slot alive.

The change adds seven lines to `concierge/bounty_availability.py`. It preserves
the original exception as `__cause__`, its HTTP status and headers, and the
caller's ownership of its Session. Cleanup failures do not replace the original
provider error. No retry, cooldown, pagination, classification, or dispatch
behavior changes.

## Observed outcome

Native Python 3.12.14, Requests 2.34.2, loopback HTTP/1.1, one caller-owned
connection pool with one slot and blocking acquisition. A real response hook
raises the HTTP error; the wrapper and original error remain retained while
another read is attempted.

| Response | Before: available slots | After: available slots | Before next read | After next read |
| --- | ---: | ---: | --- | --- |
| 429 | 0 | 1 | blocked until manual release at 250 ms | completed, 1.224 ms |
| 500 | 0 | 1 | blocked until manual release at 250 ms | completed, 5.075 ms |

Every observation made exactly two local GETs. The second GET returned the same
healthy payload. The original HTTP exception object, status, and
`Retry-After: 120` header survived unchanged, and the caller Session was not
closed. A deliberately failing `close()` also preserved the original cause.

The 250 ms value is a harness observation window, **not** a provider timeout or
a measured speedup denominator. This establishes pool-slot release in the
actual Requests implementation; it does not measure live GitHub latency or
fleet-wide throughput. The healthy loopback read is not a retry of the failed
provider request.

## Reproduce only when needed

Preserve the baseline source from commit
`4f5bc01a9b6d6b91d5937d9743ed3608f9003ebc` and invoke:

```sh
python work/validation/availability-http-release-20261004/replay.py /path/to/baseline/bounty_availability.py concierge/bounty_availability.py
```

Baseline Git blob: `26203721782586a13e6aa2e0b3e3d5934b094888`.
Candidate Git blob: `c3b25c1e1e7a05670de53b398dfb40b1dd2f1051`.
Full observations and SHA-256 values are retained in `results.json`.

The recording imports the exact availability modules and unchanged config.
It exercises their HTTP reader with real Requests, not the installed CLI or a
live sponsor. No general test suite or hosted job was added. An initial
measurement used a differently keyed pool lookup and was discarded because it
could evict the real pool; the recorded replay inspects the existing pool
without creating another one.
