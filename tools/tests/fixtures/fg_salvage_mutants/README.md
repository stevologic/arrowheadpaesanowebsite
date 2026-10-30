# FG salvage mutants (Karen QA on f1034e00)

Seven patches that used to leave `test_archive_replay_qa` and
`test_narrative` green. Each file is a clean `git diff` from the
unmutated tree. Apply one at a time from the repo root:

```
git apply --check tools/tests/fixtures/fg_salvage_mutants/X4-count-match-removed.patch
git apply tools/tests/fixtures/fg_salvage_mutants/X4-count-match-removed.patch
python3 -m unittest tools.tests.test_archive_replay_qa tools.tests.test_narrative
git checkout -- tools/chiefs_narrative/facts.py tools/chiefs_narrative/generate.py
```

`git apply --check` must pass with no fuzz.

| Mutant | Patch | Killing test |
| --- | --- | --- |
| X4 count match removed | `X4-count-match-removed.patch` | `test_claimed_count_mismatch_is_not_rewritten` |
| X4b claimed count ignored | `X4b-claimed-count-ignored.patch` | `test_missing_claimed_count_is_not_rewritten` |
| X6 states-a-miss bypassed | `X6-states-a-miss-bypassed.patch` | `test_make_claim_wrong_yardage_is_not_rewritten` |
| X11 team check bypassed | `X11-team-check-bypassed.patch` | `test_wrong_team_miss_is_not_rewritten` |
| X12 rewrite still-states-miss removed | `X12-rewrite-still-states-miss-removed.patch` | `test_rewrite_rejected_when_it_drops_miss_language` |
| X7a failing title-join always dropped | `X7a-title-join-always-dropped.patch` | `test_clean_title_join_neighbor_survives` |
| X13 build path stops holding | `X13-build-stops-holding.patch` | `test_build_holds_unfixable_hard_leftover_and_gates_fail` |

Rewrite-path pins that are not themselves patches, but kill the
string-replace mutants that used to survive these fixtures:

| Mutant | Killing test |
| --- | --- |
| X11 `team = team or 'KC'` | `test_unidentified_team_is_not_rewritten` |
| F3 `made` rewritten to `missed` | `test_made_from_is_not_rewritten_to_missed` |
| F4 first `\\d+` rewritten | `test_rewrite_changes_only_kick_yards_not_first_number` |
| D3 X11 plus post-rewrite team check removed | `test_unidentified_team_is_not_rewritten` |
