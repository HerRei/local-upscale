**Prepared beta issue tracker**

The user confirmed public GitHub Issues for bug reports and
`hermes.reisner@gmail.com` for contact/private requests on 12 September 2026.
The [repository directory](repository/README.md) now contains a README, a
[bug report form](repository/.github/ISSUE_TEMPLATE/bug-report.yml) and an
[issue chooser configuration](repository/.github/ISSUE_TEMPLATE/config.yml)
with the confirmed email contact. These files are ready to copy into the chosen
public feedback repository. The current local-only instruction remains in effect;
no remote repository or issue configuration was changed.

The form asks for version, platform and reproduction steps, with optional
diagnostics, media details and cancellation/recovery observations. It does not
depend on labels existing in the destination repository. It follows
[GitHub's issue-form schema](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-githubs-form-schema).
Local YAML parsing and field checks passed, including six unique input IDs,
labels and string dropdown options. Actual rendering and submission in the
destination repository remain to verify after that repository is approved.

Suggested destination name: `localsr-feedback` under `HerRei`, subject to name
availability when publication is authorized. Copy the contents of `repository`,
including its hidden `.github` directory, into that repository. Keep Issues
enabled and add the actual beta/download and known-issues information after those
destinations are finalized. Contact and tracker type are already confirmed. Adapt
the existing [feature request form](../../.github/ISSUE_TEMPLATE/feature_request.yml)
if feature suggestions should use the same tracker.

Do not copy the source repository's security contact link: its private URL does
not provide access for general testers. The prepared chooser uses the confirmed
email address for private reports. Test reading the tracker while signed out and issue
creation as an ordinary tester before linking it from the app, Store or website.

Repository creation, publication and issue intake activation remain pending.
No test email was sent and no claim is made that the mailbox was verified.
See the [privacy/support draft](../../docs/beta-privacy-and-support.md) and
[beta decision log](../../docs/beta-release-decisions.md).
