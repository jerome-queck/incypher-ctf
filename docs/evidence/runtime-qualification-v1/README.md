# Retained runtime qualification v1

Corrective evidence for #366, following PR #365's audit of #269, #270, #281, #283 and #298.
These are observed receipts, not reconstructions from ticket or PR claims.

Fixed point: strict image `sha256:c7c53ecd627bfcfe9fcb083f4e7d3c909f30fe9a091c881bb0e4b5898387c765`,
config `sha256:697ba267bb0ddb3ef175a64df75fe80fcf5a46bdd8b3fdce3da3d7800cce0919`,
`linux/arm64`, strict profile `fa12edd38923856160f7a8651e53c9c87dd1c4ddb894cd33846f9b7c071cdd43`,
catalogue `60f13b3b2e93be0366dd5be52f1642210892cb3b23bab8363bc0d7c707ec613a`.

| Ticket requirement | Retained proof |
|---|---|
| #269 pinned strict preflight | `269-strict-isolation-preflight/strict-isolation-preflight.receipt.json` |
| #269 deny/refusal probes | `269-strict-isolation-preflight/refusals.json` plus the Attempt receipt deny probes |
| #269 teardown and residue | `269-strict-isolation-preflight/residue-inventory.json` plus receipt cleanup fields |
| #270 real built-image Attempt and six breaches | `270-attempt-resource-envelope/attempt-resource-envelope.receipt.json` |
| #270 bounded process cleanup | `270-attempt-resource-envelope/attempt-process-lifecycle.receipt.json` |
| #270 crash between launch and result | `270-attempt-resource-envelope/crash-reconciliation.receipt.json` |
| #281 external green Solve/Isolation | `281-evaluator-framework/green.evaluator.json` |
| #281 deliberate Isolation failure | `281-evaluator-framework/isolation-failure.evaluator.json` |
| #283 IPC/catalogue/measured Turn/limit provenance | `283-native-codex-control/native-codex-control.receipt.json` |
| #283 empty executor credential surfaces | native receipt plus the exact-image Attempt deny receipt |
| #298 seven components / sixteen handles | seven `298-tool-resident/resident.*.json` receipts |
| #293 serial Candidate authority and crash boundaries | `293-serial-submission/serial-submission.receipt.json` |
| All candidate-manifest links | externally signed `candidate-manifest.json` |

Verify from a clean checkout:

```sh
python3 scripts/verify_runtime_evidence.py \
  docs/evidence/runtime-qualification-v1/269-* \
  docs/evidence/runtime-qualification-v1/270-* \
  docs/evidence/runtime-qualification-v1/281-* \
  docs/evidence/runtime-qualification-v1/283-* \
  docs/evidence/runtime-qualification-v1/298-*
```

The verifier checks external signatures, exact repository inputs, fixed image/profile/catalogue,
semantic outcomes, cleanup and candidate-manifest links. Any altered input, fixture, component,
output, receipt or signature refuses verification.

The #293 controlled runtime trace is self-verifying and deliberately classified separately from
the externally observed Gate capsules:

```sh
python3 -c "from pathlib import Path; from solver.submission.receipt import verify_receipt; verify_receipt(Path('docs/evidence/runtime-qualification-v1/293-serial-submission/serial-submission.receipt.json'))"
```
