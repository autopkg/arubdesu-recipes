# arubdesu-recipes

Recipes for <http://autopkg.github.io/autopkg/>

## Map

### Shared processors

`SharedProcessors/` is exposed to recipes by the stub recipe `com.github.arubdesu.shared`, so any recipe in any repo can call one as `com.github.arubdesu.shared/<ProcessorName>`.

| Processor | Answers |
| --- | --- |
| `GitHubLatestStableProvider` | the newest stable release *or tag* of any GitHub project |
| `TerraformLockAuditor` | which provider pins across many repos are behind the Terraform registry |
| `PreCommitPinAuditor` | which hook `rev:` pins across many repos are behind upstream |

`GitHubLatestStableProvider` answers "what is the latest stable version" for one project. The two auditors answer "is what I already pin still current" across a whole directory of repos, and write a JSON report rather than downloading anything.

#### Shared helpers are copied, and a hook keeps the copies equal

`PreCommitPinAuditor` and `GitHubLatestStableProvider` both carry `PRERELEASE_MARKER` and `VERSION_IN_TAG`, all three processors carry their own `truthy`, and the two auditors share `as_list`, `publish_report`, their exit codes and their directory-walk settings. That duplication cannot be removed the obvious way. AutoPkg loads a processor with `importlib.util.spec_from_file_location` and **never puts the containing directory on `sys.path`** (`autopkglib/__init__.py`, `_load_processor_from_file`), so a shared processor that imports a sibling module raises `ImportError`, which AutoPkg turns into `AutoPackagerLoadError`. Copying is the only portable way to share a helper here.

That makes undetected drift the real risk, so `scripts/check_duplicated_constants.py` runs as a local pre-commit hook and fails when any module-level constant or function defined in more than one shared processor disagrees. It compares parsed syntax trees with docstrings stripped, so reformatting and per-file prose pass while a changed regex or a changed comparison does not.

#### Why not GitHubReleasesInfoProvider

AutoPkg's own `GitHubReleasesInfoProvider` is close, and its `sort_by_highest_tag_names` already makes the highest version win. Three things stop it working for pins, each verified against the live GitHub API and still true in AutoPkg 3.0.0:

1. **It cannot do a version-only lookup.** `select_asset()` skips any release that carries no assets, and hook repos publish source-only releases: `homebysix/pre-commit-macadmin`, `astral-sh/ruff-pre-commit` and `antonbabenko/pre-commit-terraform` have no assets on any release. Asked for one, the processor walks the entire release history and then raises `No releases found for repo ... on page 3`. A pin audit needs a version and no file, which is why `asset_regex` is optional here and `url` stays unset without it.
2. **It has no `/tags` fallback.** `pre-commit/mirrors-mypy`, `shellcheck-py/shellcheck-py` and `DavidAnson/markdownlint-cli2` publish tags and no Releases, and the core processor fails with `curl: (56) ... 404`. Those pins need checking as much as any other.
3. **It trusts GitHub's `prerelease` flag alone.** `pycqa/isort` published `9.0.0b5` with the flag set to *false*, and that release does carry assets, so the core processor with `sort_by_highest_tag_names` set selects the beta and reports `9.0.0b5`. Here, tag shape is checked alongside the flag, catching both `v1.0.0-rc1` and the separator-less `9.0.0b5` without mistaking variant suffixes such as `-alpine` or `-arm64` for prereleases.

### If these land upstream, delete code here

`GitHubLatestStableProvider` exists to work around the three gaps above, not to be a permanent fork of core behavior. The intent is to send them upstream and remove the local version if they are accepted:

| Local behavior | Core change that retires it |
| --- | --- |
| optional `asset_regex`, with `url` unset without it | make asset selection optional, so a release with no assets can still yield a version |
| `/tags` fallback | an `allow_tags_fallback` input on the core processor |
| tag-shape prerelease test | check tag shape alongside the `prerelease` flag |

If all three land, `GitHubLatestStableProvider` becomes a thin shim over the core processor and should be deleted. `PreCommitPinAuditor` carries its own copy of the same tag logic and would lose it too.

`TerraformLockAuditor` is not an upstream candidate: the registry protocol is Terraform-specific and belongs in a recipe repo, not in core. It asks the registry rather than GitHub because the registry is what `terraform init` consults; a GitHub tag can name a version the registry never published, which no `init` will install.

### The two audit recipes

`TerraformLockAudit.audit` and `PreCommitPinAudit.audit` download nothing and install nothing. Their output is a JSON report, written by default into each recipe's own AutoPkg cache directory.

The split exists so that whatever consumes the answer does not need the network or a token. These recipes use both, on whatever schedule you run them; whatever reads the report, such as a status light, a dashboard or a pre-commit hook, reads a local file and uses neither.

