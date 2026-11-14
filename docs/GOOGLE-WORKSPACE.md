# Google Workspace connections

This expansion follows the account-bound connector design in
[jarvis-parallax](https://github.com/SupradeepDanturti/jarvis-parallax), especially
its `backend/agent/google.py` and `backend/agent/sheets.py`. It adapts that
structure to this application's async Python runtime, durable SQLite approvals
and dcrypt credential vault; it does not replace the harness backend with the
reference application.

## Supported access

- Gmail: bounded search/read and exact-approved sends, retaining the existing
  harness integration.
- Calendar: bounded event reads and exact-approved event creation.
- Drive: opt-in discovery of spreadsheet metadata, including names, IDs and edit
  capability. This is not general Drive file download, folder management or
  arbitrary document access.
- Sheets: account-bound spreadsheet/range registrations, tab metadata, bounded
  cell reads, and exact-approved RAW cell updates.

Provider authorization and application approval are separate. Consent grants
Google API scopes; it does not approve a particular email, event or cell update.
No Google account is connected by installing this code or by entering the
harness access token.

## Local setup

1. Enable Gmail, Calendar, Google Drive and Google Sheets APIs in your Google
   Cloud project as needed.
2. Configure a web OAuth client and its permitted testing/users/scopes. Register
   the exact callback `http://127.0.0.1:8787/oauth/google/callback`, adjusting the
   port to match `LOCAL_PORT`.
3. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `HARNESS_TOKEN` and a stable
   `CREDENTIAL_KEY` using the existing local configuration instructions. Keep
   secrets outside source control, frontend bundles and model context.
4. Start the local server and use Connections → Connect Google. Complete Google
   consent yourself, return to Connections and refresh.
5. Verify the displayed account and granted permissions before registering or
   discovering spreadsheets. Existing connections need a fresh consent flow to
   obtain the added identity, Sheets and Drive permissions.

The requested scopes include OpenID/email identity, the existing Gmail read/send
and Calendar permissions, Sheets access and Drive metadata read access. Stored
granted scopes determine capabilities; the app must not assume a requested scope
was actually granted. Missing permissions require reconnecting with the intended
scopes, not silently using another account.

## Account and approval binding

OAuth verifies the Google subject and verified email before saving a new grant.
Each connection has a fresh grant ID. Gmail sends, Calendar creation and Sheets
updates bind the reviewed payload to that identity and grant ID. Reconnecting,
disconnecting or changing accounts prevents old bound actions from executing
under a replacement grant. Token refresh within the same verified grant is not
a new authorization.

Legacy credential envelopes remain readable through the dcrypt migration layer.
An old grant without verified identity can retain compatible existing reads,
but the new identity-bound features and writes require reconnecting. Existing
credentials are not evidence that new scopes were granted.

The Google connector uses fixed endpoint patterns, bounded JSON responses and
sanitized errors. Credentials are never agent arguments. Redirects or arbitrary
caller-selected API origins are not a way to extend access.

## Spreadsheet registrations and discovery

Register a spreadsheet by ID with a clear name and either one bounded range or
spreadsheet-wide access. Up to 20 registrations are retained per Google account.
Registration names and IDs are account-scoped, so switching accounts cannot
silently reuse another account's registered handle.

A range registration permits only its saved range. Spreadsheet-wide access lets
you choose a bounded range for each operation; it does not authorize an
unbounded sheet dump. A typical range is `Sheet1!A1:D20`; quote a tab name with
spaces as `'Budget 2026'!A1:D20`.

Drive discovery is off unless explicitly enabled for the current verified
account. Searches return at most 20 spreadsheet metadata records per page.
Discovery handles expire after five minutes and are tied to the current grant.
Disabling discovery or replacing the grant invalidates those temporary handles.
Register a spreadsheet when you want durable account-scoped access to it.

Tab metadata is returned in pages of at most 50. Cell operations require a
tab-qualified rectangular A1 range with at most 100 rows, 20 columns and 1,000
cells. Smaller API/tool request-size limits can further constrain an update.
Named ranges, open-ended columns/rows and arbitrary Drive URLs do not bypass
these bounds.

## Reviewed cell updates

Read the selected range first, then enter a JSON array of rows for the proposed
update. Values are bounded text, finite numbers, booleans or nulls. The app
creates a pending action showing the exact sheet/range and values. Review it in
Approvals; proposing the update does not execute it.

Updates use Google Sheets `valueInputOption=RAW`. Text starting with `=` is
submitted as literal data rather than being interpreted as a formula. General
formatting, tab creation/deletion, formula evaluation and arbitrary batch-update
requests are not part of this connector.

Existing approval digest, expiry, execution-claim and replay checks still apply.
If a provider write has an ambiguous outcome, reconcile with Google before any
new attempt. A failed request must not be advertised as a confirmed cell change.

## API and agent capabilities

- `GET/POST /api/google/sheets`: list/register account-bound spreadsheets.
- `DELETE /api/google/sheets/:id`: remove a registration.
- `PATCH /api/google/sheets/discovery`: explicitly enable or disable discovery.
- `POST /api/google/sheets/search`: bounded Drive spreadsheet search.
- `POST /api/google/sheets/:id/tabs`: bounded tab metadata.
- `POST /api/google/sheets/:id/read`: bounded values within the saved grant.
- `POST /api/google/sheets/:id/propose`: create an exact-approved RAW update.
- Read-only agents use `sheets_read`; writes remain `propose_action` requests of
  kind `sheets.update`, followed by the normal owner approval flow.

All application API calls require the harness bearer token. Account grant IDs
are identifiers, not substitutes for API authentication. Workflow specialist
agents remain read-only and cannot approve or execute updates.

## Verification boundary

Automated tests use synthetic credentials, isolated SQLite and mocked Google
responses. They verify account/grant changes, scope checks, bounds, discovery,
approval/replay and RAW payload behavior. They do not establish live Google
consent, account-specific permissions, quota availability or actual cell writes.
Perform live acceptance separately with an explicitly chosen test spreadsheet
and exact owner approval for any mutation.
