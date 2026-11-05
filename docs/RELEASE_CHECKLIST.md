# Public release checklist

Use this checklist for ongoing release and deployment assurance. Mark a check only after performing the corresponding verification. The v1.4.9 source release is not an independent security certification:

- [ ] Complete local setup with a new administrator and unique setup token.
- [ ] Confirm wrong token, weak password, and invalid public URL are rejected.
- [ ] Confirm a completed setup cannot be repeated or bypassed.
- [ ] Confirm the default runaway No animation and the opt-out setting.
- [ ] Confirm language switching and complete visible English dashboard copy.
- [ ] Confirm no private domains, IP addresses, test artifacts or credentials remain.
- [ ] Confirm Docker, native Linux, cloud deployment instructions against actual environments.
- [ ] Confirm encrypted data survives restart and backup restoration with the correct key.
- [ ] Confirm Safari iOS, Chrome Android and desktop browser interaction flows.
- [ ] Confirm upload processing for JPEG, MPO, HEIC/HEIF, PNG, WebP and animated GIF.
- [ ] Confirm the full dependency license inventory, native codec notices and icon ownership.
- [ ] Confirm security review for CSRF, XSS, invitation access, origin checks, setup and session handling.
- [ ] Confirm accessibility testing and reduced-motion behavior.
- [ ] Confirm GitHub branch protection, private vulnerability reports and release tags.
- [ ] Publish screenshots and sample data with fictional people and no metadata.
