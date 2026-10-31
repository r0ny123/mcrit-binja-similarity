# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-10-31

### Added

- Support for 32-bit ARM binaries (ARM and Thumb-2, little-endian), with `smda` 5.0.2.
- **Export Built-in Corpus...** and **Import Corpus into Built-in Server...** save the built-in server's samples to a file and load one, including exports from any MCRIT server. Sessions then find binaries already indexed and skip uploading them again.
- The **Built-in corpus** setting names an export file to load whenever the built-in server starts.

### Changed

- Release archives hold only the plugin, its README, changelog, license and the headless example; tests, CI and screenshots stay in the repository.

## [0.2.0] - 2026-10-29

### Changed

- The built-in MCRIT server is now the default, so the plugin works with nothing to set up after installing. Set the server URL under Settings > MCRIT Binary Similarity to use an MCRIT server or mcritweb instead.
- `mcrit` 1.16.0 is now a required dependency, installed automatically with `smda` and `requests`. It is a large install.

### Fixed

- With MCRIT 1.16 servers, a session could match a sample that MCRIT was still indexing, and never reported a sample that failed to index: the plugin looked up indexing jobs with a filter that 1.16 no longer matches. It now asks for them by sample id.
- The extension manager description wrote `>` as the HTML entity `&gt;`.

## [0.1.0] - 2026-10-29

### Added

- MCRIT as a provider in Binary Ninja's Binary Similarity sessions (Ultimate 6.0 or newer), next to BinDiff and WARP. Each edge `A -> B` exports both binaries to SMDA reports from Binary Ninja's analysis and matches them with MCRIT's MatcherVs.
- Built-in MCRIT server: with the server URL set to `builtin`, MCRIT runs inside Binary Ninja, with no Docker, MongoDB or account. It needs the optional `mcrit` package and gives the same results as a standalone MCRIT 1.16 server.
- Similarity scores mapped to Binary Ninja's 0–255 scale, with confidence lowered when another candidate scores close to the best match.
- Side-by-side diff rendering of matched functions in the Graph and Linear views, for disassembly, Low/Medium/High Level IL and Pseudo C, Objective-C and Rust. Blocks are aligned by PicBlock hash, and only differing instructions are marked.
- Apply, which copies names, types, variables and comments from the matched function, and can rename unnamed functions after their MCRIT corpus label.
- Query per node: one corpus query serves every incoming edge of a function set.
- Uploads of unknown samples, on by default for local and built-in servers and opt-in for remote ones, with optional family and version labels.
- `Plugins > MCRIT Similarity > Test Server Connection`, which checks the server URL and API token without starting a session.
- A warning when the server applies different matching parameters than requested, as mcritweb does.
- An on-disk cache of SMDA reports, loaded in the background once analysis finishes.
- Support for x86, x86_64 and aarch64 binaries.

### Security

- The API token is stored in the system keychain and never logged.
- A token is only sent over HTTPS, or HTTP to a loopback address; redirects and credentials in the server URL are refused.
- The built-in server listens only on 127.0.0.1 and accepts only requests carrying a token generated for each Binary Ninja run.

[Unreleased]: https://github.com/r0ny123/mcrit-binja-similarity/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/r0ny123/mcrit-binja-similarity/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/r0ny123/mcrit-binja-similarity/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/r0ny123/mcrit-binja-similarity/releases/tag/v0.1.0
