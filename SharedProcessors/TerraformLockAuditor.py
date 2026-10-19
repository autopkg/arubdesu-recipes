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
"""See docstring for TerraformLockAuditor class"""

from __future__ import absolute_import

import datetime
import json
import os
import re
import sys
from typing import Any, Dict, List, Tuple

# pyright: reportMissingImports=false
from autopkglib import (  # ty: ignore[unresolved-import]  # pylint: disable=import-error
    APLooseVersion,
    ProcessorError,
)
from autopkglib.URLGetter import (  # ty: ignore[unresolved-import]  # pylint: disable=import-error
    URLGetter,
)

__all__ = ["TerraformLockAuditor"]

LOCK_FILENAME = ".terraform.lock.hcl"
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
# The lock file is machine-generated with a fixed shape, so these two patterns read it
# without an HCL parser: a provider block, then the version line inside that block's body.
PROVIDER_BLOCK = re.compile(r'provider\s+"(?P<source>[^"]+)"\s*\{(?P<body>.*?)\n\}', re.S)
VERSION_LINE = re.compile(r'^\s*version\s*=\s*"([^"]+)"', re.M)
CONSTRAINTS_LINE = re.compile(r'^\s*constraints\s*=\s*"([^"]+)"', re.M)
PUBLIC_REGISTRY_HOST = "registry.terraform.io"
VERSIONS_ENDPOINT = "https://{host}/v1/providers/{namespace}/{name}"
DEFAULT_SEARCH_ROOTS = ("~/Documents",)
DEFAULT_MAX_DEPTH = 3
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


def major_line_prefix(pinned: str) -> str:
    """The version prefix a bump may move within, as a string match rather than parsed semver.

    Nothing here parses semver. It prefix-matches the registry's own version strings, which is
    weaker and worth being explicit about, because two conventions get different answers:
    a CalVer provider reports a new line every year, and a version with no dot at all yields ''
    and so can never be `major_behind`.

    The 0.x case is the one that mattered. Semver reserves breaking changes for the major
    *except* below 1.0, where the minor carries them, so a `0.x` pin's line is `0.x.` - taking
    everything before the first dot made 0.9.0 -> 0.10.0 a routine bump when it is entitled to
    break everything. Below 0.1 even that window is gone: a 0.0.x pin has no line at all.
    """
    parts = pinned.split(".")
    if len(parts) < 2 or not parts[0].isdigit():
        return ""
    if parts[0] == "0":
        if parts[1] == "0":
            # 0.0.x promises nothing between releases, so there is no line to move within:
            # a prefix no other version shares makes every newer release major_behind.
            return pinned + "."
        return "0.{}.".format(parts[1])
    return parts[0] + "."


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


