## Summary

Describe the user-visible outcome and the reason for the change.

## Verification

- [ ] `ruff check src tests smoke_test_gui.py`
- [ ] `ruff format --check src tests smoke_test_gui.py`
- [ ] `pytest -q`
- [ ] GUI behavior was checked when the change affects the interface
- [ ] Worker shutdown leaves no child process or memmap file behind

## Risk

Call out changes involving model loading, memory limits, file replacement, metadata, or IPC.
