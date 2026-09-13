# v2 runtime qualification

Externally signed #293 serial-submission and #296 Incident-containment controlled-runtime traces are
generated together from one strict image on the pinned external 200 GiB Colima runtime.
The v1 capsules remain immutable at their historical fixed point. The v2 manifest links only these
delivered slices: final-window lifecycle and later fixed domain remedies remain planned.

Regenerate both from the canonical external checkout and state root:

```sh
python3 scripts/qualify_runtime_evidence.py \
  --evaluator-private-key ../incypher-practice-rig/.rig-private/runtime-evidence/private.pem
```

Verify from this checkout:

```sh
python3 scripts/verify_runtime_evidence.py \
  docs/evidence/runtime-qualification-v2/293-serial-submission \
  docs/evidence/runtime-qualification-v2/296-incident-containment
```
