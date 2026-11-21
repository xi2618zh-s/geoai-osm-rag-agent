# Test benchmark split

This tracked directory contains only public schemas and evaluation instructions.
Future hidden test queries must not be copied into prompts, knowledge aliases, or
development fixtures. M11a freezes only the public schema boundary; hidden task
content, release controls, and adjudication remain M11b work. A test run must
record the same hashes, versions, seeds, model, prompt, code revision, and config
identity as a development run without exposing the query payload.

## M11b private holdout protocol

The tracked directory contains a payload-free m11b_private_receipt_v1.json
plus this protocol. The 40 hidden queries, reference geometries, source files,
and build seed are local in Git-ignored private/ and must be transferred securely
to a separate evaluator. Do not copy them to prompts, knowledge aliases,
development fixtures, source documentation, or public reports. Run the M11b
reproducibility audit in the original workspace or after restoring the private
package. A fresh public checkout may run offline tests with private checks
explicitly skipped; the hash receipt alone does not prove a private rebuild.

The private split is a held-out synthetic variant of the M04b contract geometry,
not an external expert-annotated geographic evaluation. Lock model, prompt and
configuration before running any formal test predictions in M11c.
