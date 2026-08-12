# 0002. mklittlefs image handling

- Status: Implemented
- Conceived: 2026-07-31 · #6
- Related: #6 · note 0003 (image format)

<!-- migrated verbatim from docs/PORTING.md lines 455-498 -->

### `mklittlefs` used to silently corrupt images when reusing an output path with different block params

`build_littlefs_image()` originally "updated in place" when `--output` already existed: read the
existing file's bytes into `UserContext`, then mounted it with *this run's* `block_size`/
`block_count`. When those didn't match whatever built the file previously (e.g. rebuilding
`littlefs.img` first at MicroPython's default block count, then again at Kaluma's), littlefs-python
either silently ignored the new values (image stayed the old size) or reformatted and dropped
existing files - both with no error or warning, reproducible even against a validly-built image,
not just a stale/foreign one. Fixed by always building fresh from an empty buffer and requiring
`-f`/`--force` to overwrite an existing path (raising `ValueError` otherwise) - see the CHANGELOG's
`mklittlefs -f`/`--force` entry.

### `mklittlefs` crashes at exit under PyPy (littlefs-python, not a port bug)

`rp2040py mklittlefs` writes the image correctly and prints its success message, then aborts
(SIGABRT: `littlefs/lfs.c:6200: lfs_file_sync: Assertion `lfs_mlist_isopen(lfs->mlist, (struct
lfs_mlist*)file)' failed`) - but *only* when the whole process is running under PyPy (e.g. `uv tool
install --python pypy-3.10 rp2040py[fs]`, which is exactly what
`.github/actions/setup-rp2040py`'s composite action does for the emulator speedup). Never
reproduces under CPython.

Root cause, isolated by instrumenting each step with `flush=True` prints: every operation
(`LittleFS(...)`, `lfs.open(...)`/write/close, an explicit `lfs.unmount()`, even a manual
`gc.collect()` immediately after closing the file) completes and prints successfully - the abort
only happens later, during interpreter shutdown itself, after the process has nothing left to do.
This points at `littlefs-python`'s Cython `__dealloc__` finalizers for the `LittleFS`/file C
objects running in an order CPython's deterministic refcounting happens to always get right, but
that PyPy's non-refcounting GC doesn't guarantee - a finalizer apparently re-closes a file object
whose underlying `lfs_mlist` entry was already removed when it was correctly closed the first time
(via its own `with` block). Neither an explicit `lfs.unmount()` nor a manual `gc.collect()` forces
that finalization to happen in-order early enough to dodge it - this is a real upstream
`littlefs-python`/PyPy interaction, not something fixable from call-site code alone.

Workaround: `_cmd_mklittlefs` in `cli/__init__.py` calls `os._exit(0)` right after printing success
- but only when `sys.implementation.name == "pypy"`, never unconditionally. The image on disk is
already complete and correct by that point, so skipping the rest of interpreter shutdown is safe
for *this* process's exit - but doing it unconditionally would also kill the caller's process if
`_cmd_mklittlefs`/`main()` is ever invoked in-process rather than as the real entry point (e.g. from
a test suite, confirmed the hard way while first writing this fix). This only papers over the CLI's
own one-shot process exit; a long-running PyPy program that calls the public
`build_littlefs_image()` as a library and keeps running afterwards can still hit the same crash
later, unpredictably, whenever PyPy's GC happens to finalize the stale objects - there's no
workaround for that case from rp2040py's side.

