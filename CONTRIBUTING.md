# Contributing

Bug reports and pull requests are welcome. Report security issues privately as described in [SECURITY.md](SECURITY.md), not in an issue.

## Pull requests

1. Set up the environment and run the checks as in the README's [Development](README.md#development) section. The `Lint, types and unit tests` check runs the same commands and must pass before merging.
2. Add or update tests for behaviour you change. The default suite needs no Binary Ninja license; if your change touches export, rendering or the provider's session handling, also run the real Binary Ninja and end-to-end suites described there.
3. Describe a change users will notice under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md), in the section [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) names for it (Added, Changed, Deprecated, Removed, Fixed, Security).
4. Keep a pull request to one change, with a [Conventional Commits](https://www.conventionalcommits.org/) title (`fix:`, `feat:`, `perf:`, `refactor:`, `docs:`, `build:`). Pull requests are squash-merged, so the title becomes the commit message.

## Releasing

Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.y.z, anything may change between minor versions.

1. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, add a new empty `## [Unreleased]` above it, and update the comparison links at the bottom.
2. Set `X.Y.Z` as the version in `pyproject.toml`, `plugin.json` and `mcrit_similarity/__init__.py`, and merge to `main`.
3. Tag the merged commit `vX.Y.Z` and push the tag. The release workflow checks the tag against those three versions and the changelog, runs the CI checks, and publishes the GitHub release with that changelog section as its notes. Releases are immutable, so a mistake needs a new patch version.

The plugin is a client of an MCRIT server. Problems in matching results themselves usually belong to [MCRIT](https://github.com/familiary/mcrit/issues), and problems in disassembly to [SMDA](https://github.com/danielplohmann/smda/issues).
