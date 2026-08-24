# 0095. Move the `setup-rp2040py` composite action to the repo root

* Status: Implemented
* Conceived: 2026-08-24

## Context

`setup-rp2040py` (installs `rp2040py` as a standalone `uv tool`, then hands the `rp2040py` command
to later steps) has existed since before this record as
`.github/actions/setup-rp2040py/action.yml` - a normal path for an action meant only to be
referenced from *within* this repo's own workflows, or via `uses:
o-murphy/rp2040py/.github/actions/setup-rp2040py@<ref>` from another repo (already the case for
`ballistics-lab/micropython-bclibc`, see the README's "Used by" section).

GitHub Marketplace publishing requires the action metadata file to sit at the repository root -
a subdirectory action works fine for `uses:`, but cannot be listed. The user asked specifically to
make this action Marketplace-publishable.

## Decision

* Move `action.yml` from `.github/actions/setup-rp2040py/` to the repo root (`git mv`, then remove
  the now-empty `.github/actions/` tree). Nothing in this repo's own workflows referenced the old
  path (none of them consume the action - see "Consequences" below), so no other workflow file
  needed updating.
* Add a `branding:` block (`icon: cpu`, `color: blue`) - required for a polished Marketplace
  listing, cosmetic only, no behavioral change.
* Fix a stale doc reference in the same file while touching it: `python_version`'s description
  pointed at a `docs/PORTING.md` path that no longer exists (superseded by the
  `docs/records/`/`docs/reference/` restructure in [0032](0032-docs-restructure.md)); repointed to
  [0013](0013-cython-core.md), which is what the README's own Performance section now cites for the
  same PyPy-speedup claim.
* Document the new top-level `uses: o-murphy/rp2040py@<tag>` form in the README (new "Use in CI
  (GitHub Action)" subsection under Installation) and repoint the "Used by" bullet's path.
* Actually publishing to the Marketplace (tagging a release and checking "Publish this Action to
  the GitHub Marketplace" in GitHub's release UI) is a manual, human-only step outside what this
  session can do - out of scope here, left to the maintainer.

## Consequences

* **Pros:**
  * `uses: o-murphy/rp2040py@<tag>` now works and is eligible for Marketplace listing once a
    release is published.
  * No functional change to what the action does - same two inputs, same composite steps.

* **Cons / follow-up needed:**
  * **Breaking for existing external consumers of the old path.** Any workflow (e.g.
    `ballistics-lab/micropython-bclibc`, cited in the README) still using `uses:
    o-murphy/rp2040py/.github/actions/setup-rp2040py@<ref>` will break once it re-resolves against
    a ref where the old path no longer exists (a ref/SHA already pinned to a commit before this
    move keeps working - only `@main`/a moving branch ref breaks going forward). That repo is
    outside this session's scope and was **not** updated here; it needs its own follow-up PR to
    `uses: o-murphy/rp2040py@<tag>`.
  * This repo's own CI (`.github/workflows/*.yml`) tests the local checkout directly via `uv
    sync`/`uv run`, not via this action - so there is no in-repo workflow that exercises the
    published action end-to-end. Worth a dedicated smoke-test workflow at some point (not built
    here).
