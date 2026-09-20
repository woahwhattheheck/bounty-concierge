# Build order — IVRIT-PY-13 / 200 NIS real batching

Status: **READY_PRECLAIM_LOCK_REQUIRED**

Target: https://github.com/ivrit-ai/ivrit-py/issues/13  
Pinned source for this order: `76cac007abbf3e4ab2bea9fc28495d583d8a7926`

## Stage 0 — claim/lock gate

A fork-capable, benchmark-capable seat should first:
1. re-read #13 and repository bounty rules;
2. verify no maintainer lock/assignment or applicable implementation carrier appeared;
3. post one concise scope/benchmark proposal to the issue;
4. **wait for explicit maintainer lock/assignment**.

Do not code for bounty eligibility before the lock. If the maintainer declines, selects another contributor, changes economics below the fleet floor, or requires paid resources without owner approval: RELEASE.

Suggested scope question:
- confirm whether the >=4x criterion is measured across multiple input files, within-file segments, or both;
- confirm preferred model/corpus/device/precision/decoding settings for acceptance;
- confirm accuracy comparison metric/corpus and payout route/timing.

## Stage 1 — baseline

After lock:
- pin exact upstream source, faster-whisper version, model, device/GPU, precision, language, decoding settings, and corpus;
- benchmark the existing public `transcribe(path=[...])` path with outputs fully consumed;
- use repeated timed runs after warm-up; report wall time, audio duration, files/hour or real-time factor, peak memory, and failures;
- preserve input order and attribution.

## Stage 2 — implementation

Replace sequential local faster-whisper batch execution with the smallest reviewable true batching seam.

Likely starting point, to revalidate rather than blindly copy:
- preserve single-input behavior;
- use faster-whisper's supported batching primitive (`BatchedInferencePipeline` or current canonical equivalent);
- forward only compatible inference kwargs deliberately;
- keep list result ordering and per-input attribution explicit;
- keep per-item failure behavior well-defined;
- do not silently change streaming or diarization contracts.

Prior closed PR #23 is a donor/reference only, not authority.

## Stage 3 — funded acceptance evidence

The PR must not claim success without measured evidence for both sponsor requirements:

### Throughput
- sequential baseline vs true batch on identical corpus/settings/hardware;
- >=4.0x throughput improvement according to the maintainer-confirmed metric;
- at least 3 measured runs after warm-up;
- raw timing table and aggregation method in the PR.

### Accuracy
- paired outputs from baseline and batched path;
- maintainer-confirmed accuracy metric (prefer WER/CER when references exist; otherwise exact/normalized transcript parity plus explicit limitations);
- no accuracy regression under the agreed criterion.

### Regression contract
Cover:
- single-source compatibility;
- one-element list;
- multi-file list order/attribution;
- batch_size validation / unsupported settings;
- per-item failure semantics;
- progress attribution;
- streaming behavior or explicit unsupported boundary;
- no accidental kwargs swallowing;
- deterministic benchmark invocation.

## Stage 4 — publication

- feature branch on a durable fork;
- PR references `Closes #13`;
- include exact benchmark command, environment, raw results, and acceptance mapping;
- pass repository checks;
- answer maintainer questions from source understanding;
- preserve payout/merge receipt after acceptance.

Do not infer earned revenue from implementation or CI. Payment eligibility begins only after the sponsor's lock/review/merge path is satisfied.
