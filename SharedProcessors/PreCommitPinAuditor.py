#!/usr/local/autopkg/python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Allister Banks
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""See docstring for PreCommitPinAuditor class"""

from __future__ import absolute_import

import datetime
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

# pyright: reportMissingImports=false
import autopkglib.github  # ty: ignore[unresolved-import]  # pylint: disable=import-error
import yaml
from autopkglib import (  # ty: ignore[unresolved-import]  # pylint: disable=import-error
    APLooseVersion,
    Processor,
    ProcessorError,
)

__all__ = ["PreCommitPinAuditor"]

CONFIG_FILENAME = ".pre-commit-config.yaml"
# Directories never worth descending into: build output, caches, and vendored trees hold
# copies of configs that nobody commits, and .terraform/node_modules make the walk crawl.
PRUNED_DIRECTORY_NAMES = frozenset(
    (
        ".git",
        ".terraform",
        ".cache",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
        "node_modules",
        "site-packages",
        "__pycache__",
        ".venv",
        "venv",
        "build",
        "dist",
    )
)
GITHUB_REPO_URL = re.compile(r"^https?://(?:www\.)?github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")
# A rev that is a bare 40- or 7-to-40-char hex string is a frozen SHA, which pre-commit
# writes with `pre-commit autoupdate --freeze`. Comparing it to a tag is meaningless.
FROZEN_SHA = re.compile(r"^[0-9a-f]{7,40}$")
# A marker may follow a separator (v1.0.0-rc1) or a digit directly (isort tags 9.0.0b5), and
# both shapes must be caught: pycqa/isort published 9.0.0b5 with GitHub's prerelease flag set
# to false, so trusting the flag alone recommends a beta as the newest stable release.
# Two precision rules, both from false positives that dropped a *stable* tag and so reported a
# behind pin as current - a silent wrong answer, which is the worst kind here. Word markers
# carry a (?![a-z]) boundary or `-pre` matches `-prefix`; the single letters demand a following
# digit or `-a`/`-b` matches any variant suffix, which made `-alpine`, `-arm64`, `-amd64`,
# `-apple`, `-bookworm` and `-build2` all read as prereleases.
# Byte-identical with GitHubLatestStableProvider.py by requirement; the
# check-duplicated-constants pre-commit hook fails the run if the two drift.
PRERELEASE_MARKER = re.compile(
    r"(?:[-_.]|\d)(?:alpha|beta|preview|pre|rc|dev|next)(?![a-z])"
    r"|(?:[-_.]|\d)[ab]\d+",
    re.I,
)
VERSION_IN_TAG = re.compile(r"(\d+(?:\.\d+)*)")
DEFAULT_SEARCH_ROOTS = ("~/Documents",)
DEFAULT_MAX_DEPTH = 3
DEFAULT_PER_PAGE = 50
TRUE_STRINGS = ("true", "yes", "1", "on")
# The report holds nothing secret and exists to be read by other programs.
REPORT_MODE = 0o644
# Standalone exit codes, ascending in the order the control flow can first hit them. Upstream
# lookups never stop a run - each failure is recorded on its entry - so none has a code. Any
# other ProcessorError exits 10, which is what AutoPkg's own execute_shell uses.
EXIT_BAD_ARGUMENTS = 1
EXIT_NOTHING_TO_AUDIT = 2
EXIT_REPORT_NOT_WRITTEN = 3
EXIT_STALE_PINS = 4
EXIT_UNCLASSIFIED = 10


class BadArgumentsError(ProcessorError):
    """An input could not be parsed; nothing was read or written."""


class NothingToAuditError(ProcessorError):
    """No file to audit was found, or an explicitly named one does not exist."""


class ReportNotWrittenError(ProcessorError):
    """The audit ran, but its report could not be written."""


class StalePinsError(ProcessorError):
    """The report was written, and it holds stale pins while fail_on_stale is set."""


def as_list(value: Any) -> List[str]:
    """Accepts an array or a comma-separated string.

    AutoPkg substitutes %VARIABLES% only inside strings, so an override that wants to
    change a list of roots cannot hand one through Input as an array - and `autopkg run -k`
    can only pass strings at all. Taking both shapes is what makes these settable without
    editing the recipe."""
    if not value:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def truthy(value: Any, default: bool = False) -> bool:
    """Recipe plists hand us real bools; execute_shell and env vars hand us strings."""
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.strip().lower() in TRUE_STRINGS
    return bool(value)


def publish_report(report_path: str, report: Dict[str, Any]) -> None:
    """Replaces the report whole, or leaves the previous one exactly as it was.

    Consumers read the report on their own schedule, and a read that lands between a truncate
    and the last byte decodes nothing and drops every finding for that pass. Writing a temp
    file and renaming it over the destination means a reader sees the previous document or the
    new one, never part of either. Raises whatever failed, with the temp file already removed.
    """
    # Beside the destination, never in /tmp: os.replace is atomic only within one filesystem.
    # The pid makes the name unique per writer, because a fixed temp name is atomic only against
    # itself - two overlapping runs would share it, one truncating what the other was writing.
    temporary = "{}.tmp.{}".format(report_path, os.getpid())
    published = False
    try:
        with open(temporary, "w") as report_file:
            # The rename publishes this inode, so its mode becomes the report's mode, and open()
            # alone would leave that to the umask of whatever launched autopkg.
            os.fchmod(report_file.fileno(), REPORT_MODE)
            json.dump(report, report_file, indent=2, sort_keys=True)
            # Durable before visible: renaming first lets a crash leave the name pointing at
            # data that never reached the disk.
            report_file.flush()
            os.fsync(report_file.fileno())
        os.replace(temporary, report_path)
        published = True
    finally:
        if not published:
            try:
                os.unlink(temporary)
            except OSError:
                pass


class PreCommitPinAuditor(Processor):
    """Audits every `.pre-commit-config.yaml` under one or more roots against upstream,
    writing a JSON report that an offline consumer can read later.

    A pre-commit hook repo is third-party code that executes on every commit, pinned by a
    `rev:` string. Nothing in pre-commit tells you a pin is old; `autoupdate` moves pins
    without asking whether you wanted to move. This answers the narrower question - which
    pins are behind the newest stable upstream tag - across every repo at once, which is
    the part per-repo tooling cannot do.

    Two deliberate design choices:

    1. Each upstream repo is resolved once, not once per config. 22 configs sharing 9 hook
       repos is 9 API calls, which matters against an hourly rate limit.
    2. The network half and the judgement half are separated by a file. This writes the
       report; whoever consumes it - a traffic-light daemon, a dashboard - reads a local
       JSON and spends no token and no network of its own.

    Releases are consulted first and tags second, because several hook repos publish tags
    and no Releases at all: pre-commit/mirrors-mypy and shellcheck-py/shellcheck-py both
    404 on /releases/latest, and those are pins as much in need of checking as any other.

    Statuses assigned per pin: `current`, `stale`, `ahead` (pinned past anything published,
    normally a fork or a typo), `frozen_sha` (`autoupdate --freeze`, not comparable),
    `unchecked_host` (not github.com), `local` (an inline hook definition with no upstream),
    and `error` (the lookup itself failed)."""

    description = __doc__
    input_variables = {
        "config_search_roots": {
            "required": False,
            "description": (
                "Array, or comma-separated string, of directories to "
                "walk for {}. Defaults to ['~/Documents'].".format(CONFIG_FILENAME)
            ),
        },
        "config_paths": {
            "required": False,
            "description": (
                "Array or comma-separated string of explicit config "
                "paths. When set, no walking happens and "
                "config_search_roots is ignored."
            ),
        },
        "max_depth": {
            "required": False,
            "description": "Directory levels below each root to walk. Defaults to 3.",
            "default": DEFAULT_MAX_DEPTH,
        },
        "ignore_repos": {
            "required": False,
            "description": (
                "Array or comma-separated string of 'owner/repo' names to "
                "record but never look up, for internal mirrors or repos "
                "you knowingly hold back."
            ),
        },
        "report_path": {
            "required": False,
            "description": (
                "Where the JSON report is written. Defaults to "
                "%RECIPE_CACHE_DIR%/pre-commit-pin-report.json."
            ),
        },
        "fail_on_stale": {
            "required": False,
            "description": (
                "Raise when any pin is stale. Defaults to false, so a run "
                "reports without interrupting a recipe chain."
            ),
            "default": False,
        },
        "include_prereleases": {
            "required": False,
            "description": "Treat prerelease tags as candidates. Defaults to false.",
            "default": False,
        },
        "releases_per_page": {
            "required": False,
            "description": "Releases or tags fetched per repo. Defaults to 50.",
            "default": DEFAULT_PER_PAGE,
        },
        "CURL_PATH": {
            "required": False,
            "description": "Path to curl. Defaults to /usr/bin/curl.",
            "default": "/usr/bin/curl",
        },
        "curl_opts": {
            "required": False,
            "description": "Optional array of extra curl options.",
        },
        "GITHUB_URL": {
            "required": False,
            "description": "API base, for GitHub Enterprise.",
            "default": "https://api.github.com",
        },
        "GITHUB_TOKEN_PATH": {
            "required": False,
            "description": ("Token file path. The AutoPkg GITHUB_TOKEN preference wins over this."),
            "default": "~/.autopkg_gh_token",
        },
    }
    output_variables = {
        "precommit_report_path": {"description": "Path the JSON report was written to."},
        "precommit_configs_scanned": {"description": "Count of config files parsed."},
        "precommit_repos_checked": {"description": "Count of distinct upstream repos resolved."},
        "precommit_pins_total": {"description": "Count of pins found across all configs."},
        "precommit_stale_pins": {"description": "Count of pins behind the newest stable tag."},
        "precommit_lookup_errors": {"description": "Count of repos whose lookup failed."},
        "precommit_summary": {"description": "One-line human summary, for a light or a log."},
    }

    def main(self) -> None:
        started = datetime.datetime.now(datetime.timezone.utc)
        self.output("{} start: pre-commit pin audit".format(started.isoformat()))
        config_paths = self.locate_configs()
        if not config_paths:
            raise NothingToAuditError(
                "No {} found. Set config_paths, or widen config_search_roots/max_depth.".format(
                    CONFIG_FILENAME
                )
            )

        configs, repo_names = self.parse_configs(config_paths)
        upstream = self.resolve_upstream(repo_names)
        totals = self.classify(configs, upstream)
        report_path = self.write_report(started, config_paths, configs, upstream, totals)

        summary = "{} stale of {} pins across {} configs ({} repos, {} lookup errors)".format(
            totals["stale"], totals["pins"], totals["configs"], totals["repos"], totals["errors"]
        )
        self.env["precommit_report_path"] = report_path
        self.env["precommit_configs_scanned"] = totals["configs"]
        self.env["precommit_repos_checked"] = totals["repos"]
        self.env["precommit_pins_total"] = totals["pins"]
        self.env["precommit_stale_pins"] = totals["stale"]
        self.env["precommit_lookup_errors"] = totals["errors"]
        self.env["precommit_summary"] = summary
        elapsed = datetime.datetime.now(datetime.timezone.utc) - started
        self.output("{} in {:.1f}s -> {}".format(summary, elapsed.total_seconds(), report_path))

        if totals["stale"] and truthy(self.env.get("fail_on_stale")):
            raise StalePinsError(summary)

    def locate_configs(self) -> List[str]:
        """Explicit config_paths wins; otherwise a depth-limited walk of each root."""
        explicit = as_list(self.env.get("config_paths"))
        if explicit:
            explicit_paths = [os.path.expanduser(path) for path in explicit]
            missing = [path for path in explicit_paths if not os.path.isfile(path)]
            if missing:
                raise NothingToAuditError(
                    "config_paths entries do not exist: {}".format(", ".join(missing))
                )
            self.output("{} explicit config path(s)".format(len(explicit_paths)))
            return explicit_paths

        roots = as_list(self.env.get("config_search_roots")) or list(DEFAULT_SEARCH_ROOTS)
        try:
            max_depth = int(self.env.get("max_depth", DEFAULT_MAX_DEPTH))
        except (TypeError, ValueError) as cast_error:
            raise BadArgumentsError(
                "max_depth must be a whole number, got {!r}".format(self.env.get("max_depth"))
            ) from cast_error
        found: List[str] = []
        for root in roots:
            expanded = os.path.expanduser(str(root))
            if not os.path.isdir(expanded):
                self.output("WARNING: root {} is not a directory; skipped".format(expanded))
                continue
            before = len(found)
            found.extend(self.walk_for_filename(expanded, CONFIG_FILENAME, max_depth))
            self.output("{}: {} config(s)".format(expanded, len(found) - before))
        return sorted(found)

    def walk_for_filename(self, root: str, filename: str, max_depth: int) -> List[str]:
        """Depth-limited os.walk, pruning caches and vendored trees in place."""
        # normpath before counting separators, not rstrip while counting the unstripped root:
        # `-k config_search_roots=~/Documents/GitHub/` made every depth read one too deep, so
        # max_depth silently lost a level for anyone who typed a trailing slash.
        root = os.path.normpath(root)
        root_depth = root.count(os.sep)
        hits: List[str] = []
        directories_visited = 0
        for current, subdirectories, files in os.walk(root):
            directories_visited += 1
            depth = current.count(os.sep) - root_depth
            if depth >= max_depth:
                subdirectories[:] = []
            else:
                # Slice assignment prunes in place, which is what os.walk honors
                subdirectories[:] = [
                    name
                    for name in subdirectories
                    if name not in PRUNED_DIRECTORY_NAMES and not name.endswith(".app")
                ]
            if filename in files:
                hits.append(os.path.join(current, filename))
        self.output("walked {} directories under {}".format(directories_visited, root))
        return hits

    def parse_configs(self, config_paths: List[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Returns (per-config pin lists, distinct owner/repo names worth looking up)."""
        configs: List[Dict[str, Any]] = []
        repo_names: List[str] = []
        for path in config_paths:
            pins, parse_error = self.pins_from_config(path)
            configs.append({"path": path, "parse_error": parse_error, "pins": pins})
            for pin in pins:
                name = pin["github_repo"]
                if name and name not in repo_names:
                    repo_names.append(name)
        self.output(
            "{} config(s) parsed, {} distinct upstream repo(s)".format(
                len(configs), len(repo_names)
            )
        )
        return configs, repo_names

    def pins_from_config(self, path: str) -> Tuple[List[Dict[str, Any]], str]:
        """One config's `repo:`/`rev:` pairs, with the hook ids that ride on each."""
        try:
            with open(path, "rb") as config_file:
                document = yaml.safe_load(config_file)
        except (OSError, yaml.YAMLError) as parse_error:
            self.output("WARNING: {} did not parse: {}".format(path, parse_error))
            return [], str(parse_error)
        if not isinstance(document, dict):
            return [], "top level is not a mapping"

        pins: List[Dict[str, Any]] = []
        for entry in document.get("repos") or []:
            if not isinstance(entry, dict):
                continue
            repo_url = str(entry.get("repo") or "")
            revision = str(entry.get("rev") or "")
            hook_ids = [
                str(hook.get("id") or "")
                for hook in entry.get("hooks") or []
                if isinstance(hook, dict)
            ]
            owner_repo = self.github_repo_from_url(repo_url)
            pins.append(
                {
                    "repo": repo_url,
                    "rev": revision,
                    "hooks": hook_ids,
                    "github_repo": owner_repo or "",
                    "status": self.initial_status(repo_url, revision, owner_repo),
                }
            )
        return pins, ""

    def github_repo_from_url(self, repo_url: str) -> Optional[str]:
        match = GITHUB_REPO_URL.match(repo_url.strip())
        return "{}/{}".format(match.group(1), match.group(2)) if match else None

    def initial_status(self, repo_url: str, revision: str, owner_repo: Optional[str]) -> str:
        """Everything decidable without the network, so lookups only run where they help."""
        if repo_url in ("local", "meta"):
            return "local"
        if not owner_repo:
            return "unchecked_host"
        if FROZEN_SHA.match(revision):
            return "frozen_sha"
        return "pending"

    def resolve_upstream(self, repo_names: List[str]) -> Dict[str, Dict[str, Any]]:
        """One lookup per distinct repo: releases first, tags when there are no releases."""
        ignored = set(as_list(self.env.get("ignore_repos")))
        try:
            per_page = int(self.env.get("releases_per_page", DEFAULT_PER_PAGE))
        except (TypeError, ValueError) as cast_error:
            raise BadArgumentsError(
                "releases_per_page must be a whole number, got {!r}".format(
                    self.env.get("releases_per_page")
                )
            ) from cast_error
        session = autopkglib.github.GitHubSession(
            self.env["CURL_PATH"],
            self.env.get("curl_opts"),
            self.env["GITHUB_URL"],
            self.env["GITHUB_TOKEN_PATH"],
        )
        upstream: Dict[str, Dict[str, Any]] = {}
        api_calls = 0
        lookup_started = datetime.datetime.now(datetime.timezone.utc)
        for index, name in enumerate(repo_names, start=1):
            if name in ignored:
                upstream[name] = {
                    "latest_version": "",
                    "latest_tag": "",
                    "source": "ignored",
                    "error": "",
                }
                continue
            try:
                tags, source, calls = self.fetch_tag_list(session, name, per_page)
                api_calls += calls
                latest_tag, latest_version = self.highest_stable_tag(tags)
                upstream[name] = {
                    "latest_version": latest_version,
                    "latest_tag": latest_tag,
                    "source": source,
                    "error": "",
                }
            except ProcessorError as lookup_error:
                self.output("WARNING: {} lookup failed: {}".format(name, lookup_error))
                upstream[name] = {
                    "latest_version": "",
                    "latest_tag": "",
                    "source": "",
                    "error": str(lookup_error),
                }
            if index % 5 == 0 or index == len(repo_names):
                elapsed = datetime.datetime.now(datetime.timezone.utc) - lookup_started
                self.output(
                    "  resolved {}/{} repos, {} API calls, {:.1f}s".format(
                        index, len(repo_names), api_calls, elapsed.total_seconds()
                    )
                )
        return upstream

    def fetch_tag_list(
        self, session: Any, name: str, per_page: int
    ) -> Tuple[List[Dict[str, Any]], str, int]:
        """(candidate tags, which endpoint answered, API calls spent)."""
        releases, status = session.call_api("/repos/{}/releases?per_page={}".format(name, per_page))
        calls = 1
        if status not in (200, 404):
            raise ProcessorError("GitHub returned {} for {} releases".format(status, name))
        candidates = [
            {
                "tag": str(item.get("tag_name") or ""),
                "flagged_prerelease": bool(item.get("prerelease")),
            }
            for item in releases or []
            if isinstance(item, dict) and not item.get("draft")
        ]
        if candidates:
            return candidates, "releases", calls

        tags, status = session.call_api("/repos/{}/tags?per_page={}".format(name, per_page))
        calls += 1
        if status != 200:
            raise ProcessorError("GitHub returned {} for {} tags".format(status, name))
        candidates = [
            {"tag": str(item.get("name") or ""), "flagged_prerelease": False}
            for item in tags or []
            if isinstance(item, dict)
        ]
        if not candidates:
            raise ProcessorError("{} has neither releases nor tags".format(name))
        return candidates, "tags", calls

    def highest_stable_tag(self, candidates: List[Dict[str, Any]]) -> Tuple[str, str]:
        """(tag, version). Highest version wins, tag string breaks ties deterministically."""
        include_prereleases = truthy(self.env.get("include_prereleases"))
        ranked: List[Tuple[Any, str, str]] = []
        for candidate in candidates:
            tag = candidate["tag"]
            if not include_prereleases and (
                candidate["flagged_prerelease"] or PRERELEASE_MARKER.search(tag)
            ):
                continue
            match = VERSION_IN_TAG.search(tag)
            if not match:
                continue
            ranked.append((APLooseVersion(match.group(1)), tag, match.group(1)))
        if not ranked:
            raise ProcessorError("no stable tag among {} candidate(s)".format(len(candidates)))
        ranked.sort(key=lambda item: (item[0], item[1]))
        return ranked[-1][1], ranked[-1][2]

    def classify(
        self, configs: List[Dict[str, Any]], upstream: Dict[str, Dict[str, Any]]
    ) -> Dict[str, int]:
        """Turns each pending pin into current/stale/ahead/error, and tallies the lot."""
        # `repos` counts repos actually resolved, not every entry in the map: an ignore-list
        # entry costs no API call, and reporting it as checked overstates what the run did.
        resolved = [name for name, entry in upstream.items() if entry.get("source") != "ignored"]
        totals = {
            "configs": len(configs),
            "pins": 0,
            "repos": len(resolved),
            "stale": 0,
            "current": 0,
            "ahead": 0,
            "errors": 0,
            "skipped": 0,
        }
        for config in configs:
            for pin in config["pins"]:
                totals["pins"] += 1
                latest = upstream.get(pin["github_repo"], {})
                pin["latest_tag"] = latest.get("latest_tag", "")
                pin["latest_version"] = latest.get("latest_version", "")
                pin["lookup_source"] = latest.get("source", "")
                if pin["status"] != "pending":
                    totals["skipped"] += 1
                    continue
                if latest.get("error"):
                    pin["status"] = "error"
                    pin["error"] = latest["error"]
                    totals["errors"] += 1
                    continue
                if latest.get("source") == "ignored":
                    pin["status"] = "ignored"
                    totals["skipped"] += 1
                    continue
                pin["status"] = self.compare_pin(pin["rev"], pin["latest_tag"])
                totals[pin["status"]] = totals.get(pin["status"], 0) + 1
        return totals

    def compare_pin(self, revision: str, latest_tag: str) -> str:
        """Exact tag match is `current`; otherwise compare the versions inside the tags."""
        if revision == latest_tag:
            return "current"
        pinned_match = VERSION_IN_TAG.search(revision)
        latest_match = VERSION_IN_TAG.search(latest_tag)
        if not pinned_match or not latest_match:
            return "stale"
        pinned = APLooseVersion(pinned_match.group(1))
        newest = APLooseVersion(latest_match.group(1))
        if pinned < newest:
            return "stale"
        if pinned > newest:
            return "ahead"
        # Same version, different tag string: a rebuild tag like v0.11.0.1-1. Treat the
        # exact-match failure as stale so the rebuild is not silently skipped.
        return "stale"

    def write_report(
        self,
        started: datetime.datetime,
        config_paths: List[str],
        configs: List[Dict[str, Any]],
        upstream: Dict[str, Dict[str, Any]],
        totals: Dict[str, int],
    ) -> str:
        """The whole point of the file: consumers read this and touch no network."""
        report_path = str(self.env.get("report_path") or "")
        if not report_path:
            cache_dir = self.env.get("RECIPE_CACHE_DIR") or os.getcwd()
            report_path = os.path.join(str(cache_dir), "pre-commit-pin-report.json")
        report_path = os.path.expanduser(report_path)
        if "%" in report_path:
            raise ReportNotWrittenError(
                "report_path still holds an unsubstituted variable: {}. AutoPkg "
                "substitutes Input values before RECIPE_CACHE_DIR exists, so leave "
                "REPORT_PATH empty to get the cache directory.".format(report_path)
            )
        report = {
            "schema": 1,
            "generated_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generator": "com.github.arubdesu.shared/PreCommitPinAuditor",
            "roots": as_list(self.env.get("config_search_roots")) or list(DEFAULT_SEARCH_ROOTS),
            "config_count": len(config_paths),
            "totals": totals,
            "repos": upstream,
            "configs": configs,
        }
        try:
            parent = os.path.dirname(report_path)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent)
            publish_report(report_path, report)
        except (OSError, TypeError, ValueError) as write_error:
            # TypeError and ValueError are json.dump refusing the document, which leaves the
            # report exactly as unwritten as a full disk does.
            raise ReportNotWrittenError(
                "could not write {}: {}".format(report_path, write_error)
            ) from write_error
        return report_path