class TerraformLockAuditor(URLGetter):
    """Audits every `.terraform.lock.hcl` under one or more roots against the provider
    registry, writing a JSON report an offline consumer can read later.

    Nothing in Terraform tells you a locked provider is behind. `terraform init -upgrade`
    moves the lock without reporting what moved or why, and a lock file is per-repo, so no
    single repo can see that five majors of the same provider are pinned across a fleet.
    That fleet-wide spread is the signal this exists to surface.

    Where the pin sits gets three distinct answers rather than one, because they call for
    different work:

    * `stale` - a newer version exists inside the pinned release line, so the move is one the
      existing constraint already permits. The actionable one.
    * `major_behind` - the pin is already the newest in its line, but a newer line exists.
      That is a migration, not a bump, so conflating it with `stale` would make a report
      nobody can act on. See `major_line_prefix` for what counts as a line, and note the
      deliberate 0.x rule: below 1.0 the minor carries breaking changes.
    * `ahead` - pinned past anything the registry publishes. A fork, a yanked release, or a
      version republished only privately. It exists because falling through to `major_behind`
      made a downstream consumer render "6.5.0 -> 6.4.0, a major upgrade".
    * `current` - the pin is the newest stable version published.

    Four statuses cover pins that cannot be compared at all, and they are kept apart because
    only the first is an absence of information rather than a defect in the input:
    `unchecked_host` (a private registry - Terraform Enterprise, an internal mirror - which
    the public registry 404s for), `ignored` (named in `ignore_providers`), `malformed_source`
    (a source address that is not host/namespace/name), and `unparsable_pin` (a provider block
    with no `version =` line). A single `unchecked_host` label for all four made a typo
    indistinguishable from a deliberate exclusion."""

    description = __doc__
    input_variables = {
        "lock_search_roots": {
            "required": False,
            "description": (
                "Array, or comma-separated string, of directories to "
                "walk for {}. Defaults to ['~/Documents'].".format(LOCK_FILENAME)
            ),
        },
        "lock_paths": {
            "required": False,
            "description": (
                "Array or comma-separated string of explicit lock file "
                "paths. When set, no walking happens and "
                "lock_search_roots is ignored."
            ),
        },
        "max_depth": {
            "required": False,
            "description": "Directory levels below each root to walk. Defaults to 3.",
            "default": DEFAULT_MAX_DEPTH,
        },
        "ignore_providers": {
            "required": False,
            "description": (
                "Array or comma-separated string of full provider sources "
                "to record but never look up, e.g. "
                "'terraform.example.com/acme/internal'."
            ),
        },
        "registry_host": {
            "required": False,
            "description": (
                "Host queried for providers whose source names it. Defaults "
                "to registry.terraform.io."
            ),
            "default": PUBLIC_REGISTRY_HOST,
        },
        "report_path": {
            "required": False,
            "description": (
                "Where the JSON report is written. Defaults to "
                "%RECIPE_CACHE_DIR%/terraform-lock-report.json."
            ),
        },
        "fail_on_stale": {
            "required": False,
            "description": (
                "Raise when any pin is stale within its major line. "
                "major_behind alone never raises. Defaults to false."
            ),
            "default": False,
        },
        "include_prereleases": {
            "required": False,
            "description": "Treat hyphenated prerelease versions as candidates. False.",
            "default": False,
        },
    }
    output_variables = {
        "terraform_report_path": {"description": "Path the JSON report was written to."},
        "terraform_locks_scanned": {"description": "Count of lock files parsed."},
        "terraform_providers_checked": {"description": "Count of distinct providers resolved."},
        "terraform_pins_total": {"description": "Count of provider pins across all locks."},
        "terraform_stale_pins": {"description": "Count of pins behind within their major line."},
        "terraform_major_behind_pins": {"description": "Count of pins whose major line is old."},
        "terraform_ahead_pins": {
            "description": "Count of pins newer than anything the registry publishes.",
        },
        "terraform_lookup_errors": {"description": "Count of providers whose lookup failed."},
        "terraform_summary": {"description": "One-line human summary, for a light or a log."},
    }

    def main(self) -> None:
        started = datetime.datetime.now(datetime.timezone.utc)
        self.output("{} start: terraform lock audit".format(started.isoformat()))
        lock_paths = self.locate_locks()
        if not lock_paths:
            raise NothingToAuditError(
                "No {} found. Set lock_paths, or widen lock_search_roots/max_depth.".format(
                    LOCK_FILENAME
                )
            )

        locks, provider_sources = self.parse_locks(lock_paths)
        providers = self.resolve_providers(provider_sources)
        totals = self.classify(locks, providers)
        self.record_spread(locks, providers)
        report_path = self.write_report(started, lock_paths, locks, providers, totals)

        summary = (
            "{} stale, {} a major behind, of {} pins across {} locks "
            "({} providers, {} lookup errors)".format(
                totals["stale"],
                totals["major_behind"],
                totals["pins"],
                totals["locks"],
                totals["providers"],
                totals["errors"],
            )
        )
        self.env["terraform_report_path"] = report_path
        self.env["terraform_locks_scanned"] = totals["locks"]
        self.env["terraform_providers_checked"] = totals["providers"]
        self.env["terraform_pins_total"] = totals["pins"]
        self.env["terraform_stale_pins"] = totals["stale"]
        self.env["terraform_major_behind_pins"] = totals["major_behind"]
        self.env["terraform_ahead_pins"] = totals["ahead"]
        self.env["terraform_lookup_errors"] = totals["errors"]
        self.env["terraform_summary"] = summary
        elapsed = datetime.datetime.now(datetime.timezone.utc) - started
        self.output("{} in {:.1f}s -> {}".format(summary, elapsed.total_seconds(), report_path))

        if totals["stale"] and truthy(self.env.get("fail_on_stale")):
            raise StalePinsError(summary)

    def locate_locks(self) -> List[str]:
        """Explicit lock_paths wins; otherwise a depth-limited walk of each root."""
        explicit = as_list(self.env.get("lock_paths"))
        if explicit:
            explicit_paths = [os.path.expanduser(path) for path in explicit]
            missing = [path for path in explicit_paths if not os.path.isfile(path)]
            if missing:
                raise NothingToAuditError(
                    "lock_paths entries do not exist: {}".format(", ".join(missing))
                )
            self.output("{} explicit lock path(s)".format(len(explicit_paths)))
            return explicit_paths

        roots = as_list(self.env.get("lock_search_roots")) or list(DEFAULT_SEARCH_ROOTS)
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
            found.extend(self.walk_for_filename(expanded, LOCK_FILENAME, max_depth))
            self.output("{}: {} lock file(s)".format(expanded, len(found) - before))
        return sorted(found)

    def walk_for_filename(self, root: str, filename: str, max_depth: int) -> List[str]:
        """Depth-limited os.walk, pruning caches and vendored trees in place."""
        # normpath before counting separators, not rstrip while counting the unstripped root:
        # `-k lock_search_roots=~/Documents/GitHub/` made every depth read one too deep, so
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

    def parse_locks(self, lock_paths: List[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Returns (per-lock pin lists, distinct provider sources worth looking up)."""
        locks: List[Dict[str, Any]] = []
        provider_sources: List[str] = []
        for path in lock_paths:
            pins, parse_error = self.pins_from_lock(path)
            locks.append({"path": path, "parse_error": parse_error, "pins": pins})
            for pin in pins:
                source = pin["source"]
                if source not in provider_sources:
                    provider_sources.append(source)
        self.output(
            "{} lock(s) parsed, {} distinct provider(s)".format(len(locks), len(provider_sources))
        )
        return locks, provider_sources

    def pins_from_lock(self, path: str) -> Tuple[List[Dict[str, Any]], str]:
        """One lock file's provider blocks: source, pinned version, declared constraints."""
        try:
            with open(path) as lock_file:
                contents = lock_file.read()
        except OSError as read_error:
            self.output("WARNING: {} unreadable: {}".format(path, read_error))
            return [], str(read_error)

        pins: List[Dict[str, Any]] = []
        for block in PROVIDER_BLOCK.finditer(contents):
            body = block.group("body")
            version_match = VERSION_LINE.search(body)
            constraints_match = CONSTRAINTS_LINE.search(body)
            source = block.group("source")
            host = source.split("/", 1)[0]
            pins.append(
                {
                    "source": source,
                    "host": host,
                    "version": version_match.group(1) if version_match else "",
                    "constraints": constraints_match.group(1) if constraints_match else "",
                    "status": "pending"
                    if host == str(self.env.get("registry_host") or PUBLIC_REGISTRY_HOST)
                    else "unchecked_host",
                }
            )
        if not pins:
            # A lock file with no provider blocks is legal - a stack using only modules writes
            # one - so this is reported rather than called a parse failure. Claiming the file
            # is broken when it is merely empty is the kind of wrong row that costs the whole
            # report its credibility.
            self.output("{} declares no providers".format(path))
        return pins, ""

    def resolve_providers(self, provider_sources: List[str]) -> Dict[str, Dict[str, Any]]:
        """One registry call per distinct provider, whatever the number of lock files."""
        ignored = set(as_list(self.env.get("ignore_providers")))
        registry_host = str(self.env.get("registry_host") or PUBLIC_REGISTRY_HOST)
        providers: Dict[str, Dict[str, Any]] = {}
        lookup_started = datetime.datetime.now(datetime.timezone.utc)
        api_calls = 0
        for index, source in enumerate(provider_sources, start=1):
            parts = source.split("/")
            skip_reason = self.skip_reason(source, parts, ignored, registry_host)
            if skip_reason:
                providers[source] = {
                    "versions": [],
                    "latest_stable": "",
                    "error": "",
                    "skipped": True,
                    "skip_reason": skip_reason,
                }
                continue
            try:
                payload = self.fetch_versions(registry_host, parts[1], parts[2])
                api_calls += 1
                versions = [str(item) for item in payload.get("versions") or []]
                providers[source] = {
                    "versions": versions,
                    "latest_stable": self.highest_stable(versions),
                    "upstream_source": str(payload.get("source") or ""),
                    "error": "",
                    "skipped": False,
                }
            except ProcessorError as lookup_error:
                self.output("WARNING: {} lookup failed: {}".format(source, lookup_error))
                providers[source] = {
                    "versions": [],
                    "latest_stable": "",
                    "error": str(lookup_error),
                    "skipped": False,
                }
            if index % 5 == 0 or index == len(provider_sources):
                elapsed = datetime.datetime.now(datetime.timezone.utc) - lookup_started
                self.output(
                    "  resolved {}/{} providers, {} registry calls, {:.1f}s".format(
                        index, len(provider_sources), api_calls, elapsed.total_seconds()
                    )
                )
        return providers

    def skip_reason(self, source: str, parts: List[str], ignored: Any, registry_host: str) -> str:
        """Why this provider gets no registry call, as a distinct cause rather than a catch-all.

        All three used to collapse into `unchecked_host`, so a deliberate exclusion, a typo'd
        source address and a genuine private registry were the same row in the report."""
        if source in ignored:
            return "ignored"
        if len(parts) != 3:
            return "malformed_source"
        if parts[0] != registry_host:
            return "unchecked_host"
        return ""

    def fetch_versions(self, host: str, namespace: str, name: str) -> Dict[str, Any]:
        url = VERSIONS_ENDPOINT.format(host=host, namespace=namespace, name=name)
        try:
            body = self.download(url, text=True)
        except ProcessorError as download_error:
            raise ProcessorError("{}: {}".format(url, download_error)) from download_error
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as decode_error:
            raise ProcessorError("non-JSON from {}: {}".format(url, decode_error)) from decode_error
        if not isinstance(payload, dict):
            raise ProcessorError("non-object from {}".format(url))
        # URLGetter's curl carries no --fail, so a 404 arrives as exit 0 with an error
        # object as the body; unchecked, a typo'd provider would read as "no versions".
        if payload.get("errors"):
            raise ProcessorError(
                "registry says {} for {}".format(
                    "; ".join(str(item) for item in payload["errors"]), url
                )
            )
        return payload

    def highest_stable(self, versions: List[str], major_prefix: str = "") -> str:
        """Highest version, optionally confined to one major line. '' when none qualify."""
        include_prereleases = truthy(self.env.get("include_prereleases"))
        ranked: List[Tuple[Any, str]] = []
        for candidate in versions:
            if "-" in candidate and not include_prereleases:
                continue
            if major_prefix and not candidate.startswith(major_prefix):
                continue
            ranked.append((APLooseVersion(candidate), candidate))
        if not ranked:
            return ""
        ranked.sort(key=lambda item: item[0])
        return ranked[-1][1]

    def classify(
        self, locks: List[Dict[str, Any]], providers: Dict[str, Dict[str, Any]]
    ) -> Dict[str, int]:
        """Assigns a status per pin, and tallies the lot."""
        totals = {
            "locks": len(locks),
            "pins": 0,
            "providers": len(providers),
            "current": 0,
            "stale": 0,
            "major_behind": 0,
            "ahead": 0,
            "errors": 0,
            "skipped": 0,
        }
        for lock in locks:
            for pin in lock["pins"]:
                totals["pins"] += 1
                provider = providers.get(pin["source"], {})
                latest_stable = provider.get("latest_stable", "")
                pinned = pin["version"]
                latest_in_major = self.highest_stable(
                    provider.get("versions") or [], major_line_prefix(pinned)
                )
                pin["latest_stable"] = latest_stable
                pin["latest_in_major"] = latest_in_major
                if pin["status"] != "pending":
                    totals["skipped"] += 1
                    continue
                if provider.get("error"):
                    pin["status"] = "error"
                    pin["error"] = provider["error"]
                    totals["errors"] += 1
                    continue
                incomparable = self.incomparable_status(provider, pinned, latest_stable)
                if incomparable:
                    pin["status"] = incomparable
                    totals["skipped"] += 1
                    continue
                pin["status"] = self.compare_pin(pinned, latest_stable, latest_in_major)
                totals[pin["status"]] = totals.get(pin["status"], 0) + 1
        return totals

    def incomparable_status(self, provider: Dict[str, Any], pinned: str, latest_stable: str) -> str:
        """The reasons a pin cannot be compared, kept apart. '' means go ahead and compare."""
        if provider.get("skipped"):
            return str(provider.get("skip_reason") or "unchecked_host")
        if not pinned:
            return "unparsable_pin"
        if not latest_stable:
            # The registry answered and has published nothing stable, which is a fact about
            # upstream rather than about the host we asked.
            return "no_stable_release"
        return ""

    def compare_pin(self, pinned: str, latest_stable: str, latest_in_major: str) -> str:
        """`stale` only when the pinned release line has something newer to move to."""
        if pinned == latest_stable:
            return "current"
        if latest_in_major and APLooseVersion(pinned) < APLooseVersion(latest_in_major):
            return "stale"
        if APLooseVersion(pinned) > APLooseVersion(latest_stable):
            return "ahead"
        return "major_behind"

    def record_spread(
        self, locks: List[Dict[str, Any]], providers: Dict[str, Dict[str, Any]]
    ) -> None:
        """Per-provider pinned-version spread across locks - the fleet-wide signal.

        A single repo cannot see this, which is the whole reason the audit walks roots
        rather than one working directory."""
        for source, provider in providers.items():
            pinned_versions: List[str] = []
            for lock in locks:
                for pin in lock["pins"]:
                    if pin["source"] == source and pin["version"] not in pinned_versions:
                        pinned_versions.append(pin["version"])
            pinned_versions.sort(key=APLooseVersion)
            provider["pinned_versions"] = pinned_versions
            # Dict comprehension: distinct release lines in use, order-preserving via dict.
            # Same helper `classify` uses, so a 0.x provider's lines read 0.9/0.10 rather than
            # collapsing to a single "0" that hides the spread this function exists to report.
            provider["pinned_major_lines"] = list(
                {
                    major_line_prefix(version).rstrip(".") or "unknown": None
                    for version in pinned_versions
                }.keys()
            )
            provider["published_version_count"] = len(provider.get("versions") or [])
            # The full version list is registry inventory, not audit findings; drop it so
            # the report stays readable and stable between runs.
            provider.pop("versions", None)
            if len(provider["pinned_major_lines"]) > 1:
                self.output(
                    "SPREAD: {} pinned across majors {} ({})".format(
                        source,
                        ", ".join(provider["pinned_major_lines"]),
                        ", ".join(pinned_versions),
                    )
                )

    def write_report(
        self,
        started: datetime.datetime,
        lock_paths: List[str],
        locks: List[Dict[str, Any]],
        providers: Dict[str, Dict[str, Any]],
        totals: Dict[str, int],
    ) -> str:
        """The whole point of the file: consumers read this and touch no network."""
        report_path = str(self.env.get("report_path") or "")
        if not report_path:
            cache_dir = self.env.get("RECIPE_CACHE_DIR") or os.getcwd()
            report_path = os.path.join(str(cache_dir), "terraform-lock-report.json")
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
            "generator": "com.github.arubdesu.shared/TerraformLockAuditor",
            "registry_host": str(self.env.get("registry_host") or PUBLIC_REGISTRY_HOST),
            "roots": as_list(self.env.get("lock_search_roots")) or list(DEFAULT_SEARCH_ROOTS),
            "lock_count": len(lock_paths),
            "totals": totals,
            "providers": providers,
            "locks": locks,
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
    PROCESSOR = TerraformLockAuditor()
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
        print("TerraformLockAuditor: {}".format(argument_error), file=sys.stderr)
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
        print("TerraformLockAuditor: {}".format(processor_error), file=sys.stderr)
        # Exact type, not isinstance: each subclass names one fatal condition and none subclass
        # another, so anything unmatched is a ProcessorError this file did not classify.
        sys.exit(EXIT_CODES.get(type(processor_error), EXIT_UNCLASSIFIED))
