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
#
# Ported from legacy-files/process_uma.py in macadmins/sofa (Apache-2.0), whose catalog and
# .dist parsing is by Alex Ferrer Alequin (Arequ) with error handling by Allister Banks
# (arubdesu), at commit 12305e6249dac1c4a26554127f8c1ed440a06a20 (2025-09-20).

"""See docstring for AppleSoftwareUpdateCatalogProvider class"""

import gzip
import os
import plistlib
import re
import xml.etree.ElementTree as ElementTree
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

# pyright: reportMissingImports=false
from autopkglib import (  # ty: ignore[unresolved-import]  # pylint: disable=import-error
    APLooseVersion,
    ProcessorError,
)
from autopkglib.URLGetter import (  # ty: ignore[unresolved-import]  # pylint: disable=import-error
    URLGetter,
)

__all__ = ["AppleSoftwareUpdateCatalogProvider"]

GZIP_MAGIC = b"\x1f\x8b"
# A .dist's <strings language="English"> block holds .strings lines: "SU_TITLE" = "Command ...";
STRINGS_LINE = re.compile(r'"(?P<key>[^"]+)"\s*=\s*"(?P<value>[^"]*)";')


def dist_strings(dist_root: ElementTree.Element) -> Dict[str, str]:
    """The English localization table, which CLT titles such as SU_TITLE resolve through."""
    for strings in dist_root.iter("strings"):
        if strings.get("language") == "English":
            return {
                match["key"]: match["value"] for match in STRINGS_LINE.finditer(strings.text or "")
            }
    return {}


def auxinfo_values(dist_root: ElementTree.Element) -> Dict[str, Any]:
    """The <auxinfo> plist dict, where installer .dist files keep BUILD and VERSION."""
    auxinfo_dict = dist_root.find("auxinfo/dict")
    if auxinfo_dict is None:
        return {}
    plist_bytes = b"<plist>" + ElementTree.tostring(auxinfo_dict) + b"</plist>"
    try:
        return plistlib.loads(plist_bytes)
    except plistlib.InvalidFileException:
        return {}


def pkg_ref_version(dist_root: ElementTree.Element, pkg_ref_id: str) -> str:
    """The version of the pkg-ref whose id or packageIdentifier is pkg_ref_id.

    The choice's pkg-ref carries packageIdentifier and no version; a second pkg-ref with the same
    id carries the version. Older CLT .dist files use a short id, CLTools_Executables, so both
    attributes are matched."""
    # Every id the requested identifier goes by in this .dist.
    ref_ids = {
        pkg_ref.get("id")
        for pkg_ref in dist_root.iter("pkg-ref")
        if pkg_ref_id in (pkg_ref.get("id"), pkg_ref.get("packageIdentifier"))
    }
    for pkg_ref in dist_root.iter("pkg-ref"):
        if pkg_ref.get("id") in ref_ids and pkg_ref.get("version"):
            return pkg_ref.get("version", "")
    return ""


def dist_minimum_os(dist_root: ElementTree.Element) -> str:
    """The min attribute of the first os-version in <volume-check><allowed-os-versions>."""
    os_version = dist_root.find("volume-check/allowed-os-versions/os-version")
    return os_version.get("min", "") if os_version is not None else ""


def dist_maximum_os(dist_root: ElementTree.Element) -> str:
    """The exclusive before= bound turned into an inclusive maximum: 28.0 -> 27.99."""
    os_version = dist_root.find("volume-check/allowed-os-versions/os-version")
    before = os_version.get("before", "") if os_version is not None else ""
    parts = [int(part) for part in before.split(".") if part.isdigit()]
    if not parts:
        return ""
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    parts[-1] -= 1
    return ".".join(str(part) for part in parts) + ".99"


def dist_host_architectures(dist_root: ElementTree.Element) -> str:
    """The <options hostArchitectures> attribute; Command Line Tools .dist files carry it."""
    options = dist_root.find("options")
    return options.get("hostArchitectures", "") if options is not None else ""


