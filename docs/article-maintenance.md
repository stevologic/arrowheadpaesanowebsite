# Article and dependency maintenance

## Writer policy

The site article job uses the existing xAI secret and base URL, with
`GROK_MODEL=latest`. The account-visible stable numbered text models are
compared by numeric major/minor version, with 4.7 as the minimum. The selected
model is cached for the process and recorded in the edition's generator label.
Other model families and dated variants are excluded. A differently named
future model family needs a reviewed selector update rather than a guess.

If discovery, parsing, streaming, or a factual rewrite fails, the Grok article
run fails without an older-model or offline replacement. The previous article,
archive, and repair file are retained. Schedule collection can still refresh
the independent slate before the writer fails. Other explicitly selected
providers keep their existing behavior and keys.

The existing 420-second stream deadline, 300-second idle timeout, one uniqueness
retry, and two factual rewrites remain. There is no provider transport retry
except one retry when the API explicitly rejects `response_format`. Responses
are closed on success and failure. Partial and truncated streams are rejected.
Streamed provider token measurements are logged without another API request;
no dollar estimate is inferred from elapsed time or Actions minutes.

## Cadence and integration

| Work | Cadence | Cost and review control |
| --- | --- | --- |
| Full sourced article | Daily, existing 09:37 UTC schedule | Latest eligible xAI; existing factual and signed Review gates |
| Schedule slate | Existing evening and late game windows | Deterministic; no LLM or markets; unchanged data preserves bytes |
| Python runtime updates | Weekly | Dependabot minor/patch group; majors separate; at most two version PRs |
| GitHub Actions and local npm tooling | Monthly | Dependabot minor/patch groups; majors separate; at most two version PRs per ecosystem |
| Major dependency decisions | Quarterly maintainer review, sooner for security | Review separate major PRs; no new recurring LLM job |
| Dependency security updates | On advisory detection once enabled | Default-branch security groups; no routine version PRs on main |

Feature PRs target develop. CI validates feature PRs and develop pushes, without
deploying integration commits or auto-merging them. A validated develop commit
is promoted through a reviewed PR to main. Main remains the deployment source
and requires `gates`. Dependabot PRs are excluded from automatic merge even
when they target main. Existing unsigned Review and `hold` policies are retained.

The Dependabot configuration must land on the default branch before GitHub
activates it. Non-default `target-branch` options affect version updates;
security updates always use the default branch. The separate main entries
disable version PRs only (`open-pull-requests-limit: 0`) and group security
updates. They do not enable security alerts or updates in repository settings.
See [GitHub's configuration reference](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference).

## Verified baseline and remaining setup

Inspected main `8720a30e8a0f9f5425fb7141176ac21c0c34e1bf` on October 12, 2026.
PRs 189, 192, 195, and 196 are merged. Superseded PRs 185, 186, 187, 188, 190,
and 191 are already closed. Only PR 96 remains open, draft and held for
Cloudflare comments/security setup. No open issues or integration branch were
present. Historical and unmerged branches are preserved.

[Account run 38153334416](https://github.com/stevologic/arrowheadpaesanowebsite/actions/runs/38153334416/job/114511127206)
advertised exact `grok-4.7` with HTTP 200, while selecting 4.6, timing out, and
calling 4.5 with a 4.6 generator label. Fresh repository variables now select
4.7 but still list 4.6 as fallback; the workflow/code policy removes dependence
on those stale fallback variables. [xAI documents the exact 4.7 ID](https://docs.x.ai/developers/grok-4-7).

Schedule branch `narrative/update-20261011-1021` has the same parsed slate as
`4cd130d`; its diff is byte formatting only. Semantic equality before writing
now preserves existing bytes, while changed scores/kickoffs/networks are written.

Dependabot alerts were reported disabled (HTTP 403), and the existing credential
cannot enable them without `admin:repo_hook`. No new credential or permission
was requested. A repository administrator must enable security alerts and
security updates through the existing GitHub settings before those updates run.

The initial lockfile repair removed high/critical findings. The reviewed
`hugo-bin` 0.165.0 update also removes the file-type/downloader moderate
advisories: the combined npm audit now reports zero vulnerabilities. Local
tooling requires Node 22.12 or newer and explicitly installs extended Hugo
0.152.2, matching production. Read-only CI installs the manifest without
scripts, audits at moderate severity, then explicitly exercises only the
Hugo wrapper installer and builds with it. The upgraded download-artifact v8
is tested against upload-artifact v4 ZIPs, including hidden publish metadata
and exact JSON/SVG bytes. Python minimums are requests 2.34.2 and PyYAML 6.0.3;
setup-python v7 uses its supported runner runtime without changing Python 3.11.

The publisher now opens a draft feature PR to develop. All PRs, including
edition-only and `narrative/update-` bot heads, run the actual required
PR-event `gates` job; neither a path filter nor a job condition skips it.
GitHub places GITHUB_TOKEN-created PR runs in an approval-required state.
A maintainer selects **Approve workflows to run** in the PR/Actions UI,
reviews the edition and any holds, and promotes validated develop through a
reviewed main PR. Karen's signed PASS remains mandatory for Review editions.
The sole deployment path is pages.yml after main integration. See
[GitHub's bot trigger rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow) and
[eligible required checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks).

The publisher no longer dispatches redundant CI, polls for up to 18 minutes,
attempts an ineligible merge, or carries an Actions write permission. An open
publication PR to main/develop applies backpressure before collection/provider
work, preventing repeated paid drafts and branch churn while approval or QA is
pending. Review/merge resumes the normal unchanged daily/schedule-only cadence.
The queue lookup fails closed on API errors, and the workflow summary links
the queued draft. No paid article generation or fabricated sports content is
needed to validate the workflow repair.

Fresh repository API reads report Dependabot alerts disabled (HTTP 404),
Dependabot security updates disabled, and
`automated-security-fixes` as `enabled: false, paused: false`. This configuration
does not activate that setting. An authorized repository administrator must
open Settings → Security and quality → Advanced Security, confirm Dependency
graph and Dependabot alerts are enabled, then enable **Dependabot security
updates**. The existing dependabot.yml already groups security updates on main;
no extra account-wide toggle or new credential is required. See
[GitHub's setup instructions](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/configure-security-updates).

Shared maintenance-action contract 1.1.0 is available at immutable pin
`stevologic/oss-deps-explorer/actions/maintenance@01b814e47b08d3949dd8288f0c426b7ee12630ca`
in draft PR 32. Its owner confirms no overlap with the custom content pipeline.
Adoption remains a separate reviewed consumer draft, read-only by default,
preserving current providers, credentials, protected develop, and signed gates.
