# Security

What Altius does to protect your data, what it expects of you, and how to report a problem.

## What leaves your computer

The self-hosted edition sends nothing anywhere except to the price source you chose
(Dukascopy, Yahoo Finance or OANDA) and the alert channels you turned on (ntfy, Telegram,
Discord, Slack, your SMTP server, a webhook). There is no telemetry, no update check and no
account. The browser only talks to your own Altius server: the page's Content Security Policy
allows no other origin, and the fonts are bundled.

In the hosted edition your strategies, settings, forward tests and journal are kept on the
hosted server for your account, and that server makes the same outbound connections on your
behalf.

## Password and sessions

- `tradealerts serve` listens on `127.0.0.1` by default, where no password is needed.
- To listen on any other address it needs `TRADEALERTS_PASSWORD`, and refuses to start without
  one unless you pass `--no-auth`, which is only for when a reverse proxy already handles
  sign-in. The Docker setup sets a password for you.
- Behind a reverse proxy, always set a password. A copy without one refuses requests that
  arrive through a proxy (they carry `X-Forwarded-For`, `X-Real-IP` or `Forwarded`), unless
  `--no-auth` says the proxy signs people in. See [Running on a server](self-hosting.md).
- A session is a signed cookie (HttpOnly, SameSite=Strict). Changing the password signs
  everyone out. Repeated wrong passwords are slowed down, and the count is kept in the
  database so a restart does not reset it. Each attempt is counted before the password is
  checked, so sending many guesses at once gets no more through than sending them one by one.
- Requests from a browser must carry `X-Requested-With: tradealerts` for anything other than
  reading, which stops another site from using your session; the server also checks the `Host`
  header against the addresses it was started for.
- Responses carry `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, a strict Content Security Policy (`default-src 'self'`, no
  inline scripts or styles), and `Cache-Control: no-store` on the API.

Put a reverse proxy with HTTPS in front of a server install; the container publishes its port
on `127.0.0.1` only. [Running on a server](self-hosting.md) has the Caddy and nginx settings.

## Secrets

Alert tokens, passwords, webhook addresses and ntfy topics are secrets: anyone who has them
can send to your channels or read your ntfy topic.

- The desktop app, and a server install with the `keychain` extra, keep them in the
  computer's password store (Windows Credential Manager, macOS Keychain, Linux Secret Service)
  and leave only a `keychain:<name>` reference in `config.yaml`. `tradealerts serve
  --no-keychain` keeps them in the file instead; so does a store that cannot be used, or a
  "store" that is really a file (the `keyrings.alt` package).
- `config.yaml`, the sign-in secret and the safety copies made before a restore are written
  readable only by the account that runs Altius.
- `config.yaml` can read any value from an environment variable (`${NAME}`), which keeps
  secrets out of the file on a server.
- Settings never sends a saved password or token back to the browser.
- The hosted edition never reads or writes a password store, and expands no environment
  variables in a customer's settings.

## Backups

A full backup contains your secrets, so it restores on another computer: treat the zip like
the secrets themselves. A **shareable backup** (`/api/backup?redact=true`, or the option in
Settings) blanks passwords, tokens, webhook addresses and ntfy topics. A restore is checked in
full before anything is replaced, a safety copy is made first, and a restore that fails
part-way is rolled back.

## Public ntfy topics

On public ntfy.sh anyone who guesses your topic name can read your alerts. Use a long random
topic, or self-host ntfy with an access token.

## Hosted edition

- Each customer has their own SQLite database and their own folders; backtests run in
  worker processes, one heavy job per customer at a time.
- Customer settings load untrusted: no environment-variable expansion and no folder
  overrides.
- Alert webhooks and SMTP servers may only point at public internet addresses, so a customer
  cannot make the server call its own network. The address is looked up once and the
  connection goes to the address that was checked; IPv6 forms that carry a private IPv4
  address inside them, and internal names such as cloud metadata services, are refused too.
  Redirects are not followed (a redirected alert would have lost its message anyway).
  The self-hosted edition may send alerts to your own network on purpose.
- A lapsed subscription makes the account read-only: everything can be viewed and
  downloaded; forward tests, alerts and new backtests pause.

## Supply chain

The public repository runs the tests on Linux, Windows and macOS, a Docker smoke test,
ESLint over the interface, CodeQL, dependency review on pull requests, and weekly Dependabot
updates, and attaches a CycloneDX software bill of materials to each tagged release.
Releases are not yet code-signed.

## Reporting a problem

Please report security problems privately through GitHub's *Report a vulnerability* button on
the repository's Security tab, not in a public issue. See [SECURITY.md](../SECURITY.md).
