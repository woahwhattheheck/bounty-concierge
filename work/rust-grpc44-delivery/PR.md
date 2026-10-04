# chore: update dependencies and migrate gRPC telemetry APIs

/claim #44

Updates the dependency graph and lockfile to compatible current releases. Migrates Tonic/Prost generation and server transport, updates OpenTelemetry exporter/provider initialization, and synchronizes the generated CI workflow. Existing News RPC implementations and the protobuf schema are unchanged; reflection remains on v1alpha.

Validation: `cargo fmt --all -- --check`, `cargo test --locked --all-features`, and `cargo clippy --locked --all-targets --all-features -- -D warnings` passed on Rust 1.99.0. The existing workflow-generation test is retained; no new test suite was added. README prerequisites now state Rust 1.91 or newer and protoc; the minimum compiler version was not separately exercised.

The accompanying `rust-grpc44-demo.mp4` is a 40-second walkthrough of the checked code and recorded CI result. No production deployment or paid telemetry calls were made.
