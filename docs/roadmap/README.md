# Roadmap and evaluation records

This folder holds the project's control documents for Stage 2 (M00–M12a). Design decisions are in [`../adr/`](../adr/).

| Document | Contents |
|---|---|
| [`EVALUATION_PROTOCOL.md`](EVALUATION_PROTOCOL.md) | Test layers, data splits, leakage rules and the recorded results of every evaluation |
| [`DATA_CATALOG.md`](DATA_CATALOG.md) | Every data asset with source, licence, version, size and hashes |
| [`TOOL_CATALOG.md`](TOOL_CATALOG.md) | Registered tools, their registries and boundaries |
| [`INTERFACES.md`](INTERFACES.md) | Stable public contracts, error codes and what is not implemented |
| [`DECISIONS.md`](DECISIONS.md) | ADR index and how each decision was implemented |
| `*_EVALUATION_FREEZE.md`, `M11C_V2_STAGE2_FREEZE.md` | Evaluation protocols frozen before the corresponding implementation |
| [`M11C_PROTOCOL_DRAFT.md`](M11C_PROTOCOL_DRAFT.md) | Superseded draft of the M11c protocol, kept as history |
| [`reviews/`](reviews/) | M03 Phase 2 tag-classification draft referenced by hash from the knowledge build |

## Frozen originals and English translations

Several freeze documents were written in Chinese and frozen before implementation. Their SHA-256 values are recorded in freeze manifests and frozen reports, and some are read and verified by code, so the originals are kept byte for byte as evaluation evidence and are not edited. Each of them has an English translation next to it:

| Frozen original (Chinese, authoritative) | English translation |
|---|---|
| `M09_EVALUATION_FREEZE.md` | [`M09_EVALUATION_FREEZE.en.md`](M09_EVALUATION_FREEZE.en.md) |
| `M11B_EVALUATION_FREEZE.md` | [`M11B_EVALUATION_FREEZE.en.md`](M11B_EVALUATION_FREEZE.en.md) |
| `M11C_EVALUATION_FREEZE.md` | [`M11C_EVALUATION_FREEZE.en.md`](M11C_EVALUATION_FREEZE.en.md) |
| `M11C_V2_EVALUATION_FREEZE.md` | [`M11C_V2_EVALUATION_FREEZE.en.md`](M11C_V2_EVALUATION_FREEZE.en.md) |
| `M11C_V2_STAGE2_FREEZE.md` | [`M11C_V2_STAGE2_FREEZE.en.md`](M11C_V2_STAGE2_FREEZE.en.md) |
| `M12A_EVALUATION_FREEZE.md` | [`M12A_EVALUATION_FREEZE.en.md`](M12A_EVALUATION_FREEZE.en.md) |

The M07, M08 and M10 freeze documents were written in English. If a translation and its original differ, the original applies.

Chinese text elsewhere in the repository is data, not documentation: the OSM knowledge base and its retrieval queries are deliberately bilingual (English and Chinese), and some tests and query builders contain Chinese inputs for that reason.
