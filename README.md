# MCRIT Binary Similarity for Binary Ninja

Adds [MCRIT](https://github.com/familiary/mcrit) as a provider in Binary Ninja's [Binary Similarity](https://docs.binary.ninja/guide/similarity.html) sessions, next to Google BinDiff and WARP. Matching, apply and side-by-side diff rendering all run through Binary Ninja's own similarity UI and API.

This is a session provider for comparing binaries. For the MCRIT sidebar that browses a corpus inside Binary Ninja, see the [MCRIT plugin](https://github.com/familiary/mcrit-plugin).

## Requirements

- Binary Ninja **Ultimate** 6.0 (build 10601 or newer). Binary Similarity is Ultimate-only; on other editions the plugin loads and logs that it needs Ultimate.
- A reachable MCRIT server: its REST API, or [mcritweb](https://github.com/familiary/mcritweb) in front of it.
- Binaries on x86, x86_64 or aarch64 — the architectures SMDA can export from Binary Ninja's analysis.

## Install

1. Put the repository into Binary Ninja's plugin folder:

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

2. Install the two Python dependencies (`smda`, `requests`) into Binary Ninja's Python. The easy way is the command palette (`Cmd+P` / `Ctrl+P`), command `Install python3 module`, run once per package. From a terminal, install into the module folder Binary Ninja uses:

   ```bash
   # macOS; on Linux use ~/.binaryninja, on Windows "%APPDATA%\Binary Ninja"
   python3.13 -m pip install --target ~/Library/Application\ Support/Binary\ Ninja/python313/site-packages -r requirements.txt
   ```

   `python3.13` must match the version Binary Ninja bundles (the folder is named `python3xx`). If `python.interpreter` points at your own Python, install into that one instead.

3. Restart Binary Ninja. The log shows `Registered MCRIT Binary Similarity provider`.

4. Open Settings (`Cmd+,` / `Ctrl+,`), search for `mcrit_similarity`, and set the server URL: `builtin` to run MCRIT inside Binary Ninja (see below), `http://127.0.0.1:8000/` for a local MCRIT API, or `https://<host>/api/` plus username and API token for mcritweb. Then run `Plugins > MCRIT Similarity > Test Server Connection`: it checks the URL and token without starting a session.

## No server: built-in MCRIT

Set the server URL to `builtin` and the plugin runs MCRIT inside Binary Ninja, with no Docker, MongoDB or account. It needs the `mcrit` package in Binary Ninja's Python: command palette, `Install python3 module`, then `mcrit` (or `mcrit` in place of `-r requirements.txt` in the pip command from step 2). That install is large, since MCRIT brings numpy, scipy and lief.

- Samples are kept in memory until Binary Ninja exits, so each run uploads and indexes them again. Two binaries of a few thousand functions take seconds.
- It matches only the binaries in your sessions. Matching against a shared corpus, with its labels and families, needs a server.
- MCRIT runs in a single process and listens only on 127.0.0.1, behind a token generated for each run.
- Stopping a session takes effect once the sample being indexed or matched is done.

## Get an MCRIT server

For a corpus that persists and is shared, the plugin connects to an MCRIT server. The quickest one is [docker-mcrit](https://github.com/familiary/docker-mcrit):

```bash
git clone https://github.com/familiary/docker-mcrit && cd docker-mcrit
chown -R 10001:10001 storage/mcritweb
docker compose up -d
```

The first `up` builds both images and takes a while. Register the first account through the web interface — it becomes the administrator. The MCRIT API is on `http://127.0.0.1:8000/` and mcritweb on `http://127.0.0.1:5000/api/` (loopback only). Point the plugin at either; an existing server works the same way with its URL.

> [!NOTE]
> Through mcritweb, uploading samples needs a contributor or admin token, and the matching parameters (MinHash threshold, PicHash size, LSH bands) are dropped. For those settings to apply, point the plugin at the direct API instead.

## Run a session

1. `File > New Similarity Session`, then `Quick Setup`.
2. Pick the reference binary A and the binary B to find matches in (`Browse files...` if they are not open). This creates the edge `A -> B`.
3. Pick a profile. *Review matches first* runs MCRIT next to BinDiff and WARP; *Apply matches automatically* adds the Metric Similarity Resolver, which applies the best match by itself. For a session with MCRIT alone, remove the other providers under `Configuration`, where `Add Provider > MCRIT` also adds it to a session set up by hand; `Profiles > Save Current as Profile...` keeps that setup for the next Quick Setup.
4. Run the session when Quick Setup offers to, or with `Start`.

Every function in B gets its best MCRIT match in A, with MCRIT's 0–100 score mapped to Binary Ninja's 0–255 similarity scale. Open B's node to list the results; typing `MCRIT` in the search box keeps only this provider's. `Render` shows the pair side by side under `Rendered Result`, and `Apply` copies names, types, variable info and comments from the matched function, like the built-in providers; both ask to open the binaries first if they are not. A placeholder name such as `sub_140005480` is not copied, since it names an address in the other binary. Functions whose name both sides share (check a few known ones) should match themselves — that is the quickest way to confirm the setup works.

## Settings

Settings > **MCRIT Binary Similarity** (user scope):

| Setting | Meaning |
|---|---|
| Server URL | Default `http://127.0.0.1:8000/`; `builtin` runs MCRIT inside Binary Ninja |
| Username | `username` header for mcritweb |
| API token | Moved into the system keychain on first use; the field then reads empty. Enter a new token to replace it |
| Request timeout | Seconds per HTTP request |

To delete a stored token, in the Python console:

```python
SecretsProvider["SystemSecretsProvider"].delete_data("mcrit_similarity.api_token")
```

Per-session options live in the session's Configuration dialog: MinHash threshold, PicHash size, LSH bands, max candidates, include library matches, persist samples, family and version for uploads, plus two opt-ins:

- **Apply corpus labels** — when applying a match where neither function has a real name, rename the source function to the target's MCRIT function label.
- **Query per node** — with several incoming edges, query the whole corpus once and split the result per edge instead of running one MatcherVs per edge. Slower on large corpora; direct API only.

> [!NOTE]
> The [MCRIT plugin](https://github.com/familiary/mcrit-plugin) (IDA/Binary Ninja/Ghidra) registers its settings as **MCRIT** under `mcrit`. This plugin's settings are a separate **MCRIT Binary Similarity** group, so both can be installed side by side and point at the same server.

## Security

- The API token is stored in the system keychain (the log warns when none is available and it stays in Binary Ninja's settings). It is never logged.
- A token is only sent over HTTPS, or HTTP to a loopback address. Redirects are refused, so a token cannot be forwarded to another host.
- Certificates are verified. For a private CA, set `REQUESTS_CA_BUNDLE` to its bundle before starting Binary Ninja.
- Uploads are opt-in for remote servers (see *How matching works*).

## How matching works

Each view is exported to an SMDA report from Binary Ninja's analysis and looked up on the server by the SHA256 of that report. For PE files this is the hash of the mapped image SMDA exports, which can differ from the file's own SHA256, so a sample already on the server under its file hash is uploaded again as a separate sample. An unknown sample is uploaded only when *persist samples* is on, with no family unless you set one. It is on by default for a local server, always on for the built-in one, and off for a remote one, because an upload sends the binary's disassembly to that server; turn it on per session when you mean to. With it off, the visit fails with a log message naming the server. Matching then runs MCRIT's MatcherVs, and only the matched functions are fetched afterwards.

Confidence reflects how unique a match is: a tie with another candidate halves it, a lead of 10+ points keeps it, and a PicHash match gets 255 only when no other function shares that PicHash. A result is named after the target's MCRIT function label when the target has no symbol, or `name (label)` when both differ.

## Diff rendering

Rendering aligns the two functions by PicBlock hash, then pairs leftover blocks by escaped-instruction similarity. Inside paired blocks only differing instructions are marked changed; one-sided blocks are coloured removed (A) or added (B). Function entries always pair with each other, so a difference there is marked changed rather than painting the header as removed. Identical functions stay unpainted — a score of 100 alone does not mean identical, so those are diffed too. Changed lines are orange, removed red and added green, in the Graph and Linear tabs and in every view the selector offers: Disassembly, Low/Medium/High Level IL and the Pseudo C, Objective-C and Rust languages.

SMDA reports are cached under `<Binary Ninja user folder>/mcrit_similarity/smda_reports` (512 MiB / 1000 files, least-recently-used removed). After a restart, sessions reuse them, and reports up to 64 MiB are reloaded in the background once analysis finishes. Delete the folder to clear the cache.

## Headless

```python
from binaryninja import SimilarityProviderType, SimilaritySession, SimilaritySessionNode, load

provider_type = SimilarityProviderType["MCRIT"]
provider = provider_type.create(provider_type.get_default_settings())
session = SimilaritySession()
session.add_provider(provider)
# add nodes, session.graph.add_edge(a, b), then session.run()
```

`session.run()` returns immediately; wait on `completion.is_finished` as shown in the [official docs](https://docs.binary.ninja/guide/similarity.html#python-and-headless-usage). A complete runnable version is [`examples/headless_mcrit.py`](examples/headless_mcrit.py):

```bash
# a Python that has smda and requests, plus Binary Ninja's API on PYTHONPATH
PYTHONPATH="/Applications/Binary Ninja.app/Contents/Resources/python" \
    .venv/bin/python examples/headless_mcrit.py reference-binary match-binary
```

It reads the server settings configured in Settings.

## Troubleshooting

Binary Ninja's own [troubleshooting guide](https://docs.binary.ninja/guide/similarity.html#troubleshooting) covers session-side issues (no results, resolver thresholds, results that cannot be navigated or applied). Plugin-side ones:

- **Log says `MCRIT Similarity requires Binary Ninja Ultimate 6.0+`** — Binary Similarity needs Ultimate; the plugin intentionally stays idle otherwise.
- **No results / requests fail** — check the server URL: mcritweb wants the trailing `/api/`, the direct API does not. Raise *Request timeout* for slow uploads.
- **`403`** — the API token is missing or wrong (`Test Server Connection` shows which), or, on upload, it lacks contributor or admin rights. If both samples are already on the server, turning *persist samples* off also unblocks the session.
- **Log warns `MCRIT applied different matching parameters than requested`** — the server (mcritweb in particular) ignored or capped the MinHash threshold, PicHash size or LSH bands you set; the warning lists each as `requested -> applied`. Point the plugin at the direct API to use your values.
- **No matches between different architectures** — expected: MCRIT 1.13+ matches within one architecture only.
- **Render warns `no SMDA report is cached` after a restart** — re-run the session; reports reload in the background once analysis finishes.
- **`cannot import the mcrit package: the module named mcrit at … is something else`** — a Binary Ninja plugin folder named `mcrit` takes the package's name. Rename that folder, for example to `mcrit-plugin`.
- **A session option changed under `Configuration` has no effect** — Binary Ninja 6.0 passes MCRIT's per-session options on only when they are changed in the same Configuration dialog in which MCRIT was added. Select MCRIT there, `Remove Provider`, `Add Provider`, change the options, then close the dialog. Server URL, username, API token and timeout also have defaults under Settings > MCRIT Binary Similarity.
- **Settings look duplicated** — that is the MCRIT disassembler plugin's own **MCRIT** group; this plugin's settings are under **MCRIT Binary Similarity**.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"
ruff format . && ruff check . && ty check
python -m pytest tests
```

The default suite runs against a stub of the Binary Ninja API and needs no license. To run it against a real headless Binary Ninja — plus the tests that analyse the zlib builds in `tests/fixtures/` — first expose the API to the venv, then run:

```bash
python "/Applications/Binary Ninja.app/Contents/Resources/scripts/install_api.py"  # with the venv activated
MCRIT_TEST_REAL_BINARYNINJA=1 python -m pytest tests
```

`install_api.py` picks up the environment from `VIRTUAL_ENV`; `pyproject.toml` already points Pyright at `.venv`. On Linux the app path differs; the script lives in Binary Ninja's `Resources/scripts/`.

The end-to-end tests additionally start a throwaway MCRIT server (in-memory, no MongoDB, no worker) on a loopback port and run real sessions over the zlib fixtures. They need `pip install -e ".[dev,e2e]"` and are opt-in:

```bash
MCRIT_TEST_REAL_BINARYNINJA=1 MCRIT_TEST_E2E=1 python -m pytest tests -m e2e
```

- `MCRIT_E2E_SERVER_URL` tests against a server you run instead. The tests reset it via `POST /respawn` — never point it at data you want to keep.
- `MCRIT_E2E_STORAGE=mongodb` runs the same tests against MCRIT's deployed shape (MongoDB plus worker; `MCRIT_E2E_MONGO_HOST`, `MCRIT_E2E_MONGO_PORT`, `MCRIT_E2E_MONGO_DB`). `tests/e2e/mcrit_server.py` launches either shape from the command line.

## License

[GPL-3.0-only](LICENSE), matching the MCRIT projects it works with. The plugin talks to an MCRIT server over REST and contains none of its code.

| Component | License | Use |
|---|---|---|
| [SMDA](https://github.com/danielplohmann/smda) | BSD-2-Clause | dependency |
| [Requests](https://github.com/psf/requests) | Apache-2.0 | dependency |
| [zlib](https://www.zlib.net/) 1.3.1 | [zlib License](tests/fixtures/zlib_License.txt) | test binaries in `tests/fixtures/` |

Binary Ninja is proprietary software by Vector 35 and is not part of this repository.
