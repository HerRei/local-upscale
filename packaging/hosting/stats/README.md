# LocalSR usage counts

The usage counter collects daily website visits, page views and referring sites, downloads by file, and app update checks by version, platform and channel. Website counting respects Do Not Track and Global Privacy Control; app usage counting can be turned off in Software Update. It also adds the growth of GitHub release asset download totals.

The database keeps daily counts and their keys indefinitely, along with the latest GitHub asset totals and timestamps for the last event and poll. To recognize repeat requests, it stores hashes of the IP address and user agent using a random daily salt. The hashes and salts are removed when they are more than two days old, during the next recorded event. It never stores IP addresses on disk. Backups omit hashes and salts. After 500 distinct visitor-chosen keys of a kind per day, further keys are grouped as `(other)`.

Run `python localsr_stats.py --help` for the collector, backup and GitHub polling commands. Run the tests from the repository root with `python -m pytest tests/test_localsr_stats.py tests/test_stats_server_tools.py`.
