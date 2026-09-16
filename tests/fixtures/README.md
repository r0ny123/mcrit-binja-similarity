# Test binaries

Small, benign Mach-O executables for the tests that run against a real Binary Ninja
(`MCRIT_TEST_REAL_BINARYNINJA=1`). Binary Ninja loads Mach-O on every host OS, so all CI runners
analyse the same bytes.

`zlib-<arch>-<opt>` link the zlib 1.3.1 sources (adler32, deflate, inffast, inflate, inftrees,
trees, zutil) with a minimal `main`, built by Apple clang 21 with `-arch <arch> <opt>` for
`x86_64` and `arm64` at `-O2` and `-Os`. Symbols are kept so tests can pick corresponding
functions by name.

zlib is distributed under the license in [zlib_License.txt](zlib_License.txt).
