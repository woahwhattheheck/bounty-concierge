# Tarsnap/Kivaloo first-report packet — S3 flush capacity leak

Status: **fresh source-backed bug; upstream issue publication blocked by GitHub App authorization**
Owner lane: `TARSNAP-KIVALOO-FRESH-S3-FLUSH-CAPACITY`
Reporter seat: `ZZ-Argus / GPT-5.6 Sol`

## Primary source pins

- Upstream repository: `Tarsnap/kivaloo`
- Upstream source baseline: `3de151b5d878b0714552c3658562eb5a87b378ce`
- Affected file: `lib/s3/s3_request_queue.c`
- Supporting caller: `s3/dispatch.c::dropconnection()`
- Queue lifetime proof: `s3/main.c`
- Cancellation contract: `libcperciva/http/http.h::http_request_cancel()`
- Sibling invariant: `lib/dynamodb/dynamodb_request_queue.c::dynamodb_request_queue_flush()`

Open + closed upstream issue searches for `reqsip`, `S3 queue flush`, `in-progress requests flush`, and `request queue capacity` found no prior report before the publication attempt.

## Bug

`s3_request_queue_flush()` cancels and frees every in-progress S3 HTTP request but does not decrement `Q->reqsip`, the queue's count of in-progress requests.

Normal completion in `callback_reqdone()` decrements the counter:

```c
/* The number of in-progress requests has just decreased. */
Q->reqsip -= 1;
```

The flush path cancels and frees each entry without the corresponding decrement. `http_request_cancel()` is explicitly documented to **not invoke the associated callback**, so `callback_reqdone()` cannot later repair the stale count.

The live S3 dispatcher makes this persistent across clients: `s3/dispatch.c::dropconnection()` calls `s3_request_queue_flush(D->Q)`, while `s3/main.c` creates one request queue before its accept loop and reuses that same `Q` for successive client connections.

`poke()` refuses to start more work when `Q->reqsip == Q->reqsip_max`.

## Deterministic failure sequence

With the default `-n 16`:

1. A client queues 16 S3 operations while their HTTP requests remain in progress. `Q->reqsip == 16`.
2. The client connection dies before those requests complete.
3. `dropconnection()` invokes `s3_request_queue_flush()`. All 16 HTTP operations are cancelled and freed, but `Q->reqsip` remains 16.
4. A later client queues a request.
5. `poke()` sees `Q->reqsip == Q->reqsip_max` and returns without launching it.
6. The cancelled callbacks cannot fire, so the queue remains stalled until process restart.

Disconnecting with fewer than `-n` in-flight requests permanently reduces available concurrency; repeated disconnects can accumulate to the same full stall.

## Cross-check

The analogous DynamoDB flush path explicitly decrements its in-flight counter for every cancelled HTTP request:

```c
if (R->http_cookie != NULL) {
    http_request_cancel(R->http_cookie);
    sock_addr_free(R->addrs[0]);
    Q->inflight--;
}
```

That is the accounting invariant missing from the S3 queue.

## Minimal repair carrier

Owner fork PR: https://github.com/woahwhattheheck/kivaloo/pull/24

- Exact reviewed head: `e5996b1a055914e4c22da30b6c983343ab300259`
- Change: `lib/s3/s3_request_queue.c`, +4/-0
- Repair: decrement `Q->reqsip` once per in-progress request removed by flush
- Hosted validation: GitHub Actions `Compile & test` run `35479904899`
- macOS clang: SUCCESS
- Ubuntu clang + tests: SUCCESS
- Ubuntu gcc + tests: SUCCESS
- Clean-tree checks: SUCCESS
- Squash-merged fork master: `fc9cdeeaca0c17572de15ce1f247a216bca9a7b8`
- Literal master file blob after merge: `7ba74c932df0a7eebdd497de4fd06e5139fccd7c`

The patch is supporting evidence only. Tarsnap's program pays the first reporter, not the patch author; exact bounty classification remains with the maintainer.

## Upstream issue publication receipt

Two calls to the installed GitHub issue-create action against `Tarsnap/kivaloo` were attempted. Both returned the same provider error:

```
403 Resource not accessible by integration
documentation: REST issues#create-an-issue
```

This is target-repository integration authorization, not absence of GitHub write tooling; writes to owned repositories are live.

**Publisher-capable next action:** create the upstream issue before a duplicate appears, using the draft below. Do not claim a payout or a security classification.

## Upstream issue draft

### Title

`[bug bounty] s3_request_queue_flush leaves reqsip raised, so client disconnects can permanently stall kivaloo-s3`

### Body

`s3_request_queue_flush()` cancels and frees every in-progress S3 HTTP request but does not decrement `Q->reqsip`, the queue's count of in-progress requests.

That counter is normally decremented by `callback_reqdone()`. The flush path calls `http_request_cancel()`, whose contract explicitly says the associated callback will not be invoked, so the missing decrement is never repaired later.

Because `kivaloo-s3` keeps one `s3_request_queue` alive across successive client connections, a client disconnect while S3 requests are in flight permanently consumes queue capacity. Once the stale count reaches `reqsip_max`, later requests remain queued forever because `poke()` returns immediately when `Q->reqsip == Q->reqsip_max`.

Source baseline: `3de151b5d878b0714552c3658562eb5a87b378ce`.

**Code path:** normal completion decrements `Q->reqsip`; flush cancels/frees the same in-progress requests without decrementing it. `http_request_cancel()` explicitly suppresses the callback. `s3/dispatch.c::dropconnection()` invokes flush on client disconnect, and `s3/main.c` reuses the same queue for later clients.

**Reproduction sequence:** with default `-n 16`, leave 16 S3 requests in flight and disconnect the client. Flush removes all 16 requests but leaves `reqsip == 16`. A later client's queued request is never launched because `poke()` believes the concurrency limit is still full. Since the cancelled callbacks cannot run, restart is required. Smaller disconnects leak partial capacity and can accumulate.

**Cross-check:** `dynamodb_request_queue_flush()` in the same repository does `Q->inflight--` for every cancelled HTTP request.

**Suggested fix:** decrement `Q->reqsip` once for each in-progress request removed by `s3_request_queue_flush()`.

A minimal one-file fix has been compiled and tested on the fork linked above. I am available to discuss or revise this report. I am treating this as an availability/accounting bug and am not claiming a security impact or a specific bounty tier.
