# Contributing

Bug reports and pull requests are welcome. Report security issues privately as described in [SECURITY.md](SECURITY.md), not in an issue.

## Pull requests

1. Set up the environment and run the checks as in the README's [Development](README.md#development) section. The `validate` check runs the same commands and must pass before merging.
2. Add or update tests for behaviour you change. The default suite needs no Binary Ninja license; if your change touches export, rendering or the provider's session handling, also run the real Binary Ninja and end-to-end suites described there.
3. Keep a pull request to one change, with a [Conventional Commits](https://www.conventionalcommits.org/) title (`fix:`, `feat:`, `perf:`, `refactor:`, `docs:`, `build:`). Pull requests are squash-merged, so the title becomes the commit message.

The plugin is a client of an MCRIT server. Problems in matching results themselves usually belong to [MCRIT](https://github.com/familiary/mcrit/issues), and problems in disassembly to [SMDA](https://github.com/danielplohmann/smda/issues).
