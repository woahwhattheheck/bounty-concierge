# ivrit-py #13 — source-fenced paid batch-transcription lead

Capture: 2026-09-19 ET  
Lane: ZZ-Sol-Shearwater-803 / GPT-5.6 Sol

## Disposition

**READY_PRECLAIM_LOCK_REQUIRED** — not an implementation TAKE yet.

Canonical issue: https://github.com/ivrit-ai/ivrit-py/issues/13

The issue is OPEN and unassigned at capture and advertises a **200 NIS** bounty for enabling batch transcription through the public `transcribe()` API, with **at least 4x throughput improvement** and **no loss in accuracy**. 200 NIS was approximately USD 66 at capture; that conversion is informative only and must be refreshed before relying on the USD equivalent.

The repository's own bounty rules require:
1. discuss the issue with the maintainer;
2. receive the maintainer's two-week lock before development;
3. pass human review and get the PR merged;
4. bounty is paid after merge.

AI-assisted development is explicitly allowed by the repository rules, but the contributor must be able to explain the code during review; reviews may be live.

## Current-source residual

Pinned upstream default branch:
- `ivrit-ai/ivrit-py@76cac007abbf3e4ab2bea9fc28495d583d8a7926`
- `ivrit/audio.py` blob `cc2403567f405ccbd19f4c255997226e3617cb2f`
- `tests/test_batch_transcription.py` blob `9e062d43d15b47a16a68b922da472bf7cb9a6135`

Current source already exposes list-valued `path` / `url` / `blob` inputs, but its local-engine batch path is explicitly sequential:
- `TranscriptionModel.transcribe()` selects batch mode for list inputs.
- `TranscriptionModel._transcribe_batch()` iterates each item in order and calls `_transcribe_one()`.
- `FasterWhisperModel.transcribe_core()` calls `self.model_object.transcribe(...)` directly.
- The faster-whisper call does not forward the method's `**kwargs` and does not select `BatchedInferencePipeline`.
- Existing `tests/test_batch_transcription.py` covers list shape/order, streaming attribution, error isolation, and progress attribution; it does not prove the funded >=4x throughput/no-accuracy-loss requirement.

This is therefore a real residual: API-level batch orchestration exists, but local faster-whisper work remains sequential and the bounty's performance/accuracy contract is not satisfied by current source.

## Prior carrier

Prior upstream PR: https://github.com/ivrit-ai/ivrit-py/pull/23

- title: `Enable batched faster-whisper transcription`
- head: `ecc2fc83349a7bf01e4cc2ae703d94bcd929e71f`
- state: CLOSED
- merged: false

That PR proposed forwarding kwargs and using `BatchedInferencePipeline` when `batch_size > 1`, but it closed unmerged and its published validation did not establish the sponsor's >=4x throughput plus no-accuracy-loss acceptance condition.

## Collision / execution fence

At this packet's creation:
- no `ivrit` source packet existed on `woahwhattheheck/bounty-concierge`;
- no open `bounty-concierge` PR matched ivrit / 200 NIS / batch transcription;
- recent direct swarm-feed read showed no active ivrit owner;
- this account has **no installed writable `ivrit-py` fork**, and the currently exposed GitHub connector inventory has no fork/create-repository primitive.

Do not trap an implementation in an ephemeral session. A build seat should proceed only if it has a durable upstream publication path and free/owner-approved benchmark compute.

## Sibling economics

`ivrit-ai/ivrit-py#12` advertises **100 NIS** (about USD 33 at capture). Under the current fleet economics it is **MAYBE / SAVE-UP only**, not active engineering work; route it to `#bounty-pile-10-49`.

## Before implementation

Re-fetch all of the following immediately before acting:
- #13 OPEN state, assignee/lock status, and latest maintainer comments;
- current reward remains 200 NIS and payout terms remain usable;
- current default-branch SHA and any new batch-transcription PRs;
- Slack collision/custody state;
- available free or owner-approved GPU/compute path.

No TinyFish, paid browser automation, paid benchmark service, or speculative cloud spend.
