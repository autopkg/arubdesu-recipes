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
"""See docstring for AppleSoftwareUpdateProductBuilder class"""

import os
import subprocess
import xml.etree.ElementTree as ElementTree
from typing import Any, Dict, List

# pyright: reportMissingImports=false
from autopkglib import (  # ty: ignore[unresolved-import]  # pylint: disable=import-error
    Processor,
    ProcessorError,
)
from autopkglib.CodeSignatureVerifier import (  # ty: ignore[unresolved-import]  # pylint: disable=import-error  # noqa: E501
    CodeSignatureVerifier,
)

__all__ = ["AppleSoftwareUpdateProductBuilder"]

SOFTWARE_UPDATE_AUTHORITIES = [
    "Software Update",
    "Apple Software Update Certification Authority",
    "Apple Root CA",
]


class AppleSoftwareUpdateProductBuilder(Processor):
    """Rebuilds a software update product, such as the Command Line Tools, as one installer
    package: Apple's own .dist with every component package it names, through
    `productbuild --distribution`. The result installs the same packages under the same
    install logic as Software Update: the .dist's architecture and macOS version checks, and
    its choices.

    Right before building, each component's installer signature is checked with core
    CodeSignatureVerifier's package check against expected_authority_names (Apple's Software
    Update chain by default), and every package the .dist names must be present. The product
    itself is unsigned; components inside a product archive keep their payloads, not their
    signatures. Setting DISABLE_CODE_SIGNATURE_VERIFICATION skips the check, as it does for
    CodeSignatureVerifier.

    An existing package of the same name is kept, as PkgCreator keeps one of the same version."""

    description = __doc__
    input_variables = {
        "product_dir": {
            "required": True,
            "description": "Folder holding the component packages, from the downloader.",
        },
        "product_dist_path": {"required": True, "description": "The product's .dist."},
        "pkgname": {
            "required": True,
            "description": "File name for the product, without .pkg, e.g. %NAME%-%version%.",
        },
        "pkgdir": {
            "required": False,
            "description": "Folder for the product. Defaults to %RECIPE_CACHE_DIR%.",
        },
        "expected_authority_names": {
            "required": False,
            "default": SOFTWARE_UPDATE_AUTHORITIES,
            "description": "Certificate chain every component must carry.",
        },
    }
    output_variables = {
        "pkg_path": {"description": "The built product."},
        "pkg_creator_summary_result": {"description": "Summary of the built product."},
    }

    def main(self) -> None:
        product_dir = self.env["product_dir"]
        named = self.dist_packages(self.env["product_dist_path"])
        missing = sorted(
            name for name in named if not os.path.isfile(os.path.join(product_dir, name))
        )
        if missing:
            raise ProcessorError(f"The .dist names packages not in {product_dir}: {missing}")
        pkg_path = os.path.join(
            self.env.get("pkgdir") or self.env["RECIPE_CACHE_DIR"], f"{self.env['pkgname']}.pkg"
        )
        self.env["pkg_path"] = pkg_path
        if os.path.isfile(pkg_path):
            self.output(f"{pkg_path} exists; not rebuilding.")
            return
        if self.env.get("DISABLE_CODE_SIGNATURE_VERIFICATION"):
            self.output(
                "WARNING: DISABLE_CODE_SIGNATURE_VERIFICATION is set; components unchecked."
            )
        else:
            # CodeSignatureVerifier inherits DmgMounter's constructor, which takes the env as
            # its first positional argument, named data.
            verifier = CodeSignatureVerifier(
                {
                    "expected_authority_names": self.env["expected_authority_names"],
                    "verbose": self.env.get("verbose", 0),
                }
            )
            for name in sorted(named):
                verifier.process_installer_package(os.path.join(product_dir, name))
        temporary = f"{pkg_path}.building"
        build_cmd = ["/usr/bin/productbuild", "--distribution", self.env["product_dist_path"]]
        build_cmd += ["--package-path", product_dir, temporary]
        try:
            completed = subprocess.run(build_cmd, capture_output=True, check=False, text=True)
            if completed.returncode:
                raise ProcessorError(f"productbuild failed: {completed.stderr.strip()}")
            os.replace(temporary, pkg_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.output(completed.stdout.strip() or completed.stderr.strip())
        self.env["pkg_creator_summary_result"] = {
            "summary_text": "The following packages were built:",
            "report_fields": ["version", "pkg_path"],
            "data": {"version": self.env.get("version", ""), "pkg_path": pkg_path},
        }

    def dist_packages(self, dist_path: str) -> List[str]:
        """File names of the component packages the .dist's pkg-refs name."""
        try:
            dist_root = ElementTree.parse(dist_path).getroot()
        except (OSError, ElementTree.ParseError) as parse_error:
            raise ProcessorError(f"Cannot read {dist_path}: {parse_error}") from parse_error
        # pkg-ref elements with text are the ones that name a package file.
        names: Dict[str, Any] = {
            (pkg_ref.text or "").strip(): None
            for pkg_ref in dist_root.iter("pkg-ref")
            if (pkg_ref.text or "").strip()
        }
        if not names:
            raise ProcessorError(f"{dist_path} names no packages.")
        return list(names)


if __name__ == "__main__":
    PROCESSOR = AppleSoftwareUpdateProductBuilder()
    PROCESSOR.execute_shell()
