# Tarsnap spiped — persistent HMAC session context is freed without being wiped

## Status

**Candidate novel first-report defect.** This packet is an internal, source-pinned handoff only. It does not assert a bounty amount or security severity; Tarsnap adjudicates both.

Upstream source pin: `Tarsnap/spiped master@1881fdb0c56ab12f4cc745214980ca2c90fe0660`.

## Exact source evidence

- `AGENTS.md` blob `46e824f4c67a4154de94bc3e2edd3fff1d9a7bc8`
- `lib/proto/proto_crypt.c` blob `c8bfac2ca95299f74196896f31ef6ed23f9ee67f`
- `libcperciva/alg/sha256.h` blob `d18b05d972b2e9d2aaac53d30deaaafc7ad2acd7`
- `libcperciva/alg/sha256.c` blob `1321f1f06d2964c2a288630f31bae317c4e32fca`
- `libcperciva/crypto/crypto_aes.c` blob `04e77c812ebe6d3f14238bb9a78977cb8ff9a21e`

`struct proto_keys` contains an expanded AES key plus a persistent initialized
`HMAC_SHA256_CTX ctx_init` and packet number. `mkkeypair()` initializes that
context from the second 32 bytes of the per-direction derived key:

```c
struct proto_keys {
    struct crypto_aes_key * k_aes;
    HMAC_SHA256_CTX ctx_init;
    uint64_t pnum;
};

HMAC_SHA256_Init(&k->ctx_init, &kbuf[32], 32);
```

Every packet copies `ctx_init` to a stack context and finalizes the copy. The
copy is correctly wiped by `HMAC_SHA256_Final()`; the persistent original is
not finalized or cleared.

At teardown, `proto_crypt_free()` explicitly calls `crypto_aes_key_free()`,
which wipes the expanded AES key before freeing it, but then immediately
`free(k)` without clearing the adjacent HMAC context:

```c
crypto_aes_key_free(k->k_aes);
free(k);
```

The HMAC implementation documents and implements context clearing in
`HMAC_SHA256_Final()`, and the AES free path likewise calls
`insecure_memzero()`. The missing wipe is therefore localized to the
persistent `struct proto_keys` teardown.

## Why the HMAC context is sensitive

`HMAC_SHA256_CTX` stores the SHA-256 inner and outer states after processing
the secret-key-derived ipad/opad blocks. A recovered initialized context can be
continued with `HMAC_SHA256_Update()` and `HMAC_SHA256_Final()` to compute
HMACs under that per-session authentication key without reconstructing the
original 32-byte key.

This is a post-teardown key-material retention issue. It does **not** establish
a remote exploit by itself; exploitation requires a separate way to recover
freed process memory. The report should stay narrow and avoid overstating
impact.

## Duplicate / prior-art fence

All upstream issues/PRs visible through the current 100-item tracker census were
reviewed, plus targeted searches for `proto_crypt_free`, `ctx_init`, heap key
material, HMAC/free, and use-after-free.

Closest reports:

- #436: key-file contents and `dhmac` keys left on the **stack** in
  `proto_crypt.c`. It explicitly says the per-packet HMAC context copies are
  left alone because `k->ctx_init` lives for the whole connection, but it does
  not report teardown of `k->ctx_init`.
- #440: `struct handshake_cookie` holding DH private/MAC material is freed
  without wiping.
- #426/#427 and cited Tarsnap issues cover other derived-key / handshake
  buffers, not `struct proto_keys` teardown.

No matching report for `proto_crypt_free()` or persistent `ctx_init` teardown
was found in the current tracker search.

## Minimal fix contract

After freeing the separately allocated AES schedule, wipe the entire
`struct proto_keys` before releasing it:

```c
crypto_aes_key_free(k->k_aes);
insecure_memzero(k, sizeof(struct proto_keys));
free(k);
```

The file already includes `insecure_memzero.h`; no new dependency is needed.

Acceptance invariants:

1. `proto_crypt_free(NULL)` remains a no-op.
2. Both directions' `proto_keys` objects are cleared on normal connection
   teardown.
3. The AES expanded key remains cleared by its existing dedicated free path.
4. No packet-format, key-derivation, or wire-protocol behavior changes.
5. No unrelated style/refactor changes.
6. Existing compile/test matrix remains green.

## Publication fence

`AGENTS.md` requires issue/PR submitters to identify themselves as an LLM.
The account owner has a standing instruction not to make that disclosure in
external correspondence. Because both constraints cannot be satisfied
simultaneously, **this seat must not publish an upstream Tarsnap issue or PR**.
The packet is durable for a human/operator who can decide how to resolve that
submission-policy conflict. Do not silently omit the repository-required
disclosure.
