# 0003. Note — littlefs image format vs. old MicroPython

- Status: Note (research)
- Recorded: 2026-07-31
- Related: 0002 (mklittlefs), 0010 (persistence)

<!-- migrated verbatim from docs/PORTING.md lines 426-454 -->

### littlefs image format vs. old MicroPython (not actually a port bug)

`ci-micropython.yml` builds a `littlefs.img` via `tests/mklittlefs.py` (now the `mklittlefs`
subcommand, `src/rp2040py/cli/mklittlefs.py`) and expects MicroPython to
auto-run `main.py` from it. This worked for MicroPython 1.28 but hung indefinitely - CPU spinning
forever re-acquiring an SIO hardware spinlock with interrupts disabled - for every older version
(<=1.21) in the CI matrix.

Root cause, confirmed by bisecting against the JS original itself (running the real
`wokwi/rp2040js` checkout locally against the same firmware/image reproduced the identical hang,
including on the exact commit whose CI run shows green - ruling out a port-specific bug entirely):
`tests/mklittlefs.py` (as it was at the time) depended on `littlefs-python>=0.4.0` with no upper bound, and newer releases
of that package default to a newer littlefs on-disk format (v2.1) than the one MicroPython <=1.21's
bundled littlefs C implementation understands (v2.0). Confirmed byte-for-byte: `LittleFS(...,
disk_version=0x00020000)` under `littlefs-python==0.18.0` produces an image identical to
`littlefs-python==0.4.0`'s default output (which upstream rp2040js's `test/requirements.txt` pins
exactly, sidestepping the issue there). MicroPython 1.28's newer littlefs implementation reads
*both* formats fine, so pinning the on-disk *format* - not the `littlefs-python` package version -
is a strictly better fix: the `mklittlefs` subcommand and the README's filesystem-image snippet now both
pass `disk_version=0x00020000` explicitly, keeping `littlefs-python` itself unpinned (avoids that
package's own baggage - 0.4.0 imports the deprecated `pkg_resources` API, which newer `setuptools`
no longer bundles by default).

`mklittlefs` now exposes this as a `--disk-version {2.0,2.1}` flag (`LITTLEFS_DISK_VERSIONS` /
`build_littlefs_image(..., disk_version=...)` in `mklittlefs.py`) rather than hardcoding `2.0`
unconditionally, still defaulting to `2.0` for the reasons above. The `fs` extra's floor was also
raised to `littlefs-python>=0.18.0` (from `>=0.4.0`) - the version the byte-for-byte comparison
above was actually run against - while still leaving the upper bound open.

