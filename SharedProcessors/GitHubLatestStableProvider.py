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
"""See docstring for GitHubLatestStableProvider class"""

from __future__ import absolute_import

import datetime
import re
from typing import Any, Dict, List, Tuple

import autopkglib.github
from autopkglib import APLooseVersion, Processor, ProcessorError

__all__ = ["GitHubLatestStableProvider"]

# A tag is a prerelease when GitHub says so, or when the tag itself carries a marker. The
# second half matters because projects that publish tags and never cut a Release have no
# `prerelease` flag for us to read - pre-commit/mirrors-mypy and shellcheck-py/shellcheck-py
# both 404 on /releases/latest, so their tags are the only signal available.
# A marker may follow a separator (v1.0.0-rc1) or a digit directly (isort tags 9.0.0b5), and
# both shapes must be caught: pycqa/isort published 9.0.0b5 with GitHub's prerelease flag set
# to false, so trusting the flag alone recommends a beta as the newest stable release.
# Two precision rules, both from false positives that dropped a *stable* tag and so reported a
# behind pin as current - a silent wrong answer, which is the worst kind here. Word markers
# carry a (?![a-z]) boundary or `-pre` matches `-prefix`; the single letters demand a following
# digit or `-a`/`-b` matches any variant suffix, which made `-alpine`, `-arm64`, `-amd64`,
# `-apple`, `-bookworm` and `-build2` all read as prereleases.
# Byte-identical with PreCommitPinAuditor.py by requirement; the
# check-duplicated-constants pre-commit hook fails the run if the two drift.
PRERELEASE_MARKER = re.compile(
    r"(?:[-_.]|\d)(?:alpha|beta|preview|pre|rc|dev|next)(?![a-z])"
    r"|(?:[-_.]|\d)[ab]\d+",
    re.I,
)
# First dotted-number run in a tag: v1.25.1 -> 1.25.1, v0.11.0.1 -> 0.11.0.1 (four parts, which
# shellcheck-py really does ship), 20260101 -> 20260101.
VERSION_IN_TAG = re.compile(r"(\d+(?:\.\d+)*)")
DEFAULT_PER_PAGE = 50
TRUE_STRINGS = ("true", "yes", "1", "on")


def truthy(value: Any, default: bool = False) -> bool:
    """Recipe plists hand us real bools; execute_shell and env vars hand us strings."""
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.strip().lower() in TRUE_STRINGS
    return bool(value)


class NoMatchingAssetError(ProcessorError):
    """Raised when the chosen release carries no asset matching asset_regex."""


