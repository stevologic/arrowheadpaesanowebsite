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

The lockfile-only update raises `@xhmikosr/decompress` to 10.2.2 and
`http-cache-semantics` to 4.3.0 within existing constraints. The npm audit
changed from nine moderate, one high, and one critical findings to ten moderate,
zero high, and zero critical findings. The remaining file-type/downloader
advisories require a breaking `hugo-bin` update (audit recommends 0.165.0),
which needs separate validation. CI now rejects high/critical tooling findings;
production Hugo stays at 0.152.2. No dependency install scripts run in CI.

The existing article publisher dispatches CI for bot PRs. A dispatched workflow
does not necessarily satisfy a required PR-event status check, as documented
in the earlier staleness investigation. This change does not bypass that rule
or introduce a PAT. A future bot publication must satisfy main's required
`gates`, plus any factual/human holds, before merging. No paid article generation
or publication is needed to validate this maintenance patch.

Shared maintenance-action adoption remains deferred until the coordinated pilot
provides a tested immutable commit SHA. Inventory should stay read-only by
default and preserve current providers, credentials, and repository gates.