Both search `~/Documents/GitHub` by default, which is where GitHub Desktop clones repositories. To search elsewhere, set `LOCK_SEARCH_ROOTS` or `CONFIG_SEARCH_ROOTS` in an override.

Control flow, the same for both:

1. Walk each root in `LOCK_SEARCH_ROOTS` / `CONFIG_SEARCH_ROOTS` to `MAX_DEPTH` levels, skipping `.git`, `node_modules`, `.terraform`, caches and vendored trees. Both accept a comma-separated string rather than an array, because AutoPkg substitutes `%VARIABLES%` only inside strings: an array in `Input` could not be overridden, and `autopkg run -k` could not set one at all.
2. Parse each file. The lock file is machine-generated, so two regexes read it without an HCL parser; configs go through PyYAML, which AutoPkg's Python ships.
3. Resolve each distinct upstream **once**, however many files pin it. 22 configs sharing 9 hook repos is 9 lookups, which is what keeps a fleet-wide audit inside an hourly rate limit.
4. Classify every pin, write the report, and set output variables including a one-line summary.

Statuses are deliberately more than two, because the remediation differs per status and a report that collapses them is one nobody can act on.

| Status | Terraform | Pre-commit | What closes it |
| --- | --- | --- | --- |
| `current` | yes | yes | nothing |
| `stale` | yes | yes | a bump inside the line the constraint already permits |
| `major_behind` | yes | — | a migration: edit the constraint, read a changelog |
| `ahead` | yes | yes | usually nothing; a fork, a yank, or a typo to check |
| `frozen_sha` | — | yes | `autoupdate --freeze`, since a SHA cannot be version-compared |
| `local` | — | yes | nothing; an inline hook has no upstream |
| `unchecked_host` | yes | yes | nothing; a private registry publishes no public latest |
| `ignored` | yes | yes | nothing; you named it in `IGNORE_*` |
| `malformed_source` | yes | — | fix the source address in the lock |
| `unparsable_pin` | yes | — | fix the provider block; it has no `version =` line |
| `no_stable_release` | yes | — | nothing yet; upstream has published only prereleases |
| `error` | yes | yes | retry, or read the recorded lookup failure |

**What counts as a "line" is a string prefix, not parsed semver**, and two conventions get different answers. A CalVer provider reports a new line every calendar year. A version with no dot in it has no line at all and so can never be `major_behind`. `0.x` needs special handling: semver reserves breaking changes for the major *except* below 1.0, where the minor carries them, so `major_line_prefix` treats a `0.x` pin's line as `0.x`, and `0.9.0 → 0.10.0` is `major_behind` rather than a routine bump. A `0.0.x` pin has no line at all, because `0.0.x` promises nothing between releases, so every newer release is `major_behind`.

The four statuses that mean "cannot be compared" are kept apart on purpose. Only `unchecked_host` is an absence of information about a real pin; `malformed_source` and `unparsable_pin` are defects in the input, and `no_stable_release` is a fact about upstream.

`FAIL_ON_STALE` defaults to false so an audit reports without interrupting a chain. `major_behind` never raises, even when it is true.

Leaving `REPORT_PATH` empty means the recipe's own cache directory. Do not put the cache-dir variable in `Input`: `Input` values are substituted before that key exists, so it would resolve to a literal directory of that name. The processors reject a `report_path` still containing a `%` for exactly that reason.

The report is replaced whole or not at all. Each auditor writes a temporary file beside the destination and renames it over the old report, so a program reading it on its own schedule sees the previous report or the new one, never part of either. The file is mode `0644`, because it holds nothing secret and exists to be read by other programs.

### Pre-commit hook `.version` recipes

`PreCommit/` also holds five `.version` recipes. Each resolves one hook repo's newest stable tag with `GitHubLatestStableProvider` and stops: no download, no package, no report. The output is `%tag%`, which is exactly what a `rev:` pins, plus `%version%` with the prefix removed.

| Recipe | Repo | Resolved through |
| --- | --- | --- |
| `RuffPreCommit.version` | `astral-sh/ruff-pre-commit` | Releases |
| `PreCommitHooks.version` | `pre-commit/pre-commit-hooks` | Releases |
| `MirrorsMypy.version` | `pre-commit/mirrors-mypy` | tags |
| `ShellcheckPy.version` | `shellcheck-py/shellcheck-py` | tags |
| `BlackPreCommitMirror.version` | `psf/black-pre-commit-mirror` | tags |

Three of the five publish **no Releases at all**, which is why the tags fallback exists and why `GitHubReleasesInfoProvider` cannot answer for them. Two carry tag shapes worth testing against: `shellcheck-py` publishes rebuild tags such as `v0.11.0.1-1`, whose `-1` suffix must not read as a prerelease, with a version four components deep; `black-pre-commit-mirror` tags without a leading `v`, such as `26.5.1`.

