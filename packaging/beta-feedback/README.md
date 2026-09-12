**Prepared beta feedback form**

This directory stages a [bug report form](bug-report.yml) for a possible public
LocalSR feedback repository. It is not installed into the current private
repository's issue configuration and no repository has been created.

The form asks for version, platform and reproduction steps, with optional
diagnostics, media details and cancellation/recovery observations. It does not
depend on labels existing in the destination repository. It follows
[GitHub's issue-form schema](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-githubs-form-schema).
Local YAML parsing and field checks passed, including six unique input IDs,
labels and string dropdown options. Actual rendering and submission in the
destination repository remain to verify after that repository is approved.

After the user chooses GitHub and the repository name, prepare the destination
README with the current beta, supported packages, known issues and the chosen
private contact. Put the form at `.github/ISSUE_TEMPLATE/bug-report.yml`. Adapt
the existing [feature request form](../../.github/ISSUE_TEMPLATE/feature_request.yml)
if feature suggestions should use the same tracker.

Do not copy the source repository's security contact link: its private URL does
not provide access for general testers. Confirm a usable private reporting route
before adding contact links. Test reading the tracker while signed out and issue
creation as an ordinary tester before linking it from the app, Store or website.

Repository creation, publication and issue intake activation remain pending.
See the [privacy/support draft](../../docs/beta-privacy-and-support.md) and
[beta decision log](../../docs/beta-release-decisions.md).
