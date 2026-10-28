# Security policy

## Reporting a vulnerability

Report vulnerabilities privately through [GitHub's private vulnerability reporting](https://github.com/r0ny123/mcrit-binja-similarity/security/advisories/new), not in a public issue. Include the plugin version or commit, the Binary Ninja build, and steps or a sample that reproduces the problem.

Only the latest release and `main` receive fixes.

## Scope

In scope: this plugin's handling of the API token, its connection to the MCRIT server (TLS, redirects, which data is uploaded), and parsing of server responses and cached SMDA reports.

Out of scope: vulnerabilities in the MCRIT server or mcritweb ([familiary/mcrit](https://github.com/familiary/mcrit)), SMDA ([danielplohmann/smda](https://github.com/danielplohmann/smda)), or Binary Ninja itself; report those to their maintainers.
