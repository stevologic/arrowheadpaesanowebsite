# §1.6 self-check (PR #341, this HEAD)

Answered yes / no / n.a. line by line. This is the checklist, not a design-statement dump.

1. Did any workflow change make a **required** gate silently skip (`if:` that drops the job, `continue-on-error`, `|| true` around a refuse, paths-ignore that omits the PR)? **no.** `ci.yml` `gates` still has no `if:`. `--hold-repair` was **added** to that required job (after `--pages`) so a green CI predicts Pages. `pages.yml` "Refuse held edition" still has no `if:` and still sits before Publish. `review_gate --pages || true` is still absent. Automerge remains skipped when `base != main` (by design, not a silent skip of `gates`).
2. Were **pinned hashes** or generated lockfiles regenerated? **n.a.** This PR does not pin third-party hashes and does not regenerate `go.sum` / `package-lock` / similar. No hash pins were left stale.
3. Do tests read **live** `data/narrative.json` or live archive edition JSON? **no.** `test_tests_do_not_read_live_edition_json` still forbids those needles. N3 fixtures use temp dirs and split path strings.
4. Is every class-coverage kill a **drop-one of production source** (facts.py / generate.py / edition_merge.py / review_gate.py / workflows) run against a repository unittest? **yes.** `tools/tests/b2_guard_mutation_receipt.py` is not named `test_*.py`. It is not a self-mutating meta-test.
5. Is each member run **6× control + 6× mutant**? **yes.** `N = 6`. Vacuous members fail the receipt (rc 1).
6. Does each receipt row paste the **test name**, **file:line** of the mutant, and **AssertionError actual vs expected** captured from the mutant run (not a hard-coded `FAIL` bit)? **yes.** See `tools/tests/b2_class_coverage/receipt.md` and `class_coverage.json`.
7. Was the **adversarial table** actually run on this HEAD (un/non lookbehinds, Unicode dashes / NBSP / Cf, following vs after, had reached, falling behind, once trailing, never trailed, digit vs word, singular TD)? **yes.** `tools/tests/b2_class_coverage/adversarial.md` plus `WindowScoringCounts.test_adversarial_table_head`.
8. Inventory **I1–I12** covered with an isolating fixture each? **yes.** Receipt members are tagged I1–I11; I12 is the adversarial table. N-a (full archive audit) is **not** in this PR.
9. `load_repair` fail-closed on missing / unreadable / invalid JSON (`holdAutomerge` true)? **yes.** Tests: `test_load_repair_missing_is_held`, `test_load_repair_invalid_json_is_held`, `test_load_repair_non_object_is_held`, `test_load_repair_unreadable_is_held`, `test_hold_repair_missing_repair_file_is_held`.
10. `_edition_paths_aligned()` **raises** when paths are not under `data/`? **yes.** `EditionPathError`; `--check-edition` rc 1. Tests: `test_edition_paths_aligned_raises_off_data_dir`, `test_check_edition_errors_when_paths_misaligned`.
11. `--hold-repair` on the CI `gates` job so green CI predicts Pages? **yes.** Added. **If that step is red solely because develop's `holdAutomerge` is still true, this PR does not weaken the check and does not edit `data/`.** N-hold is a separate data-only PR.
12. `git merge-tree --write-tree` vs `origin/develop` clean, and **no `data/`** in the tools PR diff? **yes** (re-verified on the push HEAD). Allowlist untouched.
13. CI at the exact new head, **attempt 1**, 0 flaky, no reruns? **yes** if the required `gates` job is green; **stop and report** if it is red only because of the live hold flag (do not rerun, do not edit `data/`).
14. Manual UI merge hard-fail on `holdAutomerge`? **yes, via the required `gates` job** (`--hold-repair`). Pages still refuses independently. N-d addressed; N-hold still needs the data PR before promote to main.
15. Full scan / audit of old archives (N-a)? **n.a. / deferred.** Explicitly out of this PR.
16. N2 `narrative_repair.json` dropped-list entry? **n.a. / deferred.** Not in this PR.
17. Widen automerge allowlist? **no.** Unchanged. #343 allowlisted Preview; #340 off-allowlist was refused. This PR does not add SVG / wire / repair paths.
