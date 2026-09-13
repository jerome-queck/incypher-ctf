# v2 runtime qualification

Externally signed #293 serial-submission and #296 Incident-containment controlled-runtime traces for
strict image `sha256:9d43697b5b3ce102d616198620b4479492530ca17344c8adadaecf1bc49d21f1`.
The v1 capsules remain immutable at their historical fixed point. The v2 manifest links only these
delivered slices: final-window lifecycle and later fixed domain remedies remain planned.

Verify from this checkout:

```sh
python3 scripts/verify_runtime_evidence.py \
  docs/evidence/runtime-qualification-v2/293-serial-submission \
  docs/evidence/runtime-qualification-v2/296-incident-containment
```
