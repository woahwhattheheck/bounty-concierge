# Kivaloo: dump/undump reject valid zero-length values

**Lane:** HV-TARSNAP-KIVALOO-ZEROLEN-DUMP-AUDIT  
**Owner:** ZZ-Sol-Forge / GPT-5.6 Sol  
**Date:** 2026-09-19

## Publication status

This is a durable internal report packet, not an upstream submission or bounty claim.

The target repository's `AGENTS.md` requires issue/PR authors to publicly identify themselves as an LLM. The account's correspondence policy forbids making that disclosure. This packet therefore stops before upstream publication rather than violating either constraint.

## Exact source provenance

Current upstream `Tarsnap/kivaloo` master:

`3de151b5d878b0714552c3658562eb5a87b378ce`

The owner fork has unrelated later merges at `998a4142500a15af5144416d6cdd01d89f4d47bf`, but every relevant file is byte-identical to current upstream:

- `kvlds-dump/main.c` blob `003f6243c8219f67f7359896250dc408a8f44b0c`
- `kvlds-undump/main.c` blob `36b2542e4a9cc78dd36b2e8bdf51c0f700a4366e`
- `lib/datastruct/kvldskey.h` blob `01d11d6e956179daf8ba65bc237bb8ef494c66f4`
- `tests/kvlds-dump/test_kvlds_dump.sh` blob `ba967f777ba4446e31a14fa6d840f2c5a6aa1f87`

## Bug

KVLDS explicitly defines a key or value as **0–255 bytes**:

```c
/* Structure for KVLDS key or value (0-255 bytes). */
struct kvldskey {
    uint8_t len;
    uint8_t buf[];
};
```

Both dump formats and both undump formats nevertheless reject a valid zero-length value.

### Filesystem dump

`kvlds-dump/main.c::writefile()` does:

```c
if (fwrite(v->buf, v->len, 1, f) != 1)
    goto err2;
```

For `v->len == 0`, standard stdio performs no transfer and `fwrite(..., 0, 1, ...)` returns zero. The code interprets that normal zero-length result as a write failure.

### Stream dump

`callback_pair()` writes the length byte correctly, then immediately performs:

```c
if (fwrite(value->buf, value->len, 1, stdout) != 1)
    goto err0;
```

A legitimate value serialized as length byte `0x00` therefore aborts the dump after the length byte.

### Filesystem undump

`kvlds-undump/main.c::readfile()` accepts regular files of size up to 255, including size zero, then does:

```c
if (fread(buf, (size_t)sb.st_size, 1, f) != 1)
    goto err2;
```

For an empty `v` file, `fread(..., 0, 1, ...)` returns zero and the loader rejects the valid value before `kvldskey_create(buf, 0)`.

### Stream undump

The stream parser reads the one-byte length, then unconditionally requires:

```c
if (fread(&buf, len, 1, stdin) != 1)
    goto err1;
```

A canonical empty value frame has `len == 0`; this again returns zero and is treated as truncation.

## Independent stdio witness

A minimal C99 witness was compiled with:

```sh
cc -std=c99 -Wall -Wextra -Werror zeroio.c -o zeroio
./zeroio
```

Observed:

```text
fwrite(size=0,nmemb=1)=0
fread(size=0,nmemb=1)=0
```

This is not a full Kivaloo build/test run. It isolates the exact library semantic on which all four failure paths depend.

## Impact

A stored KVLDS entry with a valid empty value cannot be exported by `kvlds-dump` in either filesystem or stream mode. Conversely, a correctly represented empty value cannot be imported by `kvlds-undump` from either format.

That breaks the advertised dump/restore round trip for a representable KVLDS value and can prevent operators from successfully exporting an otherwise valid database.

The existing `tests/kvlds-dump/test_kvlds_dump.sh` covers five non-empty values only, so it does not exercise this boundary.

## Minimal regression

Use a **non-empty key and empty value** so the test does not depend on any special semantics of an empty key:

```sh
mkdir "$WRKDIR/input/empty"
printf 'empty-key' > "$WRKDIR/input/empty/k"
: > "$WRKDIR/input/empty/v"
```

Required outcomes after a fix:

1. filesystem undump accepts the empty `v` file;
2. filesystem dump recreates a zero-byte `v` file;
3. stream dump emits the key length/key bytes followed by value length `0x00` and succeeds;
4. stream undump accepts that frame and restores the same empty value;
5. truncated positive-length payloads still fail.

## Bounded fix

Skip the payload transfer when the declared length is zero, while preserving the current strict check for every positive length.

Examples:

```c
if ((v->len != 0) && (fwrite(v->buf, v->len, 1, f) != 1))
    ...
```

```c
if ((sb.st_size != 0) &&
    (fread(buf, (size_t)sb.st_size, 1, f) != 1))
    ...
```

and the analogous guards in the stream dump/undump path.

Do not weaken the one-byte length reads or any positive-length short-read/write checks.

## Economic boundary

Tarsnap's public policy says ordinary non-harmless bugs may be classified at **$50**, but awards below $100 are Tarsnap account credit rather than cash. Classification and eligibility are entirely maintainer decisions; this packet makes no award or payment claim.

## Duplicate boundary

Fresh GitHub-web and Slack searches on 2026-09-19 found no report specifically about `kvlds-dump` / `kvlds-undump` rejecting zero-length values. Nearby Tarsnap work involving zero-length stdio transfers is in different components (for example ccache) and does not touch these Kivaloo dump utilities.
