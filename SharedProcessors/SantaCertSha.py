#!/usr/bin/python
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

import os
import subprocess

from glob import glob
from autopkglib import ProcessorError
from autopkglib.DmgMounter import DmgMounter

__all__ = ["SantaCertSha"]


class SantaCertSha(DmgMounter):
    """Verifies signed application bundles developer certificate as sha256.
       Requires santactl to be present & at the default path - 
       /usr/local/bin/santactl, and either a bare .app bundle in a DMG,
       or an unpacked pkg - use PkgPayloadUnpacker in a previous step.
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
                ("File path to an application bundle (.app)."
                 "Can point to a path inside a .dmg, which will be mounted."),
        },
        "destination_sha_text_path": {
            "required": True,
            "description": "Destination directory to drop a file named for the sha.",
        },
    }
    output_variables = {
        "santa_sha": {
            "description":
                ("The sha256 of the dev cert that the app bundle was signed with,"
                 "for use to white/blacklist the app with Google's Santa"
                 "(see github.com/google/santa)"),
        },
    }

    description = __doc__

    def santactl_check_signature(self, path):
        """
        Runs 'santactl fileinfo <path>'. Returns a tuple with boolean exit status
        and a list of found certificate authority names
        """
        process = ["/usr/local/bin/santactl",
                   "fileinfo",
                   path]
        proc = subprocess.Popen(process,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (output, error) = proc.communicate()

        # Log everything
        dev_cert_sha = ''
        output = output.splitlines()
        if output:
            for line in output[:6]:
                self.output("%s" % line)
        if error:
            for line in error.splitlines():
                self.output("%s" % line)

        # Parse the output for certificate sha256
        # currently sha is 31 chars in on line that starts with "1. SHA-256"
        for line in output:
            if "1. SHA-256" in line:
                dev_cert_sha = line.split()[3]

        # Return a tuple with boolean exit status & the sha256 cert fingerprint
        return proc.returncode == 0, dev_cert_sha

    def main(self):
        if self.env.get('DISABLE_APP_SIGNATURE_VERIFICATION'):
            self.output("App dev signature verification disabled for this recipe "
                        "run.")
            return
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
                    "WARNING: Multiple paths match 'input_path' glob '%s':"
                    % input_path)
                for match in matches:
                    self.output("  - %s" % match)

            if [c for c in '*?[]!' if c in input_path]:
                self.output("Using path '%s' matched from globbed '%s'."
                            % (matched_input_path, input_path))
            file_extension = os.path.splitext(matched_input_path)[1]
            if file_extension == ".app":
                exit_code, sha = self.santactl_check_signature(matched_input_path)
                if exit_code:
                    self.output(
                        "Writing to the defined folder '%s' for the cert's sha256,\n '%s':"
                        % (self.env['destination_sha_text_path'], sha))
                    sha_file = os.path.join(self.env['destination_sha_text_path'],
                                            sha)
                    try:
                        with open(sha_file, 'a') as sha_path:
                            sha_path.write(sha)
                    except:
                        pass
            else:
                raise ProcessorError("Unsupported path, expects .app bundle")

        finally:
            if dmg:
                self.unmount(dmg_path)


if __name__ == '__main__':
    PROCESSOR = SantaCertSha()
    PROCESSOR.execute_shell()
