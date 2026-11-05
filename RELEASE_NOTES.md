# DateWithMe v1.4.9

First public source release by Orictux.

A playful invitation site inspired by a runaway No button, now a customizable self-hosted application.

## Highlights

- Runaway No button, enabled by default and configurable in Settings.
- One-time setup wizard and personalized owner branding.
- Private invitation links with a 16-language interface.
- Activity decks, reusable cards, invitations, date planning and previews.
- Surprise messages, photos, calendar integration and notifications.
- Administrator dashboard, optional support page and encrypted data.
- Docker Compose, native Linux and compatible Python application hosting.

## Installation

```bash
cp .env.example .env
docker compose up -d --build
```

Open `http://localhost:8031/setup` and retrieve the setup token with:

```bash
docker compose exec datewithme cat /secrets/setup.token
```

A domain is not required for local use. See [README.md](README.md) and [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for HTTPS and production security settings.

## Release considerations

Back up both persistent volumes. This release has automated tests but has not received an independent security audit. Third-party licenses remain subject to their respective terms.
