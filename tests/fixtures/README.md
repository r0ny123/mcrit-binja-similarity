# Test binaries

Small, benign Mach-O executables and ELF shared objects for the tests that run against a real
Binary Ninja (`MCRIT_TEST_REAL_BINARYNINJA=1`). Binary Ninja loads both formats on every host OS,
so all CI runners analyse the same bytes.

`zlib-<arch>-<opt>` link the zlib 1.3.1 sources (adler32, deflate, inffast, inflate, inftrees,
trees, zutil) with a minimal `main`, built by Apple clang 21 with `-arch <arch> <opt>` for
`x86_64` and `arm64` at `-O2` and `-Os`.

`zlib-armv7-<opt>` (ARM) and `zlib-thumb2-<opt>` (Thumb-2) are 32-bit ARM ELF shared objects of the
same sources and `main`, compiled freestanding with `-DZ_SOLO` by Apple clang 21
(`--target=armv7a-linux-gnueabihf -marm` or `-mthumb`, `-fPIC <opt>`) and linked by
`ld.lld -shared --build-id=none`. `crc32` stays an undefined import.

Symbols are kept so tests can pick corresponding functions by name.

zlib is distributed under the license in [zlib_License.txt](zlib_License.txt).
