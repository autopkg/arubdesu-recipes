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

from __future__ import absolute_import
import hashlib
import os

from stat import S_IXOTH
from autopkglib import ProcessorError
from autopkglib.DmgMounter import DmgMounter

__all__ = ["SantaUnsignedSha"]


class SantaUnsignedSha(DmgMounter):
    #pylint disable=line-too-long
    """Gets sha256 from unsigned binaries to inject into Munki pkginfo via
       preflight script, which therefore auto-whitelists via santactl rule verb.
       Checks if binaries aren't 0 bytes/executable, but you should verify path
       remains valid. Assumes you will use MunkiPkgInfoMerger in the subsequent
       step with preinstall key to merge script into pkginfo.
       """

    input_variables = {
        "input_paths": {
            "required": True,
            "description":
                ("File paths to unsigned binaries to grab sha from. Pkg payloads must be expanded."
                 "Can point to a path inside a .dmg, which will be mounted."),
        },
    }
    output_variables = {
        "preinstall_script": {
            "description":
                "Assembled preinstall script for whitelisting binaries to be merged"
                "into Munki pkginfo. Assumes santactl is present/at default path on clients",
        },
    }

    description = __doc__

    def check_and_hash(self, path):
        """
        Uses hashlib's sha256 algorithm to fingerprint path after checking zero size
        and execute bit is set, returns sha if passes check, bool False if not
        """
        binary_sha = False
        mode = os.stat(path).st_mode
        size = os.stat(path).st_size
        if size != 0 and (mode & S_IXOTH):
            try:
                with open(path, 'rb') as sha_me:
                    binary_sha = hashlib.sha256(sha_me.read()).hexdigest()
            except BaseException as error:
                raise ProcessorError(
                    "Encoutered error %s getting sha256 from path, %s" % (error, path))
        return binary_sha


    def gen_preinstall(self, shas_forwlist):
        """builds & returns preinstall script as multi-line string with whitelist commands"""
        preinstall_temp = """#!/bin/bash
        # Adds new unsigned binaries sha256 to santa whitelist, bails if santactl not present
        [[ ! -f "/Library/Extensions/santa-driver.kext/Contents/MacOS/santactl" ]] && exit 0
        """
        whitelist_str = "\n/usr/local/bin/santactl rule --whitelist --sha256 "
        for sha in shas_forwlist:
            preinstall_temp += whitelist_str + sha
        preinstall_temp += "\nexit 0"
        return preinstall_temp


    def main(self):
        # Check if we're trying to read something inside a dmg.
        input_paths = self.env['input_paths']
        shas_forwlist = []
        dmg_paths = []
        for input_path in input_paths:
            (dmg_path, dmg, dmg_source_path) = self.parsePathForDMG(input_path)
            if dmg:
                dmg_paths.append(input_path)
                mount_point = self.mount(dmg_path)
            else:
                sha = self.check_and_hash(input_path)
                shas_forwlist.append(sha)
        for input_path in dmg_paths:
            try:
                # Mount dmg and copy path inside.
                input_path = os.path.join(mount_point, dmg_source_path)
                # check is executable and not 0 bytes
                sha = self.check_and_hash(input_path)
                self.output("Here's some sha %s" % sha)
                shas_forwlist.append(sha)
            except BaseException as error:
                raise ProcessorError("Error processing path '%s', '%s'" % (input_path, error))
        if dmg:
            self.unmount(dmg_path)
        if len(shas_forwlist) > 0:
            preinstall = self.gen_preinstall(shas_forwlist)
            self.env["preinstall_script"] = preinstall
        else:
            raise ProcessorError("Could not build list of binary shas from input")


if __name__ == '__main__':
    PROCESSOR = SantaUnsignedSha()
    PROCESSOR.execute_shell()
