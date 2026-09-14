# Collection Request Packets

`concierge.collection_request` turns bounded evidence for completed paid work into a deterministic, send-ready sponsor collection **draft**. It exists to make compensation intent explicit without overstating what the evidence proves.

## Authority model

A merged PR is useful completion evidence, but merge alone does not prove sponsor acceptance, an award, a payable obligation, earned revenue, or payment due. Therefore:

- `acceptance.kind = NONE` produces `READY_TO_REQUEST_ASSESSMENT`. The draft directly asks the sponsor to assess the merged contribution under the advertised terms and, if accepted, arrange the payout.
- `SPONSOR_ACCEPTED` or `AWARDED` requires a separate HTTPS evidence reference plus a SHA-256 commitment and produces `READY_TO_REQUEST_PAYMENT`.
- The packet always records `external_send=false`, `provider_mutation=false`, `payment_mutation=false`, `advertised_reward_is_debt=false`, and `advertised_reward_is_earned_revenue=false`.

The module does **not** send email, post GitHub comments, mutate wallets/providers, invoice anyone, or recognize revenue. A human or separately authorized sender owns transmission.

## Regular-file custody

Both the input document and the optional `--verify` packet are captured through the same fail-closed file boundary before JSON parsing.

The reader:

- inspects the caller-visible path with `lstat`;
- rejects symlinks, FIFOs, directories, sockets, devices, and every other non-regular input before semantic parsing;
- opens once with nonblocking, close-on-exec, binary, and no-follow flags where the host exposes them;
- requires a stable nonzero inode identity;
- binds device, inode, mode, link count, size, modification time, and change time across the pathname and retained descriptor;
- refuses an initial size above 1 MiB;
- reads at most 1 MiB plus one byte, so a lying or growing source cannot cause an unbounded allocation;
- rejects premature EOF, growth, truncation, metadata drift, path replacement, and same-inode generation mutation during capture;
- takes a final descriptor snapshot after the final pathname snapshot.

This is **finite capture evidence**, not a claim that the namespace remains immutable forever after `load_json()` returns. The contract is that the accepted bytes came from one bounded regular-file generation and that the caller-visible path named that same generation at the final custody snapshot.

Unsafe file errors do not echo file contents. JSON decoding still rejects invalid UTF-8, duplicate object keys, non-finite constants, malformed syntax, and oversized canonical output.

## Example: merged bug-bounty contribution

```json
{
  "schema": "bounty-collection-request-input/v1",
  "sponsor_name": "Acme Security",
  "work": {
    "repo": "acme/widget",
    "pr": 42,
    "canonical_url": "https://github.com/acme/widget/pull/42",
    "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "state": "MERGED",
    "advertised_amount": "90",
    "currency": "USD"
  },
  "payout_route": {
    "type": "PAYMENT_LINK",
    "value": "https://pay.example/abc"
  },
  "acceptance": {
    "kind": "NONE",
    "evidence_ref": null,
    "evidence_sha256": null
  }
}
```

Compile:

```bash
python -m concierge.collection_request request.json > packet.json
```

Verify exact derivation later:

```bash
python -m concierge.collection_request request.json --verify packet.json
```

## Collection-writing rule

The generated copy asks for the advertised compensation directly. It intentionally avoids soft eligibility language such as “am I eligible?” while still preserving evidence truth: merge-only work asks for sponsor **assessment**; accepted/awarded work asks for **payment**. This keeps the commercial intent strong and the evidentiary claim bounded.
