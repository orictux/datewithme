# Publishing DateWithMe on GitHub

This guide is for the maintainer of the Orictux DateWithMe repository.

## Before you publish

1. Ensure all source files are inside the repository root, not inside an additional nested `DateWithMe` directory.
2. Keep `.env`, local data, setup tokens, private keys, screenshots containing real invitations, and Docker volumes outside the repository.
3. Read `SECURITY.md`, `THIRD_PARTY_LICENSES.md`, and `docs/RELEASE_CHECKLIST.md`. Do not claim a completed security audit or complete license inventory unless those reviews have actually been performed.
4. Test a clean setup and the upgrade path with a copy of test data.
5. Confirm your Git identity uses an address you intend to disclose or a GitHub `noreply` address.

## Create the repository

1. In the Orictux GitHub account, create a repository named `datewithme`.
2. Keep the repository private while checking uploaded files. Do not initialize it with another README or LICENSE if you already have the project files.
3. Using GitHub Desktop, clone the empty repository to your computer.
4. Copy the *contents* of the extracted `DateWithMe` directory into the cloned repository. This should place `README.md` and `LICENSE` in the repository root.
5. Review the file changes. Confirm `.env` and secrets are not listed.
6. Commit with the message `Release DateWithMe v1.4.9` and push to the repository.
7. Enable protected main-branch rules, private vulnerability reporting, and appropriate Dependabot settings.
8. Wait for GitHub Actions CI to finish successfully.

## Publish the release

1. After reviewing the repository, change its visibility to public if that is your intended distribution.
2. Open `Releases` and choose `Draft a new release`.
3. Under `Choose a tag`, create `v1.4.9` targeting the exact commit tested.
4. Use the title `DateWithMe v1.4.9 | First Public Release`.
5. Copy `RELEASE_NOTES.md` into the release description. Edit any claims that have not been tested.
6. Attach `DateWithMe-v1.4.9-Source.zip` and its `.sha256` file.
7. Select `Set as the latest release`. Do not mark it as a prerelease if the project is actually being released as stable.
8. Preview the release page, then publish.

GitHub automatically offers source archives for the tagged commit. The separately attached source ZIP is useful as a versioned, independently checksummed download.

## After publishing

- Check all README links and assets on GitHub.
- Confirm that the MIT license is detected.
- Download the release ZIP from GitHub and verify its SHA-256.
- Make a clean local test installation directly from that ZIP.
- Do not modify the contents of an already published release. Use a new patch version for fixes.

## Version policy

DateWithMe uses Semantic Versioning. A breaking change increments the major number; backward-compatible features increment the minor number; backward-compatible fixes increment the patch number.

This is the first public GitHub release. Do not invent earlier public tags.
