# Owner access and saved NBA report

Date: 2026-10-22. The owner chose one password and a revocable browser session
for the eventual private Fly app. This is implemented locally; no Fly secret,
shared database migration or deployment has been changed.

`OWNER_PASSWORD_HASH` holds a salted PBKDF2-SHA256 verifier, never the password.
Generate it with `python -m app.api.owner_auth` from the installed backend;
the command prompts twice without echoing. Store the output in the secret
manager. `OWNER_APP_ORIGIN` must be the exact HTTPS origin of the hosted app,
without a trailing slash. Hosting also requires persistent database storage
and migration `0010_owner_sessions`. The login endpoint refuses production
login without HTTPS origin configuration.

After a successful login, the browser receives an HttpOnly, Secure (for HTTPS),
SameSite=Strict cookie. The database stores only a SHA-256 hash of that random
token. Sessions last 12 hours, logout deletes the row, and changing the
password verifier invalidates earlier sessions. Login attempts are throttled
per peer and globally in the single-process prototype. Browser writes require
the configured origin; paid GET odds refresh additionally needs a custom
same-origin action header. No third-party login or sportsbook account is used.

The authenticated `GET /api/v1/nba/reports/{date}` reads the immutable report
snapshot. `/nba/report/{date}` charts saved model and market probabilities,
shows model margin uncertainty with a global-sigma caveat, and lets the owner
filter and sort selected picks. The API and page do not refresh odds when
opened. They return no report for a date without a saved snapshot.

Before release: set the secrets and origin, rehearse the migration on isolated
storage, review the production proxy/cookie behavior, and test login, logout,
rotation, backup/restore and report access in a private staging environment.
The existing admin token remains available for internal automation but is not
placed in the browser.