if __name__ == "__main__":
    PROCESSOR = PreCommitPinAuditor()
    # execute_shell() catches ProcessorError itself and exits 10, which would hide every code
    # above, so this runs the same steps it does and maps each error type to its own code.
    if {"-h", "--help"} & set(sys.argv[1:]) or (not sys.argv[1:] and sys.stdin.isatty()):
        PROCESSOR._print_help()  # pylint: disable=protected-access
        sys.exit(0)
    try:
        if sys.argv[1:]:
            PROCESSOR.parse_arguments()
        else:
            PROCESSOR.read_input_plist()
    except ProcessorError as argument_error:
        print("PreCommitPinAuditor: {}".format(argument_error), file=sys.stderr)
        sys.exit(EXIT_BAD_ARGUMENTS)
    EXIT_CODES = {
        BadArgumentsError: EXIT_BAD_ARGUMENTS,
        NothingToAuditError: EXIT_NOTHING_TO_AUDIT,
        ReportNotWrittenError: EXIT_REPORT_NOT_WRITTEN,
        StalePinsError: EXIT_STALE_PINS,
    }
    try:
        PROCESSOR.process()
        PROCESSOR.write_output_plist()
    except ProcessorError as processor_error:
        print("PreCommitPinAuditor: {}".format(processor_error), file=sys.stderr)
        # Exact type, not isinstance: each subclass names one fatal condition and none subclass
        # another, so anything unmatched is a ProcessorError this file did not classify.
        sys.exit(EXIT_CODES.get(type(processor_error), EXIT_UNCLASSIFIED))