Use these to check one dependency. Use `PreCommitPinAudit.audit` to check every pin under your roots at once.

### Error handling and exit codes

Inside an AutoPkg run, every processor here signals failure the way AutoPkg understands: `ProcessorError`, which stops the run at the processor that raised it. No processor here sets an exit code during a normal `autopkg run`, and there are no Slack notifications; the run's own output is the record.

Exit codes exist only for the two auditors, which are the only processors worth running standalone. They are numbered in the order the control flow can first encounter them, and are identical between `PreCommitPinAuditor` and `TerraformLockAuditor`:

| Code | Condition |
| --- | --- |
| 1 | an argument could not be parsed: not `key=value`, or a count that is not a whole number |
| 2 | nothing to audit: no config or lock file under the roots, or an explicitly named one does not exist |
| 3 | the report could not be written, including a `report_path` that still holds a `%VARIABLE%` |
| 4 | stale pins found while `FAIL_ON_STALE` is set; the report is written first |

Any other `ProcessorError` exits 10, AutoPkg's own code for a failed standalone processor. Upstream lookups have no code because they never stop a run. Each auditor's `__main__` runs `execute_shell`'s steps itself rather than calling it, because `execute_shell` catches `ProcessorError` and exits 10 before any mapping could see the error.

To run a processor standalone, pass inputs as `key=value` arguments. `execute_shell` does not read environment variables, so `github_repo` set in the environment does nothing and the run fails with `requires github_repo`. AutoPkg's Python needs `PYTHONPATH` to find `autopkglib`:

```sh
PYTHONPATH=/Library/AutoPkg /usr/local/autopkg/python SharedProcessors/GitHubLatestStableProvider.py github_repo=pre-commit/mirrors-mypy
```

It prints the output plist to stdout. `GitHubLatestStableProvider` uses AutoPkg's `execute_shell` unchanged, so it exits 0 on success and 10 on any `ProcessorError`. Progress messages from `self.output()` print only at verbosity 1 or above; pass `verbose=1` to see them, or read the output plist.

Conditions that deliberately do **not** stop a run:

- One repo or provider whose lookup fails is recorded as `error` on that entry; the audit finishes and reports the rest. A single rate-limited call should not discard 100 good answers.
- A provider on a private registry host is `unchecked_host`. The public registry has no opinion about it, and an absence of information is not a stale pin. A malformed source address, a version-less provider block and a provider with no stable release ever published each get their own status, and none of them stop the run either.
- A pin `ahead` of everything published does not stop the run. It is normally a fork or a republish, and it is reported rather than treated as failure.
- `major_behind` never stops the run, even under `FAIL_ON_STALE`. A major upgrade is a migration to schedule, not a failure to stop for.
- A config that will not parse is recorded with its parse error and skipped.

`URLGetter`'s curl carries no `--fail`, so an HTTP 404 arrives as a successful call whose body is an error object. `TerraformLockAuditor` checks for a registry `errors` key explicitly; without that, a missing provider reads as a successful response that merely lists no versions.

`GitHubSession` can report a status of `None`. `_call_api_once` records a status only when curl reported one other than `000`, so a network-level failure gives `None` rather than a number. Both GitHub-backed processors treat anything but `200`, or `404` where an empty answer is valid, as a failure, so `None` is handled. `call_api` also retries a failed GET without the token after a `401`, which keeps a run going on an expired token but drops it to GitHub's limit of 60 unauthenticated requests per hour; the only sign is a warning in the run's output.

## FAQ

### Why does Microsoft have an update available, but your recipe (doesn't see it yet|fails)?

Sorry, but Microsoft is often faster to provide direct download links than to fix or update the products feed(s). So far, we've had

1. improperly signed packages,
2. no signature at all,
3. broken xml in the feed,
4. xml that points to a valid URL... which 404's,
5. days which turn into weeks which turn into months where they don't update the feed at all.

We usually just have to wait. You can help confirm this symptom by doing the following:

1. Visiting the update feed URL (it's in the URLProvider processor, e.g. [for Lync](http://www.microsoft.com/mac/autoupdate/0409UCCP14.xml))
2. If it's still showing the old URL, we just have to wait, but if you're going above and beyond you can continue:
3. Download the new version and install (or unpack the payload from the pkg if you're savvy)
4. Run 'Check for Updates' from the Help menu while doing a packet capture with a tool like [CharlesProxy](http://www.charlesproxy.com)
5. If there's a new update feed, then yes, my recipe is indeed behind the times and needs updating.
   - If not... let's enjoy the wait together!
