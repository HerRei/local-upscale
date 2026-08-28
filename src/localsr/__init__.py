# Keep the human-facing alpha spelling stable. Python package metadata normalizes
# this to 0.0.7a0 under PEP 440; release preflight checks this constant against
# pyproject.toml so the desktop UI still matches the tag and bundle metadata.
__version__ = "0.0.7-alpha"
