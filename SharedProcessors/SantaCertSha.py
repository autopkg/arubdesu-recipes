#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Copyright 2016 Allister Banks, mostly stolen from Hannes Juutilainen
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
"""See docstring for SantaCertSha class"""

from __future__ import absolute_import

import os
import subprocess
from glob import glob

from autopkglib import ProcessorError
from autopkglib.DmgMounter import DmgMounter

__all__ = ["SantaCertSha"]


class SantaCertSha(DmgMounter):
    """Verifies sha256 fingerprint on developer certificate used to sign application bundles.
       Requires santactl to be present & at the default path -
       /usr/local/bin/santactl, and either a bare .app bundle, either in a DMG,
       or an unzipped/packed path - use PkgPayloadUnpacker in a previous step.
       """

    input_variables = {
        "DISABLE_APP_SIGNATURE_VERIFICATION": {
            "required": False,
            "description":
                ("Skip this Processor step altogether. Typically this "
                 "would be invoked using AutoPkg's defaults or via '--key' "
                 "CLI options at the time of the run, rather than being "
                 "defined explicitly within a recipe."),
        },
        "input_path": {
            "required": True,
            "description":
                ("File path to an application bundle (.app). Pkg payloads must be expanded."
                 "Can point to a path inside a .dmg, which will be mounted."),
        },
        "expected_certsha": {
            "required": True,
            "description": ("Sha identifier of cert used to sign the app, which "
                           "would require santactl to be installed.")
        },
    }
    output_variables = {
    }

    description = __doc__

    def santactl_check_signature(self, path):
        """
        Runs 'santactl fileinfo' commands on <path>, returns the identifier for
        certificate used to sign the code
        """
        cmd = ["/usr/local/bin/santactl",
                   "fileinfo", "--cert-index", "1", "--key", "SHA-256",
                   path]
        output = subprocess.check_output(cmd)
        if output:
            dev_cert_sha = output.strip()
        else:
            raise ProcessorError("Encoutered error processing fileinfo for path")
        # not quite sure how the first four chars are empty from stdout, but
        return dev_cert_sha[5:]

    def main(self):
        if self.env.get('DISABLE_APP_SIGNATURE_VERIFICATION'):
            self.output("App dev signature verification disabled for this recipe "
                        "run.")
            return
        expected_certsha = self.env.get('expected_certsha')
        # Check if we're trying to read something inside a dmg.
        input_path = self.env['input_path']
        (dmg_path, dmg, dmg_source_path) = self.parsePathForDMG(input_path)
        try:
            if dmg:
                # Mount dmg and copy path inside.
                mount_point = self.mount(dmg_path)
                input_path = os.path.join(mount_point, dmg_source_path)
            # process path with glob.glob
            matches = glob(input_path)
            if len(matches) == 0:
                raise ProcessorError(
                    "Error processing path '%s' with glob. " % input_path)
            matched_input_path = matches[0]
            if len(matches) > 1:
                self.output(
                    "WARNING: Multiple paths match 'input_path' glob '%s':" % input_path)
                for match in matches:
                    self.output("  - %s" % match)

            if [c for c in '*?[]!' if c in input_path]:
                self.output("Using path '%s'" % matched_input_path)
            file_extension = os.path.splitext(matched_input_path)[1]
            if file_extension == ".app":
                sha = self.santactl_check_signature(matched_input_path)
                if sha != str(expected_certsha):
                    raise ProcessorError(
                        "Mismatch in found cert sha256, new hash is %s" % sha[5:])
                else:
                    self.output("Sha's match")
            else:
                raise ProcessorError("Unsupported path, expects .app bundle")

        finally:
            if dmg:
                self.unmount(dmg_path)


if __name__ == '__main__':
    PROCESSOR = SantaCertSha()
    PROCESSOR.execute_shell()
