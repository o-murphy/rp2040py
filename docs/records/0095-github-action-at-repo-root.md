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

## Addendum (2026-08-24): install from the action's own checkout, not PyPI

The `version`/`python_version` inputs above installed `rp2040py[fs]` from PyPI, pinned by a
separate `version` string the caller had to keep in sync with whatever `uses:` ref they pinned -
two numbers naming the same thing, with no way to notice if they drifted (e.g. `uses:
o-murphy/rp2040py@v0.4.0` paired with `version: "0.3.1"` silently runs the older PyPI release
against whatever composite-step logic `v0.4.0`'s checkout carries).

Pointed out by the user: `ballistics-lab/cibuildmp`'s own root `action.yml` avoids exactly this by
installing `uv tool install "${{ github.action_path }}"` - `github.action_path` is where the
runner already checked out *this* action's repo, at the ref the caller pinned, so the installed
tool and the pinned ref are structurally the same thing rather than two values a caller must keep
matched. rp2040py now does the same:

* Dropped the `version` input entirely - the `uses: o-murphy/rp2040py@<tag>` ref *is* the version;
  there is no second place to pin one.
* `uv tool install --python "${{ inputs.python_version }}" "$SPEC"`, where `SPEC` starts as
  `github.action_path` and gets `[extras]` appended when the (new) `extras` input is non-empty.
* Added an `extras` input (default `"fs"`, matching the previous hardcoded `rp2040py[fs]`) so the
  extras list is still overridable, same shape as cibuildmp's own `extras` input - default `""`
  there since cibuildmp has no equivalent of `fs` that most callers want on.
* README's CI usage example and the `[Unreleased]` CHANGELOG entry updated to match (no `version:`
  key; new `extras:` key).

No change to `Consequences` above - still Marketplace-eligible only once a release is tagged and
published by hand, and this repo's own CI still doesn't exercise the action end-to-end.
