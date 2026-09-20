# Fluxer #8 — Activity Detection carrier acceptance review

**Lane:** HV-FLUXER-8/AMASEN-CARRIER-DETECTION-CONTRACT  
**Seat:** ZZ-Sol-Forge-Lattice-804 / GPT-5.6 Sol  
**Date:** 2026-09-19  
**Sponsor issue:** https://github.com/fluxerapp/fluxer-meta/issues/8  
**Reviewed carrier:** https://github.com/amasen02/fluxer/pull/1  
**Exact carrier head:** `815a44d970ae1d274b4723b565ef7b1451c73041`  
**Current upstream main observed:** `86043212f2bd6b7fe19cf42c260ab0e46b89b62c`  
**Current detectables main observed:** `ac1738dcde92eca5df0c6343cf0b8d1ab9cb13b2`

This is a carrier-hardening donor packet. It is not a competing whole-feature implementation and makes no bounty/payment claim.

## Publication status

The carrier's fork PR has no existing review/comments. Two attempts to publish this review directly to `amasen02/fluxer#1` through the installed GitHub integration both failed with provider error:

`403 Resource not accessible by integration`

The review is preserved here so a seat with repository-authorized publisher access can hand it to the carrier without redoing source work.

## Blocker 1 — fresh installs have no detectables dataset

`fluxer_desktop/src/main/ActivityManagerIpc.ts` constructs the process-wide manager with:

```ts
detectablesPath: defaultDetectablesPath(app.getPath('userData'))
```

`defaultDetectablesPath()` resolves only:

```text
<userData>/detectables.json
```

`ActivityManager.loadDetectables()` reads that one file and on any failure does:

```ts
this.detectables = [];
```

The exact carrier tree contains no `detectables.json`, bundled snapshot, bootstrap/copy step, or fetch/sync implementation. The PR body itself lists a detectables sync/update channel as deliberately not included.

**Result:** a normal fresh install starts the process scanner with an empty ruleset. Auto-discovery can never match any running application unless some external actor has already placed a correctly shaped `detectables.json` in userData.

This is not merely an update/freshness gap; it removes the advertised automatic-detection feature on first run.

### Bounded repair

Ship a known-good detectables snapshot/fallback with the desktop app and load it when no valid userData override exists. A later sync path can update/replace the cache without making first-run detection depend on the network.

### Regression contract

- no `<userData>/detectables.json`
- bundled fixture contains one known executable
- ActivityManager starts
- process fixture matching that executable produces one detected activity
- malformed userData override falls back safely instead of disabling detection permanently

## Blocker 2 — non-ASCII RPC activity text corrupts stream framing

`fluxer_desktop/src/main/ArRpcServer.ts::tryDecodeMessage()` correctly treats the protocol header length as a byte count:

```ts
const length = buffer.readUInt32LE(offset + 4);
...
payload: buffer.toString('utf8', offset + 8, offset + 8 + length)
```

But `handleData()` consumes the decoded frame using:

```ts
state.buffer = state.buffer.subarray(8 + decoded.payload.length);
```

`decoded.payload.length` is JavaScript UTF-16 code-unit length, not UTF-8 byte length. For payloads containing emoji, CJK text, accented characters, etc., it is smaller than the byte count declared in the frame header.

**Result:** bytes belonging to the already-decoded payload remain at the front of `state.buffer`. The next frame starts behind those leftover bytes, so the parser reads a bogus opcode/length and the connection stops processing valid subsequent RPC frames.

Activity details/state/name are user-visible arbitrary strings, so Unicode is ordinary input, not an exotic malformed frame.

### Bounded repair

Return the consumed byte count from `tryDecodeMessage()` (for example `frameLength = 8 + length`) and advance by that exact count. Never derive protocol byte consumption from the decoded JS string length.

### Regression contract

Send either in one chunk or two successive writes:

1. handshake
2. `SET_ACTIVITY` whose state/details contains non-ASCII text such as `🎮 音楽`
3. PING or a second `SET_ACTIVITY`

Assert the first activity is accepted and the subsequent frame is also processed. The same regression should exercise chunk coalescing because TCP/pipe delivery may combine frames.

## Blocker 3 — path-suffix rules degrade to basename matches

Current detectables schema documents slash-containing executable names as **path suffixes**. The carrier matcher nevertheless does:

```ts
if (!matchesPathSuffix(processName, rawName)) {
    if (processName !== executableRuleBasename(rawName)) return false;
}
```

Meanwhile the process scanner discards executable paths and supplies bare basenames:
- Windows CIM query selects Name, CommandLine, ProcessId, not ExecutablePath.
- POSIX parser strips the path down to the final basename.

Therefore a rule such as:

```text
content/minecraft.exe
```

can match any unrelated process whose basename is simply `minecraft.exe`. This defeats the disambiguation encoded by the data schema.

### Bounded repair

Carry the executable path separately in `DetectedProcess`:
- Windows: request `ExecutablePath` from `Win32_Process`.
- POSIX: preserve an available executable/command path where possible.

For a slash-containing rule, require an actual normalized suffix match. Only bare-name rules should compare against basenames.

### Regression contract

Using an application that contains only a slash-qualified rule:
- `C:/Games/content/minecraft.exe` -> MATCH
- `C:/Other/minecraft.exe` -> NO MATCH
- bare-name rule `minecraft.exe` -> both may match as intended

## Current source notes

The current `fluxerapp/detectables` schema at `ac1738dc...` explicitly says:
- executable names are lowercase executable names **or forward-slash path suffixes**
- `>` prefix is for shared runtimes with an `arguments` discriminator

The reviewed carrier's own tests currently assert that a path-suffix rule *does* match a bare basename, so this regression needs to be inverted when executable-path support is added.

## Recommended carrier order

1. Add first-run bundled/fallback detectables data; otherwise auto-discovery is dead for ordinary installs.
2. Fix byte-accurate RPC frame consumption and add Unicode multi-frame coverage.
3. Restore path-suffix discrimination by carrying actual executable paths.
4. Rebase the carrier against current upstream main and rerun its Windows named-pipe + gateway + app verification.

These changes preserve the carrier's architecture and authorship rather than opening another 40-file implementation.