class AppleSoftwareUpdateCatalogProvider(URLGetter):
    """Finds the highest version of a package in an Apple software update catalog, such as a
    macOS installer (InstallAssistant.pkg) or the Command Line Tools (CLTools_Executables.pkg).

    The catalog is a file a URLDownloader step already fetched, so its ETag caching is a visible
    recipe step. Each product carrying package_filename has its English .dist fetched for a
    title, version and build. The version comes from the .dist's <auxinfo> BUILD and VERSION
    keys, or from the pkg-ref named by pkg_ref_id. The winner is the highest version, not the
    newest post date: Apple posts updates for older macOS lines after newer ones, so the most
    recently posted installer is often not the newest macOS."""

    description = __doc__
    input_variables = {
        "catalog_path": {
            "required": True,
            "description": (
                "Path to the downloaded catalog, gzip-compressed or plain, e.g. %pathname%."
            ),
        },
        "package_filename": {
            "required": True,
            "description": "Package file name to look for, e.g. InstallAssistant.pkg.",
        },
        "major_version": {
            "required": False,
            "default": "",
            "description": (
                "Only consider versions whose first component is this, e.g. 26. Empty means any."
            ),
        },
        "title_contains": {
            "required": False,
            "default": "",
            "description": "Only consider products whose resolved title contains this text.",
        },
        "pkg_ref_id": {
            "required": False,
            "default": "",
            "description": (
                "Take version from the pkg-ref whose id or packageIdentifier is this, e.g. "
                "com.apple.pkg.CLTools_Executables, instead of <auxinfo>."
            ),
        },
    }
    output_variables = {
        "url": {"description": "URL of the package."},
        "version": {"description": "Version of the chosen product."},
        "build": {"description": "Build from <auxinfo>; empty when pkg_ref_id supplied version."},
        "title": {"description": "Product title, with localized keys resolved."},
        "product_key": {"description": "Catalog product key, e.g. 082-83364."},
        "post_date": {"description": "When Apple posted the product, ISO 8601."},
        "package_size": {"description": "Package size in bytes, from the catalog."},
        "dist_url": {"description": "URL of the English .dist that supplied the metadata."},
        "minimum_os_version": {
            "description": "The .dist's lowest allowed macOS version, from <allowed-os-versions>."
        },
        "host_architectures": {
            "description": (
                "The .dist's hostArchitectures, e.g. 'arm64', comma separated; empty when the "
                ".dist does not restrict architectures."
            )
        },
        "maximum_os_version": {
            "description": (
                "The highest macOS version below the .dist's exclusive 'before' bound, in a form "
                "Munki compares inclusively: before 28.0 becomes 27.99, before 15.4 becomes "
                "15.3.99. Empty when the .dist sets no upper bound."
            )
        },
        "product_packages": {
            "description": (
                "Every package in the chosen product, each a dict of url, filename, size and "
                "digest, for AppleSoftwareUpdateProductDownloader."
            )
        },
        "additional_pkginfo": {
            "description": (
                "minimum_os_version, maximum_os_version and supported_architectures from the "
                ".dist, whichever it sets, for MunkiPkginfoMerger."
            )
        },
    }

    def main(self) -> None:
        catalog = self.read_catalog(self.env["catalog_path"])
        package_filename = self.env["package_filename"]
        products = catalog.get("Products", {})
        candidates: List[Dict[str, Any]] = []
        for product_key, product in products.items():
            # The first package in the product whose URL ends in package_filename, if any.
            package = next(
                (
                    entry
                    for entry in product.get("Packages", [])
                    if os.path.basename(urlparse(entry.get("URL", "")).path) == package_filename
                ),
                None,
            )
            dist_url = product.get("Distributions", {}).get("English")
            if package is None or not dist_url:
                continue
            candidate = self.describe(product_key, product, package, dist_url)
            if candidate:
                candidates.append(candidate)
        self.output(f"{len(candidates)} products in the catalog carry {package_filename}")
        major_version = str(self.env.get("major_version") or "")
        title_contains = self.env.get("title_contains") or ""
        # Keep only the products that pass both optional filters.
        eligible = [
            candidate
            for candidate in candidates
            if (not major_version or candidate["version"].split(".")[0] == major_version)
            and title_contains in candidate["title"]
        ]
        if not eligible:
            raise ProcessorError(
                f"No product carries {package_filename} with major_version '{major_version}' "
                f"and a title containing '{title_contains}'."
            )
        # Highest version first; the newer post date wins a tie.
        winner = max(
            eligible,
            key=lambda candidate: (APLooseVersion(candidate["version"]), candidate["post_date"]),
        )
        self.env.update(winner)
        # Munki pkginfo keys for whichever bounds the .dist sets.
        gating: Dict[str, Any] = {
            key: winner[key] for key in ("minimum_os_version", "maximum_os_version") if winner[key]
        }
        if winner["host_architectures"]:
            gating["supported_architectures"] = winner["host_architectures"].split(",")
        self.env["additional_pkginfo"] = gating
        self.output(
            f"{winner['title']} {winner['version']} {winner['build']} ({winner['product_key']}, "
            f"posted {winner['post_date']}, macOS {winner['minimum_os_version']} or later, "
            f"architectures '{winner['host_architectures']}'): {winner['url']}"
        )

    def read_catalog(self, catalog_path: str) -> Dict[str, Any]:
        try:
            with open(catalog_path, "rb") as catalog_file:
                catalog_bytes = catalog_file.read()
            if catalog_bytes.startswith(GZIP_MAGIC):
                catalog_bytes = gzip.decompress(catalog_bytes)
            return plistlib.loads(catalog_bytes)
        except (OSError, plistlib.InvalidFileException, ValueError) as catalog_error:
            raise ProcessorError(
                f"Cannot read catalog {catalog_path}: {catalog_error}"
            ) from catalog_error

    def describe(
        self, product_key: str, product: Dict[str, Any], package: Dict[str, Any], dist_url: str
    ) -> Optional[Dict[str, Any]]:
        """Output values for one product, or None when its .dist names no version."""
        curl_cmd = self.prepare_curl_cmd()
        curl_cmd.append("--fail")
        self.add_curl_common_opts(curl_cmd)
        curl_cmd.append(dist_url)
        dist_text = self.download_with_curl(curl_cmd, text=True)
        try:
            dist_root = ElementTree.fromstring(dist_text)
        except ElementTree.ParseError as parse_error:
            raise ProcessorError(f"Cannot parse {dist_url}: {parse_error}") from parse_error
        title_element = dist_root.find("title")
        title_key = (title_element.text or "").strip() if title_element is not None else ""
        title = dist_strings(dist_root).get(title_key, title_key)
        pkg_ref_id = self.env.get("pkg_ref_id") or ""
        if pkg_ref_id:
            version, build = pkg_ref_version(dist_root, pkg_ref_id), ""
        else:
            auxinfo = auxinfo_values(dist_root)
            version, build = str(auxinfo.get("VERSION", "")), str(auxinfo.get("BUILD", ""))
        if not version:
            self.output(f"Skipping {product_key}: {dist_url} names no version")
            return None
        post_date = product.get("PostDate")
        return {
            "url": package["URL"],
            "version": version,
            "build": build,
            "title": title,
            "product_key": product_key,
            "post_date": post_date.isoformat() if post_date else "",
            "package_size": int(package.get("Size", 0)),
            "dist_url": dist_url,
            "minimum_os_version": dist_minimum_os(dist_root),
            "maximum_os_version": dist_maximum_os(dist_root),
            "host_architectures": dist_host_architectures(dist_root),
            # One dict of plain values per package, which an AutoPkg env can carry.
            "product_packages": [
                {
                    "url": entry["URL"],
                    "filename": os.path.basename(urlparse(entry["URL"]).path),
                    "size": int(entry.get("Size", 0)),
                    "digest": entry.get("Digest", ""),
                }
                for entry in product.get("Packages", [])
            ],
        }


if __name__ == "__main__":
    PROCESSOR = AppleSoftwareUpdateCatalogProvider()
    PROCESSOR.execute_shell()
