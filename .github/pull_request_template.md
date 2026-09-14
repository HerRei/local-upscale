## Summary

Describe the user-visible outcome and the reason for the change.

## Verification

List the checks run and their results. `./local-ci.sh` runs the full local suite;
individual phases and prerequisites are in `docs/development.md`.

For a bug fix, describe the reproduced case and the result after the change.
Include installed GUI or worker shutdown checks when those behaviors are affected.

## Risk

Call out changes involving model loading, memory limits, file replacement, metadata, or IPC.
