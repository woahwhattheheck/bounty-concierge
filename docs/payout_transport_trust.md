# Payout transport trust

`concierge status` treats RustChain wallet history as settlement-adjacent evidence, so remote node identity is part of the trust boundary.

## Remote nodes

Remote payout/status reads require an `https://` RustChain node URL. TLS peer verification is enabled by default and is never disabled by the payout tracker.

For a node whose certificate chains to a private or self-signed CA, point `RUSTCHAIN_CA_BUNDLE` at a readable PEM CA bundle:

```bash
export RUSTCHAIN_NODE_URL='https://rustchain-node.example'
export RUSTCHAIN_CA_BUNDLE='/path/to/rustchain-ca.pem'
python -m concierge status --wallet YOUR_WALLET
```

If the configured CA bundle is unavailable or malformed, the status read fails closed before it can report payout history.

## Local development

Plain HTTP is accepted only for explicit loopback hosts such as `localhost`, `127.0.0.1`, or `::1`. Remote `http://` URLs, credential-bearing URLs, and URLs with query/fragment ambiguity are rejected before network I/O.

This setting changes read transport only. It does not authorize transfers, payouts, wallet mutation, or any other node write.
