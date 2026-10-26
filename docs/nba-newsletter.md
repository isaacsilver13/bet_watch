# Owner-only NBA newsletter preparation

Date: 2026-10-22. `nba_reports` stores the exact daily report used by the
existing AIsaac push. The local `render_nba_digest` function derives a static
HTML email and plain-text fallback from that saved payload. It shows all
scheduled games, predicted home-win probability and margin, market probability
when available, and targeted moneyline value picks with selected and FanDuel
prices from the same saved quote capture. An empty `picks` list is
an explicit no-picks state; a missing list is rejected. The linked web report
is where interactive charts and sorting will live.

The owner chose their existing Resend account and AIsaac's current
`AIsaac <onboarding@resend.dev>` sender for the first private test, following AIsaac's `POST
/emails` pattern. `nba_digest_deliveries` is a local outbox: staging freezes
the full request, including report URL, sender, recipient and HTML, once for
each slate. Sending atomically claims a staged row before contacting Resend,
then records acceptance and the provider message ID. Failed or ambiguous
requests move to `review` and do not retry automatically. A crash after claim
leaves `sending`, also requiring review. Resend retains the per-slate
`Idempotency-Key` for 24 hours; an attempted row remains durable after that
window to prevent a later duplicate.
No real email has been sent from this repository. Local tests use
`httpx.MockTransport`.
The protected `GET /api/v1/nba/reports/{date}/digest-preview` renders the
saved report as HTML without staging or sending a message. The report page
links to this preview. When `OWNER_APP_ORIGIN` is configured, the preview
includes the corresponding private report link.

Configuration names (values belong in the secrets manager, not source):

- `RESEND_API_KEY`: an API key usable by the betting app.
- `NBA_NEWSLETTER_FROM_EMAIL`: initially AIsaac's current onboarding sender.
  Resend may limit it to the account owner; confirm acceptance in a reviewed
  private test before scheduling.
- `NBA_NEWSLETTER_TO_EMAIL`: the owner-provided private recipient.

Before activation: set the private web report address; add an owner-visible preview/send
control; reconcile AIsaac's current report push so one service owns the
newsletter; configure secrets; then test one reviewed message and schedule
only after that result is accepted. No job or route invokes the outbox send
function yet. The saved report route and password session are implemented
locally; the separate Fly app remains undeployed until authentication and
storage are verified in its target environment.
