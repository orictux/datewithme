# Contributing to DateWithMe

Thank you for helping improve DateWithMe.

## Development setup

1. Use Python 3.12 or newer.
2. Create an isolated Python environment.
3. Install dependencies from `requirements.txt` and test dependencies `pytest` and `httpx`.
4. Start the app with `uvicorn app.main:app --host 127.0.0.1 --port 8080 --workers 1 --no-access-log`.
5. Configure the installation token at `/secrets/setup.token` or `./secrets/setup.token` based on your environment.
6. Run `python -m pytest -q` before proposing a change.

Do not commit real invitations, photos, encryption keys, setup tokens, personal configuration, database files or generated test data. New UI text belongs in a localization catalog. English is the default project language. Source code and comments should use plain, professional English.

Open an issue with reproduction steps before proposing a large design change. Prefer small pull requests with tests. Maintain existing user workflows and preserve compatibility with the public beta database format where possible.

## Pull requests

Explain the behavior change, include tests, note any migrations and describe privacy/security impacts. Screenshots should use fictional data only.

## License

Contributions are submitted under the repository's MIT license unless separately agreed in writing.
