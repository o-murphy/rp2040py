# 0007. Configurable bootrom revisions (--bootrom, B0/B2)

- Status: Implemented
- Conceived: 2026-08-03 · #16 (closes #11)
- Related: #16, #11

<!-- migrated verbatim from docs/BACKLOG.md lines 1420-1425 -->

## Bootrom B0/B2 support (issue #11) — DONE

Landed in `cf4eed8` (#16) + follow-up `#17`. Design rationale (ELF `PT_LOAD` extraction,
`pyelftools` as a normal dependency, `--bootrom <tag|path>` wiring) is preserved in the PR
history. No remaining work here.


<!-- migrated verbatim from docs/PORTING.md lines 767-781 -->

### Configurable bootrom revisions (`--bootrom`) - upstream ships exactly one, hardcoded

`device/bootrom.py` exposes `BOOTROM_B1` (used by default, unchanged from the original port) plus
`--bootrom <b0|b1|b2|path>` (`cli/__init__.py`'s `_resolve_bootrom_words`, downloaded/cached the
same way firmware images are via `firmware_retrieve.py`'s `BOOTROM` spec) to boot against a
different bootrom revision's ELF or raw binary instead - see
[README](../README.md#bootrom-revisions) and `docs/BACKLOG.md`'s "Bootrom B0/B2 support (issue
#11)".

Upstream's `demo/bootrom.ts` ships exactly one `Uint32Array` (`bootromB1`, "revision: B1"),
imported directly by every demo script with no alternative and no CLI flag to select a different
one - confirmed by reading the file directly, it's the same ~4,100-word data-only export
`bootrom.py`'s `BOOTROM_B1` was ported from in the first place, just with no B0/B2 counterpart
alongside it anywhere in the repo.