class GitHubLatestStableProvider(Processor):
    """Resolves the latest *stable* version of a GitHub-hosted project, falling back from
    Releases to tags when the project publishes no Releases at all.

    Differs from the core GitHubReleasesInfoProvider in three ways that matter for
    developer tooling rather than for apps:

    1. `/tags` fallback. Several pre-commit hook repos ship tags and no Releases, so a
       Releases-only lookup 404s on exactly the pins most in need of checking.
    2. Highest version wins, not most recently published. A pin answers "is a newer
       version out", and a backport released after a newer line would otherwise win.
    3. Prereleases are excluded by tag shape as well as by GitHub's flag, since a tag-only
       project has no flag. `version_prefix` additionally holds a lookup to one release
       line, which is how you ask "newest 6.x" without being dragged to 7.

    Assets are optional: with no asset_regex this resolves a version and nothing else,
    which is the shape a pin audit needs. With asset_regex it also yields a download URL,
    which is the shape a download recipe needs."""

    description = __doc__
    input_variables = {
        "github_repo": {
            "required": True,
            "description": "GitHub owner and repo, e.g. 'astral-sh/ruff-pre-commit'.",
        },
        "asset_regex": {
            "required": False,
            "description": ("If set, %url% is the first release asset whose filename "
                            "matches this regex; no match is fatal. Omit for a "
                            "version-only lookup."),
        },
        "version_prefix": {
            "required": False,
            "description": ("Only consider versions starting with this string, e.g. '6.' "
                            "to stay on a major line. Compared after the tag prefix is "
                            "stripped."),
        },
        "tag_regex": {
            "required": False,
            "description": ("Only consider tags matching this regex, for repos that tag "
                            "more than one product, e.g. '^cli/v'."),
        },
        "include_prereleases": {
            "required": False,
            "description": "Consider prereleases too. Defaults to false.",
            "default": False,
        },
        "allow_tags_fallback": {
            "required": False,
            "description": ("Fall back to /tags when the repo publishes no Releases. "
                            "Defaults to true."),
            "default": True,
        },
        "releases_per_page": {
            "required": False,
            "description": "Releases or tags to fetch per page. Defaults to 50.",
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
            "description": ("Token file path. The AutoPkg GITHUB_TOKEN preference wins "
                            "over this."),
            "default": "~/.autopkg_gh_token",
        },
    }
    output_variables = {
        "version": {"description": "Version parsed from the winning tag, prefix stripped."},
        "tag": {"description": "The winning tag verbatim, which is what a pre-commit rev pins."},
        "url": {"description": "Download URL of the matched asset. Only set with asset_regex."},
        "asset_url": {
            "description": "API asset URL, for private repos. Only set with asset_regex.",
        },
        "asset_filename": {"description": "Matched asset's upstream filename."},
        "published_at": {"description": "Publish timestamp of the winning release, if known."},
        "latest_stable_source": {"description": "'releases' or 'tags' - which lookup answered."},
    }

    def main(self) -> None:
        started = datetime.datetime.now(datetime.timezone.utc)
        repo = str(self.env["github_repo"])
        self.output("{} start: latest stable for {}".format(started.isoformat(), repo))
        session = autopkglib.github.GitHubSession(
            self.env["CURL_PATH"],
            self.env.get("curl_opts"),
            self.env["GITHUB_URL"],
            self.env["GITHUB_TOKEN_PATH"],
        )
        candidates, source = self.gather_candidates(session, repo)
        winner, version = self.select_highest(candidates, repo)

        self.env["version"] = version
        self.env["tag"] = winner["tag"]
        self.env["published_at"] = winner["published_at"]
        self.env["latest_stable_source"] = source
        self.output("latest stable {} is {} (tag {}, via {})".format(
            repo, version, winner["tag"], source))

        asset_regex = self.env.get("asset_regex")
        if asset_regex:
            self.select_asset(winner, str(asset_regex), repo)
        else:
            for unset in ("url", "asset_url", "asset_filename"):
                self.env.pop(unset, None)
        elapsed = datetime.datetime.now(datetime.timezone.utc) - started
        self.output("done in {:.1f}s".format(elapsed.total_seconds()))

    def gather_candidates(
        self, session: Any, repo: str
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Releases first; tags only when the repo has published no Releases at all."""
        per_page = int(self.env.get("releases_per_page", DEFAULT_PER_PAGE))
        releases = self.fetch_json(session, "/repos/{}/releases".format(repo), per_page)
        candidates = [
            {
                "tag": str(item.get("tag_name") or ""),
                "flagged_prerelease": bool(item.get("prerelease")),
                "assets": item.get("assets") or [],
                "published_at": str(item.get("published_at") or ""),
            }
            for item in releases or []
            if not item.get("draft")
        ]
        if candidates:
            self.output("/releases returned {} non-draft candidate(s)".format(len(candidates)))
            return candidates, "releases"
        if not truthy(self.env.get("allow_tags_fallback"), True):
            raise ProcessorError(
                "{} publishes no Releases and allow_tags_fallback is off".format(repo))
        tags = self.fetch_json(session, "/repos/{}/tags".format(repo), per_page)
        candidates = [
            {
                "tag": str(item.get("name") or ""),
                "flagged_prerelease": False,
                "assets": [],
                "published_at": "",
            }
            for item in tags or []
        ]
        if not candidates:
            raise ProcessorError("{} has neither Releases nor tags".format(repo))
        self.output("/releases was empty; /tags returned {} candidate(s)".format(len(candidates)))
        return candidates, "tags"

    def fetch_json(self, session: Any, endpoint: str, per_page: int) -> Any:
        """404 and an empty list are the same answer here - nothing to consider - so a
        missing Releases feed returns [] rather than interrupting the run."""
        paged = "{}?per_page={}".format(endpoint, per_page)
        payload, status = session.call_api(paged)
        if status == 404:
            self.output("{} -> 404".format(paged))
            return []
        if status != 200:
            raise ProcessorError("GitHub returned {} for {}".format(status, paged))
        return payload if isinstance(payload, list) else []

    def select_highest(
        self, candidates: List[Dict[str, Any]], repo: str
    ) -> Tuple[Dict[str, Any], str]:
        """Highest version among the stable, prefix-matching candidates."""
        tag_regex = self.env.get("tag_regex")
        version_prefix = str(self.env.get("version_prefix") or "")
        include_prereleases = truthy(self.env.get("include_prereleases"))
        rejected: Dict[str, int] = {"tag_regex": 0, "prerelease": 0, "prefix": 0, "unparsable": 0}
        ranked: List[Tuple[Any, Dict[str, Any], str]] = []

        for candidate in candidates:
            tag = candidate["tag"]
            if tag_regex and not re.search(str(tag_regex), tag):
                rejected["tag_regex"] += 1
                continue
            if not include_prereleases and self.is_prerelease(candidate):
                rejected["prerelease"] += 1
                continue
            version = self.version_from_tag(tag)
            if not version:
                rejected["unparsable"] += 1
                continue
            if version_prefix and not version.startswith(version_prefix):
                rejected["prefix"] += 1
                continue
            ranked.append((APLooseVersion(version), candidate, version))

        # Generator inside join(): one "<count> <reason>" fragment per non-zero rejection
        rejection_note = ", ".join(
            "{} {}".format(count, why) for why, count in rejected.items() if count
        ) or "none"
        self.output("considered {}, rejected {}".format(len(ranked), rejection_note))
        if not ranked:
            raise ProcessorError(
                "No stable version of {} survived filtering (prereleases {}, prefix '{}', "
                "tag_regex '{}')".format(
                    repo, "included" if include_prereleases else "excluded",
                    version_prefix, tag_regex or ""))
        # Sort on (version, tag): the tag is the tie-break so two tags carrying the same
        # version - shellcheck-py ships both v0.11.0.1 and a rebuild tagged v0.11.0.1-1 -
        # resolve the same way on every run instead of by API list order.
        ranked.sort(key=lambda item: (item[0], item[1]["tag"]))
        _, winner, version = ranked[-1]
        tied = [item[1]["tag"] for item in ranked if item[2] == version]
        if len(tied) > 1:
            self.output(
                "WARNING: {} tags carry version {} ({}); took {}. If the wrong one is "
                "wanted, pin it with tag_regex.".format(
                    len(tied), version, ", ".join(tied), winner["tag"]))
        return winner, version

    def is_prerelease(self, candidate: Dict[str, Any]) -> bool:
        if candidate["flagged_prerelease"]:
            return True
        return bool(PRERELEASE_MARKER.search(candidate["tag"]))

    def version_from_tag(self, tag: str) -> str:
        match = VERSION_IN_TAG.search(tag)
        return match.group(1) if match else ""

    def select_asset(self, winner: Dict[str, Any], asset_regex: str, repo: str) -> None:
        """Sets %url% from the first asset matching asset_regex on the winning release."""
        assets = winner["assets"]
        if not assets:
            raise NoMatchingAssetError(
                "{} {} has no release assets, so asset_regex '{}' cannot match. Tag-only "
                "projects have nothing to download.".format(repo, winner["tag"], asset_regex))
        try:
            matched = [item for item in assets if re.search(asset_regex, str(item.get("name")))]
        except re.error as regex_error:
            raise ProcessorError(
                "Invalid asset_regex: {}".format(regex_error)) from regex_error
        if not matched:
            raise NoMatchingAssetError(
                "No asset of {} {} matched '{}'. Assets present: {}".format(
                    repo, winner["tag"], asset_regex,
                    ", ".join(str(item.get("name")) for item in assets)))
        chosen = matched[0]
        self.env["url"] = str(chosen.get("browser_download_url") or "")
        self.env["asset_url"] = str(chosen.get("url") or "")
        self.env["asset_filename"] = str(chosen.get("name") or "")
        self.output("matched asset {} -> {}".format(
            self.env["asset_filename"], self.env["url"]))


if __name__ == "__main__":
    PROCESSOR = GitHubLatestStableProvider()
    PROCESSOR.execute_shell()
