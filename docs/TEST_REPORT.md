# DateWithMe v1.4.9 test report

Date: 2026-11-05

The automated regression suite was executed in groups to accommodate the local test environment limits.

| Test group | Tests | Result |
| --- | ---: | --- |
| Core application, activity decks, media, branding, experience, mobile UI | 51 | Passed |
| Notifications | 13 | Passed |
| People, setup, sessions, release metadata, surprises, recovery, support menu | 27 | Passed |
| **Total** | **91** | **91 passed, across three runs** |

JavaScript syntax was verified with `node --check` for client.js, setup.js, recovery.js, and service-worker.js. Backend Python files were compiled successfully.

The release candidate was functionally tested by the project maintainer. No public production deployment, independent security penetration test, full transitive license audit, or multi-platform compatibility certification has been completed by this test environment.

The initial aggregate test command timed out without a test assertion failure. Every collected test subsequently passed when run in the three smaller groups. The original aggregate timeout is a test-environment limitation, not proof that a single-run GitHub CI job will pass.

Review docs/RELEASE_CHECKLIST.md before operating an Internet-facing installation.
