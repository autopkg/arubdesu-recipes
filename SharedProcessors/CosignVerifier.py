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
"""See docstring for CosignVerifier class"""

from __future__ import absolute_import

import hashlib
import os
import re
import shutil
import subprocess

from autopkglib import ProcessorError
from autopkglib.URLGetter import URLGetter

__all__ = ["CosignVerifier"]

# Locations to search when cosign isn't on AutoPkg's PATH (no brew assumed)
COSIGN_FALLBACK_PATHS = [
    "/usr/local/bin/cosign",
    "/opt/homebrew/bin/cosign",
]
DEFAULT_ALGORITHM = "SHA256"


class CosignVerifier(URLGetter):
    """Verifies a downloaded artifact, preferring cosign over a checksums manifest.

    If cosign is available it runs `cosign verify-blob` against the artifact's
    Sigstore bundle (.sigstore.json): keyless verification that cryptographically
    confirms the artifact was signed by a specific OIDC identity (e.g. a project's
    GitHub Actions release workflow), tied to a Fulcio cert and the Rekor log. An
    attacker who controls the release page can't forge that, so it beats a checksum.

    If cosign is NOT installed, behavior depends on require_cosign (default true):
    true raises with install guidance; false falls back to verifying the artifact
    against a checksums manifest (integrity only, authenticity resting on the HTTPS
    host) when checksums_url and asset_filename are provided. The downgrade is
    logged loudly so a missing cosign never silently weakens verification."""

    description = __doc__
    input_variables = {
        "artifact_path": {
            "required": False,
            "description": ("Path to the file to verify. Defaults to the "
                            "%pathname% set by URLDownloader."),
        },
        "bundle_path": {
            "required": False,
            "description": ("Path to an already-downloaded .sigstore.json bundle. "
                            "If absent, the bundle is fetched from bundle_url."),
        },
        "bundle_url": {
            "required": False,
            "description": ("URL of the .sigstore.json bundle to fetch. If neither "
                            "bundle_path nor bundle_url is set, defaults to the "
                            "artifact's %url% with '.sigstore.json' appended."),
        },
        "certificate_identity": {
            "required": False,
            "description": ("Exact signing identity (SAN) to require. Set this or "
                            "certificate_identity_regexp for cosign verification."),
        },
        "certificate_identity_regexp": {
            "required": False,
            "description": ("Go regexp the signing identity must match. Set this or "
                            "certificate_identity for cosign verification."),
        },
        "certificate_oidc_issuer": {
            "required": False,
            "description": ("OIDC issuer expected in the Fulcio cert, e.g. "
                            "https://token.actions.githubusercontent.com. Required "
                            "when cosign verification runs."),
        },
        "cosign_path": {
            "required": False,
            "description": "Path to the cosign binary. Defaults to searching PATH.",
        },
        "extra_cosign_args": {
            "required": False,
            "description": ("Optional array of extra args passed to verify-blob, "
                            "e.g. ['--offline']."),
        },
        "require_cosign": {
            "required": False,
            "description": ("If true (default), a missing cosign is fatal. If false, "
                            "fall back to checksum verification when cosign is absent."),
            "default": True,
        },
        "checksums_url": {
            "required": False,
            "description": "URL of a '<hash>  <filename>' manifest for the fallback.",
        },
        "asset_filename": {
            "required": False,
            "description": ("Original release asset name to look up in the manifest "
                            "for the fallback (not any renamed local copy)."),
        },
        "algorithm": {
            "required": False,
            "description": "Fallback hash algorithm. Defaults to SHA256.",
            "default": DEFAULT_ALGORITHM,
        },
    }
    output_variables = {}

    def locate_cosign(self):
        """Return a cosign path, or None if not found. Raise only if cosign_path
        was set explicitly but is missing."""
        configured = self.env.get("cosign_path")
        if configured:
            if os.path.exists(configured):
                return configured
            raise ProcessorError("cosign_path does not exist: %s" % configured)
        found = shutil.which("cosign")
        if found:
            return found
        for candidate in COSIGN_FALLBACK_PATHS:
            if os.path.exists(candidate):
                return candidate
        return None

    def resolve_bundle(self, artifact_path: str) -> str:
        """Return a local bundle path, downloading from bundle_url if needed."""
        bundle_path = self.env.get("bundle_path")
        if bundle_path:
            if not os.path.exists(bundle_path):
                raise ProcessorError("bundle_path does not exist: %s" % bundle_path)
            return bundle_path
        bundle_url = self.env.get("bundle_url")
        if not bundle_url:
            artifact_url = self.env.get("url")
            if not artifact_url:
                raise ProcessorError(
                    "No bundle_path, bundle_url, or %url% to locate the bundle.")
            bundle_url = artifact_url + ".sigstore.json"
        bundle_dest = artifact_path + ".sigstore.json"
        self.output("Fetching Sigstore bundle: %s" % bundle_url)
        self.download_to_file(bundle_url, bundle_dest)
        return bundle_dest

    def verify_with_cosign(self, cosign: str, artifact_path: str) -> None:
        """Run cosign verify-blob, raising on any non-zero exit."""
        identity = self.env.get("certificate_identity")
        identity_regexp = self.env.get("certificate_identity_regexp")
        if not identity and not identity_regexp:
            raise ProcessorError(
                "Set certificate_identity or certificate_identity_regexp.")
        if not self.env.get("certificate_oidc_issuer"):
            raise ProcessorError("certificate_oidc_issuer is required for cosign.")
        bundle_path = self.resolve_bundle(artifact_path)

        cosign_cmd = [cosign, "verify-blob", "--bundle", bundle_path]
        if identity_regexp:
            cosign_cmd.extend(["--certificate-identity-regexp", identity_regexp])
        else:
            cosign_cmd.extend(["--certificate-identity", identity])
        cosign_cmd.extend(
            ["--certificate-oidc-issuer", self.env["certificate_oidc_issuer"]])
        cosign_cmd.extend(self.env.get("extra_cosign_args", []))
        cosign_cmd.append(artifact_path)

        self.output("Running: %s" % " ".join(cosign_cmd))
        completed = subprocess.run(
            cosign_cmd, capture_output=True, text=True, check=False)
        # cosign writes "Verified OK" to stderr on success; surface it either way
        if completed.stderr.strip():
            self.output(completed.stderr.strip())
        if completed.returncode != 0:
            raise ProcessorError(
                "cosign verification failed for %s (exit %d): %s"
                % (artifact_path, completed.returncode,
                   completed.stderr.strip() or completed.stdout.strip()))
        self.output("Sigstore signature verified for %s" % artifact_path)

    def verify_with_checksum(self, artifact_path: str) -> None:
        """Fallback: verify artifact_path against a fetched checksums manifest."""
        checksums_url = self.env.get("checksums_url")
        asset_filename = self.env.get("asset_filename")
        if not checksums_url or not asset_filename:
            raise ProcessorError(
                "cosign not found and no checksum fallback configured "
                "(need checksums_url and asset_filename).")
        algorithm = self.env.get("algorithm", DEFAULT_ALGORITHM)
        self.output("WARNING: cosign unavailable; falling back to checksum "
                    "verification (integrity only, not signature authenticity).")
        self.output("Fetching checksums manifest: %s" % checksums_url)
        manifest_text = self.download(checksums_url, text=True)
        line_pattern = re.compile(
            r"^([0-9a-fA-F]+)\s+\*?%s\s*$" % re.escape(asset_filename), re.MULTILINE)
        match = line_pattern.search(manifest_text)
        if not match:
            raise ProcessorError(
                "No checksum line for %s found in manifest" % asset_filename)
        expected_checksum = match.group(1).lower()

        hasher = hashlib.new(algorithm)
        with open(artifact_path, "rb") as opened_file:
            for chunk in iter(lambda: opened_file.read(8192), b""):  # 8KB chunks
                hasher.update(chunk)
        calculated_checksum = hasher.hexdigest().lower()
        self.output("Expected checksum:   %s" % expected_checksum)
        self.output("Calculated checksum: %s" % calculated_checksum)
        if calculated_checksum != expected_checksum:
            raise ProcessorError(
                "Checksum mismatch for %s: expected %s, calculated %s"
                % (artifact_path, expected_checksum, calculated_checksum))
        self.output("Checksum verified for %s against manifest" % asset_filename)

    def main(self) -> None:
        artifact_path = self.env.get("artifact_path") or self.env.get("pathname")
        if not artifact_path or not os.path.exists(artifact_path):
            raise ProcessorError("Artifact to verify does not exist: %s" % artifact_path)

        cosign = self.locate_cosign()
        if cosign:
            self.verify_with_cosign(cosign, artifact_path)
            return
        # require_cosign may arrive as a real bool or a "true"/"false" string
        require_cosign = self.env.get("require_cosign", True)
        if isinstance(require_cosign, str):
            require_cosign = require_cosign.strip().lower() not in ("false", "no", "0")
        if require_cosign:
            raise ProcessorError(
                "cosign not found on PATH or in %s. Install it from "
                "https://github.com/sigstore/cosign/releases, set cosign_path, or "
                "set require_cosign to false to allow checksum fallback."
                % ", ".join(COSIGN_FALLBACK_PATHS))
        self.verify_with_checksum(artifact_path)


if __name__ == "__main__":
    PROCESSOR = CosignVerifier()
    PROCESSOR.execute_shell()
