# October 10, 2026 narrative refresh

The live site was still displaying the October 5 Review during Kansas City's
Week 5 bye. The October 10 edition is a researched Week 6 Preview of the October
18 Chargers game. It preserves the signed October 5 Review and the site's
templates, styles, and publication gates.

## Why the narrative remained stale

- The latest full scheduled run, [38064065539](https://github.com/stevologic/arrowheadpaesanowebsite/actions/runs/38064065539),
  failed `test_karen_archive_growth` and `test_karen_archive_replay`. The generated
  Review included opponent statistics, but the subject binding checked those
  numbers against Kansas City's recap fields. The generated claims about 28
  first downs, zero sacks on 52 attempts, and 10 hits collided with the Chiefs'
  18 first downs, two sacks on 30 attempts, and eight hits.
- Earlier Review PRs also carry salvage repair metadata and require the
  established Karen ed25519 sign-off before publication. Schedule-only runs
  preserve that edition, so they do not create a fresh approved narrative.
- Draft [PR #189](https://github.com/stevologic/arrowheadpaesanowebsite/pull/189)
  contains separate archive/checker work. It remains separate from this content
  release; the underlying full-generation checker issue is not fixed here.

The new Preview passes the existing content checks. Its repair metadata records
the clean result returned by those checks instead of retaining the prior
edition's salvage hold. No approval or signature requirement is changed.

## Publication check diagnosis

The repository ruleset requires the `gates` check. `ci.yml` ignores edition-only
paths, while `edition-qa.yml` checks those paths under the name `edition-gates`.
The manually dispatched CI run passed all 280 tests and the publication gates,
but GitHub does not count checks from `workflow_dispatch` toward PR requirements.
See [GitHub's required-check documentation](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks#checks-from-some-workflow-jobs-are-not-evaluated).

Including this operational diagnosis in the PR allows the existing `pull_request`
CI workflow to run its full `gates` job. The archive and diagram additions are
outside the automatic-merge allowlist, so this release uses the authorized human
merge after the required checks pass. Deployment uses the existing Pages
workflow. Credentials, workflows, security settings, and paid services remain
unchanged.

## Content verification

- Official Chiefs schedule: 4-0, Week 5 bye, Chargers at Arrowhead October 18 at
  3:25 p.m. Central / 1:25 p.m. Arizona, CBS.
- Official team reports and attributed reporting cover Araiza's weekly award,
  Thornton's injury, Rice's precautionary exit, Slater's surgery and injured
  reserve placement, and the Chargers' current availability concerns.
- Week 5 injury designations are distinguished from unannounced Week 6 rulings.
  Harrison trade reporting is attributed league news with no asserted Chiefs
  acquisition or invented compensation.
- The published JSON includes source links. Local HTML checks cover the home,
  current narrative, and new archive pages, their October 10 date, sources, and
  six diagram assets. The signed October 5 archive remains byte-for-byte intact.

The current edition changes actual reporting and analysis, not only its date.
