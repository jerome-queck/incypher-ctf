# v2 runtime qualification

Externally signed #293 serial-submission, #296 Incident-containment and #299 Crypto Tool
controlled-runtime traces were captured together from one strict image on the historical external
200 GiB Colima runtime. The current host starts with a 700 GiB disk request; disk capacity is an
observation rather than part of the signed strict profile. CPU/RAM and per-worker Resource
envelopes remain unchanged, and the verifier checks that the observed host disk is positive and
matches its external data disk.
The v1 capsules remain immutable at their historical fixed point. The v2 manifest links only these
delivered slices: final-window lifecycle and later fixed domain remedies remain planned.

Regenerate all three from the canonical external checkout and state root:

```sh
python3 scripts/qualify_runtime_evidence.py \
  --evaluator-private-key ../incypher-practice-rig/.rig-private/runtime-evidence/private.pem
```

Verify from this checkout:

```sh
python3 scripts/verify_runtime_evidence.py \
  docs/evidence/runtime-qualification-v2/293-serial-submission \
  docs/evidence/runtime-qualification-v2/296-incident-containment \
  docs/evidence/runtime-qualification-v2/299-tool-crypto
```
