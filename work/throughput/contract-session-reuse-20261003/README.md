# Reuse the HTTP pool during contract capture

The default transport in `concierge/bounty_contract_hardening.py::_read_stable_generation` now owns one `requests.Session` for its complete read. The public capture and verify functions already call this helper. Caller-supplied transports retain their existing ownership, and the generation, pagination, identity, and receipt checks retain their existing paths.

The source change adds eight lines. The accompanying benchmark executes the public `capture_contract` and `verify_contract` functions with the package's normal bootstrap and actual Requests HTTP/1.1 transport over local TLS. It selects the unchanged and changed helper implementations for comparison. It does not substitute response objects or skip parsing and receipt generation.

## Retained measurement

The deterministic provider fixture in `benchmark.py` contains one issue and 101 comments, requiring two comment pages on each of the two passes. Each public call performs seven GETs: three issue reads and four comment-page reads. A local TLS server counts accepted HTTPS connections. A temporary certificate verifies the original `api.github.com` hostname; scoped DNS resolution sends that hostname to the local server. Proxy and netrc discovery are disabled only in the benchmark sessions, and a fixed fixture token is used. The temporary certificate and private key are deleted.

Python 3.12.14, Requests 2.34.2, 15 alternating samples per operation, after initial complete capture and verify executions:

| Public call | Before median | After median | HTTPS connections before / after | GETs before / after |
| --- | ---: | ---: | ---: | ---: |
| Capture | 23.171600 ms | 11.082200 ms | 7 / 1 | 7 / 7 |
| Verify | 22.322278 ms | 11.559431 ms | 7 / 1 | 7 / 7 |

`results.json` retains every timing, source and fixture hashes, connection counts, and the execution outcomes. Fixed-time capture receipts and verification documents were byte-equivalent after canonical JSON serialization.

The same execution also covered a comment-body edit within an unchanged timestamp (`LIVE_GENERATION_UNSTABLE`), an incomplete comment count (`LIVE_EVIDENCE_INCOMPLETE`), and HTTP 503. The first two retained their `HOLD` result, and all three closed the owned session. An injected real session stayed open and served a further GET before its caller closed it.

These are local provider-fixture measurements. They do not measure live GitHub latency, payment or settlement behavior, hosted CI, or deployment performance. No provider writes occur.

## Reproduce

From this repository, with its Python dependencies installed and `openssl` available:

```sh
git show ca18acc2e914952b401f8a3c4995d0598f68790e:concierge/bounty_contract_hardening.py > /tmp/contract-hardening-before.py
PYTHONPATH=. python3 work/throughput/contract-session-reuse-20261003/benchmark.py --baseline /tmp/contract-hardening-before.py --samples 15
```

Baseline source blob: `128bca7519ecb38649a58a517c0deda36687c16f`.

Changed source blob: `b268e9eb556c891e23d8cc2f48775fedc15fe554`.

The execution package comprised 150 source files verified against the fetched Git tree `dd67fd238a7ecf5cc5a85a604e5757b56197d8fa`, with only the helper changed. Other fleet source directories were read without modification. The benchmark emits its actual Python, Requests, OpenSSL, source, and fixture identifiers on each run.
