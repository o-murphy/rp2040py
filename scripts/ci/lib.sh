#!/bin/sh
# Shared helpers for the firmware CI scripts in this directory (kaluma.sh, ...). Source it; do not run it.
#
# A script runs many steps - one firmware version after another - inside ONE job, so the package is built once per Python runtime instead of once per firmware.
# A failing step must not hide the ones after it: ci_step records the outcome and returns, and ci_finish exits non-zero if any hard step failed.

CI_RESULTS="$(mktemp)"
CI_FAILED=0

# ci_step <name> <timeout> <command...>   hard step: a failure fails the job (after the remaining steps have run)
# ci_soft_step <name> <timeout> <command...>   soft step: a failure is an annotation and a "soft-fail" row, never a job failure
_ci_run() {
    kind="$1"; name="$2"; limit="$3"; shift 3
    echo "::group::$name"
    start=$(date +%s)
    if timeout "$limit" "$@"; then
        status="pass"
    else
        status="FAIL"
    fi
    elapsed=$(( $(date +%s) - start ))
    echo "::endgroup::"
    if [ "$status" = "FAIL" ]; then
        if [ "$kind" = "soft" ]; then
            status="soft-fail"
            echo "::warning title=$name::soft-failing step failed after ${elapsed}s (see the log group above)"
        else
            CI_FAILED=1
            echo "::error title=$name::step failed after ${elapsed}s (see the log group above)"
        fi
    fi
    printf '| %s | %s | %ss |\n' "$name" "$status" "$elapsed" >> "$CI_RESULTS"
}

ci_step() { _ci_run hard "$@"; }
ci_soft_step() { _ci_run soft "$@"; }

# ci_finish <title>: write the table to the job summary (when running on GitHub) and exit with the verdict.
ci_finish() {
    {
        echo "### $1"
        echo
        echo "| step | result | time |"
        echo "|---|---|---|"
        cat "$CI_RESULTS"
    } > "${CI_RESULTS}.md"
    cat "${CI_RESULTS}.md"
    if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
        cat "${CI_RESULTS}.md" >> "$GITHUB_STEP_SUMMARY"
    fi
    rm -f "$CI_RESULTS" "${CI_RESULTS}.md"
    exit "$CI_FAILED"
}
