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
"""See docstring for AppleSoftwareUpdateProductDownloader class"""

import hashlib
import os
import shutil
import struct
from typing import Any, Dict, List

# pyright: reportMissingImports=false
from autopkglib import (  # ty: ignore[unresolved-import]  # pylint: disable=import-error
    ProcessorError,
)
from autopkglib.URLGetter import (  # ty: ignore[unresolved-import]  # pylint: disable=import-error
    URLGetter,
)

__all__ = ["AppleSoftwareUpdateProductDownloader"]

DIST_FILENAME = "product.dist"
# A xar header: magic, header size, version, compressed and uncompressed table-of-contents
# lengths, checksum algorithm.
XAR_HEADER = struct.Struct(">4sHHQQI")
XAR_MAGIC = b"xar!"


def toc_sha1(path: str) -> str:
    """SHA-1 of a flat package's compressed xar table of contents, which is what the catalog's
    Digest holds. The table of contents carries the checksum of every file in the package and
    is what the package's signature covers. Empty when the file is not a xar archive."""
    with open(path, "rb") as package:
        header = package.read(XAR_HEADER.size)
        if len(header) != XAR_HEADER.size:
            return ""
        magic, header_size, _, toc_length, _, _ = XAR_HEADER.unpack(header)
        if magic != XAR_MAGIC:
            return ""
        package.seek(header_size)
        # Integrity only; the packages' signatures are what AppleSoftwareUpdateProductBuilder
        # relies on.
        return hashlib.sha1(package.read(toc_length), usedforsecurity=False).hexdigest()


class AppleSoftwareUpdateProductDownloader(URLGetter):
    """Downloads every package of the software update product AppleSoftwareUpdateCatalogProvider
    chose, plus the product's English .dist, into one folder per product.

    Each package's size, and the SHA-1 of its xar table of contents, must match the catalog's
    Size and Digest. A package already present with
    the right size and digest is not downloaded again; files the product no longer lists, and
    folders of other products under download_dir, are removed. Like URLDownloader, this
    downloads during the check phase.

    It sets pathname to the package named package_filename, so a recipe that uses one component
    keeps working. Signatures are not checked here: AppleSoftwareUpdateProductBuilder checks
    every component before it builds the product."""

    description = __doc__
    input_variables = {
        "product_packages": {
            "required": True,
            "description": "Package list from AppleSoftwareUpdateCatalogProvider.",
        },
        "product_key": {
            "required": True,
            "description": "Catalog product key, e.g. 082-83364; names the product's folder.",
        },
        "dist_url": {"required": True, "description": "URL of the product's English .dist."},
        "package_filename": {
            "required": True,
            "description": "The package pathname points at, e.g. CLTools_Executables.pkg.",
        },
        "download_dir": {
            "required": False,
            "description": (
                "Folder holding one subfolder per product. Defaults to "
                "%RECIPE_CACHE_DIR%/downloads/products."
            ),
        },
    }
    output_variables = {
        "product_dir": {"description": "Folder holding the product's packages and .dist."},
        "product_dist_path": {"description": "The product's English .dist."},
        "pathname": {"description": "The package named package_filename."},
        "download_changed": {"description": "True when any package was downloaded."},
    }

    def main(self) -> None:
        download_dir = self.env.get("download_dir") or os.path.join(
            self.env["RECIPE_CACHE_DIR"], "downloads", "products"
        )
        product_dir = os.path.join(download_dir, self.env["product_key"])
        os.makedirs(product_dir, exist_ok=True)
        for name in os.listdir(download_dir):
            other = os.path.join(download_dir, name)
            if name != self.env["product_key"] and os.path.isdir(other):
                self.output(f"Removing {other}, an earlier product")
                shutil.rmtree(other)
        packages: List[Dict[str, Any]] = self.env["product_packages"]
        if not packages:
            raise ProcessorError("product_packages is empty.")
        downloaded = 0
        for package in packages:
            path = os.path.join(product_dir, package["filename"])
            if self.matches_catalog(path, package):
                continue
            self.fetch(package["url"], path)
            if not self.matches_catalog(path, package):
                raise ProcessorError(
                    f"{package['filename']} does not match the catalog's size "
                    f"{package['size']} and digest {package['digest']}."
                )
            downloaded += 1
        wanted = {package["filename"] for package in packages} | {DIST_FILENAME}
        for name in os.listdir(product_dir):
            if name not in wanted:
                os.unlink(os.path.join(product_dir, name))
        dist_path = os.path.join(product_dir, DIST_FILENAME)
        self.fetch(self.env["dist_url"], dist_path)
        pathname = os.path.join(product_dir, self.env["package_filename"])
        if not os.path.isfile(pathname):
            raise ProcessorError(f"The product has no {self.env['package_filename']}.")
        self.env["product_dir"] = product_dir
        self.env["product_dist_path"] = dist_path
        self.env["pathname"] = pathname
        self.env["download_changed"] = downloaded > 0
        self.output(f"{downloaded} of {len(packages)} packages downloaded into {product_dir}")

    def matches_catalog(self, path: str, package: Dict[str, Any]) -> bool:
        if not os.path.isfile(path) or os.path.getsize(path) != package["size"]:
            return False
        return not package["digest"] or toc_sha1(path) == package["digest"].lower()

    def fetch(self, url: str, path: str) -> None:
        """curl with --fail into a temporary name, renamed into place only when complete."""
        temporary = f"{path}.download"
        curl_cmd = self.prepare_curl_cmd()
        curl_cmd.append("--fail")
        self.add_curl_common_opts(curl_cmd)
        curl_cmd.extend([url, "-o", temporary])
        self.output(f"Downloading {url}")
        try:
            self.download_with_curl(curl_cmd, text=False)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


if __name__ == "__main__":
    PROCESSOR = AppleSoftwareUpdateProductDownloader()
    PROCESSOR.execute_shell()
