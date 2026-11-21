# M12a school coverage gaps — formal evaluation (m12a-protocol-v1)

- Cases passed: **13/13** (threshold 13/13); deterministic receipts identical across two runs: True.
- Freeze manifest 601968b7b759f58da581b898a08774375e1519b30d64cba8e3279bab086906c8; receipt bf46afbabf8fa0cb2ef9dd0d7d9c13c95d6e9d57056c4af3d71a2300261fda4a; source fingerprint sha256:6a2d2025963dc8cba83eea70811f5ef1cd4e450ef2b1d1ec8825d4ac31efedd7.
- Front end: restricted command grammar (m12a-grammar-v1), not open-ended natural-language input. No LLM calls.
- Public dev cases written by the project author; references from an independent oracle by the same author (independent implementation, not independent labelling); not a hidden test set.

| Case | Category | Passed | Failed components |
|---|---|---|---|
| m12a_A1 | success | True | - |
| m12a_A2 | success | True | - |
| m12a_A3 | legit_empty | True | - |
| m12a_B1 | clarification | True | - |
| m12a_B2 | clarification | True | - |
| m12a_B3 | clarification | True | - |
| m12a_B4 | clarification | True | - |
| m12a_B5 | unsupported | True | - |
| m12a_C1 | crs_reject | True | - |
| m12a_C2 | crs_alternate_snapshot | True | - |
| m12a_C3 | crs_reconfirmed | True | - |
| m12a_C4 | crs_evidence_invalid | True | - |
| m12a_C5 | empty_unexpected | True | - |

Population sums cover the SCB cells returned for the study rectangle (including edge cells); they are not the exact population of the 20x20 km area. Distances above 2000 m are upper bounds.
