# Security policy

## Supported versions

The latest release receives security fixes.

## Reporting a vulnerability

Please report suspected vulnerabilities privately via GitHub's
[private vulnerability reporting](https://github.com/quasarnova-team/hypernova/security/advisories/new)
("Security" tab → "Report a vulnerability"). Do not open a public issue.

We aim to acknowledge reports within **72 hours** and to publish a fix or a
mitigation plan within 30 days of confirmation.

## Scope notes

The wire-signing profile (HMAC-SHA256 in the Part 14 SecurityHeader) has documented,
deliberate limits — no replay window, no encryption yet — described honestly in
[doc/security.md](doc/security.md). Reports about those documented limits are
feature requests; reports that *break the stated guarantees* (forged frames passing
verification, key handling flaws) are vulnerabilities and get the process above.
