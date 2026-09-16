# MCRIT Similarity for Binary Ninja

Adds [MCRIT](https://github.com/danielplohmann/mcrit) as a provider in Binary Ninja's [Binary Similarity](https://docs.binary.ninja/guide/similarity.html) sessions, next to Google BinDiff and WARP. Matching, results, apply and side-by-side rendering all go through Binary Ninja's own similarity UI and API.

This is a session provider for comparing binaries, not the MCRIT sidebar for browsing a corpus.

## Requirements

- Binary Ninja Ultimate 6.0 (build 10601 or newer). Binary Similarity is Ultimate-only; on other editions the plugin loads and logs that.
- An MCRIT server, either its REST API (port 8000) or [mcritweb](https://github.com/fkie-cad/mcritweb) at `/api/`.
- Binaries on x86, x86_64 or aarch64, the architectures SMDA can export from Binary Ninja analysis.

## Install

1. Link the repository into the Binary Ninja plugin folder:

   ```bash
   # macOS
   ln -s "$PWD" ~/Library/Application\ Support/Binary\ Ninja/plugins/mcrit-binja-similarity
   # Linux
   ln -s "$PWD" ~/.binaryninja/plugins/mcrit-binja-similarity
   ```

   ```bat
   :: Windows
   mklink /J "%APPDATA%\Binary Ninja\plugins\mcrit-binja-similarity" "%CD%"
   ```

2. Install the packages listed in `requirements.txt` (`smda` and `requests`) into Binary Ninja's Python. The easiest way is the command palette (`Ctrl+P` / `Cmd+P`), command `Install python3 module`, once per package. From a terminal, install into the user folder Binary Ninja uses for modules:

   ```bash
   # macOS; on Linux use ~/.binaryninja, on Windows %APPDATA%\Binary Ninja
   python3.13 -m pip install --target ~/Library/Application\ Support/Binary\ Ninja/python313/site-packages -r requirements.txt
   ```

   If `python.interpreter` points at your own Python, install into that one instead.

3. Restart Binary Ninja. The log shows `Registered MCRIT Binary Similarity provider`.

4. In Settings (`Ctrl+,` / `Cmd+,`), search for `mcrit` and set the server URL: `http://127.0.0.1:8000/` for a local MCRIT server, or `https://<host>/api/` plus username and API token for mcritweb.

## Run a session

1. File > New Similarity Session.
2. Quick Setup: choose the reference binary A and the binary B to find matches in. This creates the edge `A -> B`.
3. Configuration > Add Provider > MCRIT. Google BinDiff and WARP can run in the same session, and Metric Similarity Resolver applies the best match automatically.
4. Start.

Results appear per function in B with MCRIT's score (0 to 100) mapped to the 0 to 255 similarity scale. Apply copies names, types and comments from the matched function in A, as with the built-in providers.

## Settings

Settings > MCRIT (user scope):

| Setting | Meaning |
|---|---|
| Server URL | Default `http://127.0.0.1:8000/` |
| Username | `username` header for mcritweb |
| API token | Moved into the system keychain on first use, after which the field reads empty. Enter a new token to replace it |
| Request timeout | Seconds per HTTP request |

To delete a stored token, in the Python console:

```python
SecretsProvider["SystemSecretsProvider"].delete_data("mcrit.api_token")
```

Provider settings live in the session's Configuration dialog: MinHash threshold, PicHash size, LSH bands, max candidates, include library matches, persist samples, family and version for uploads, and two options that are off by default:

- Apply corpus labels: when applying a match where neither function has a real name, rename the source function to the target's MCRIT function label.
- Query per node: with several incoming edges, query the whole corpus once and split the result per edge instead of running one MatcherVs per edge. This forces recalculation, so it is slower on large corpora, and it needs a direct MCRIT server.

### Behind mcritweb

- Uploading a sample is `POST /samples` and needs a contributor or admin token. A 403 fails the visit with a log message; the session keeps running.
- mcritweb drops the query parameters of `matches/sample`, so the MinHash threshold, PicHash size and LSH band settings are ignored and Query per node cannot force recalculation. Use a direct MCRIT server for those.

## How matching works

Each view is exported to an SMDA report from Binary Ninja's analysis and looked up on the server by SHA256. An unknown sample is uploaded if persist samples is on; otherwise the visit fails with a log message. The log reports the upload, the sample id and the indexing time, and matching starts once indexing has finished, including for samples someone else uploaded that are still being indexed. Family and version only apply to samples uploaded by this session.

Matching itself is MCRIT's MatcherVs (`GET /matches/sample/{B}/{A}`); only the matched functions are fetched afterwards (`POST /functions`).

Confidence reflects how unique a match is: a tie with another candidate halves it, a lead of 10 points or more keeps it, and a PicHash match gets 255 only when no other function shares that PicHash. A result is named after the target's MCRIT function label when the target has no symbol, or `name (label)` when both differ.

## Diff rendering

Rendering aligns the two functions by PicBlock hash, then pairs the remaining blocks by escaped-instruction similarity. Inside paired blocks only the differing instructions are marked changed, and instructions present on one side only are marked removed (A) or added (B). Blocks without a counterpart are coloured whole. Function entries always pair with each other, so a difference there is marked changed instead of painting the header as removed or added. Identical functions stay unpainted; a 100 score alone does not mean identical, so those are diffed too.

SMDA reports are cached under `<Binary Ninja user folder>/mcrit_similarity/smda_reports` (512 MiB or 1000 files, least recently used removed first). After a restart, sessions reuse them, and the report of a binary up to 64 MiB is loaded in the background once analysis finishes, so rendering works without re-running the session. Delete the folder to clear the cache.

## Headless

```python
from binaryninja import SimilarityProviderType, SimilaritySession, SimilaritySessionNode, load

provider_type = SimilarityProviderType["MCRIT"]
provider = provider_type.create(provider_type.get_default_settings())
session = SimilaritySession()
session.add_provider(provider)
# add nodes, session.graph.add_edge(a, b), then session.run()
```

`session.run()` returns immediately; wait on `completion.is_finished` as shown in the [official docs](https://docs.binary.ninja/guide/similarity.html#python-and-headless-usage). See `examples/headless_mcrit.py`.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"
ruff format . && ruff check . && ty check
python -m pytest tests
```

The tests run against a stub of the Binary Ninja API and need no license. To run the same suite, plus the tests that analyse the zlib builds in `tests/fixtures/`, against a real headless Binary Ninja:

```bash
MCRIT_TEST_REAL_BINARYNINJA=1 python -m pytest tests
```

For `import binaryninja` to resolve in your editor, run Binary Ninja's `scripts/install_api.py` with the virtual environment activated (macOS: `python "/Applications/Binary Ninja.app/Contents/Resources/scripts/install_api.py"`). It picks the environment up from `VIRTUAL_ENV`; without it, the API goes into your user site-packages. `pyproject.toml` already points Pyright at `.venv`.

## License

[GPL-3.0-only](LICENSE), matching the MCRIT projects it works with.

The plugin talks to an MCRIT server (GPL-3.0-only, Daniel Plohmann and contributors) over REST and contains none of its code. The Binary Ninja export builds on the Binary Ninja frontend of [mcrit-plugin](https://github.com/danielplohmann/mcrit-plugin) (GPL-3.0-only).

| Component | License | Use |
|---|---|---|
| [SMDA](https://github.com/danielplohmann/smda) | BSD-2-Clause | dependency |
| [Requests](https://github.com/psf/requests) | Apache-2.0 | dependency |
| [zlib](https://www.zlib.net/) 1.3.1 | [zlib License](tests/fixtures/zlib_License.txt) | test binaries in `tests/fixtures/` |

Binary Ninja is proprietary software by Vector 35 and is not part of this repository.
