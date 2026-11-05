# Security Policy

## Release status

DateWithMe v1.4.9 is the first public source release. It has not undergone an independent security audit. Before exposing an installation to the Internet, review the deployment settings, secure the reverse proxy, and test encrypted backup recovery.

## Report vulnerabilities

Please do **not** disclose an exploit, private key, or personal information in a public GitHub issue. Use the repository's private security advisory feature when enabled. Repository maintainers should enable private vulnerability reporting before making the repository public.

Include affected version, reproduction steps, expected behavior and impact. Do not test against servers that you do not own or have authorization to assess.

## Security properties and limits

- Admin sessions are stored server-side and require HttpOnly cookies. HTTPS must be enforced at the edge.
- First-run setup requires possession of a local one-time token.
- The application encrypts its SQLite data at rest, but it is not end-to-end encryption. The running server can read data.
- Personal data may appear in reverse proxy or infrastructure logs even when the application does not retain IP addresses in its database. Configure proxy logging appropriately.
- Treat both `/data` and `/secrets` as sensitive. Never commit or expose keys or decrypted databases.
- A short invitation code is not a secure secret. Use the default 16-character codes and assume anyone with a link can access that invitation.
- A single application process is supported. Running multiple workers or replicas against the same encrypted database is unsafe.
- Keep Linux, Docker, Python packages, reverse proxy and browser dependencies patched.

## Supported versions

The first public release is v1.4.9. Security fixes are provided through subsequent maintenance releases. This project does not support importing databases from unrelated personal installations.
