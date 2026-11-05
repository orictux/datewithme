# Third-party License Notes

The DateWithMe application code is released under the MIT license. Dependencies and platform-provided components are independently licensed.

Direct Python dependencies currently include:

| Project | License information |
| --- | --- |
| FastAPI | MIT |
| Uvicorn | BSD-3-Clause |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| argon2-cffi | MIT |
| Pillow | MIT-CMU |
| pillow-heif | BSD-3-Clause (check bundled codec components) |
| tzdata | Apache-2.0 |
| pywebpush | MPL-2.0, verify the published dependency build |

This file is an initial inventory, **not a complete Software Bill of Materials**. A public stable release must ship the relevant full license notices for distributed dependencies, including native codec libraries and Docker base images. Optional service providers have their own terms. Confirm the licenses in each exact distributed package before public release.
