#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Copyright 2017 Allister Banks, with herculean help from Clayton Burlison
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

from __future__ import absolute_import

import os
import plistlib
import subprocess

from autopkglib import Processor, ProcessorError

try:
    from urllib import request as urllib  # For Python 3
except ImportError:
    import urllib  # For Python 2



__all__ = ["ShoveStuffInABucket"]

class ShoveStuffInABucket(Processor):
    """Checks for pkg on AWS bucket and uploads if not present. Intended for use with Simian.
       Shells out to awscli binary to do the dirty work, assumes auth is config'd"""
    input_variables = {
        "pkg_repo_path": {
            "required": True,
            "description":
                ("File path to a pkg or dmg to upload. Corresponds to filename"
                 "of item as imported by Munki, so we get distinct version of"
                 "whatever item (dmg/pkg) it is"),
        },
        "pkginfo_repo_path": {
            "required": True,
            "description":
                ("Pkginfo file we need to read the dict out of before first"
                 "shoving our Cloudfront url + filename into the PackageCompleteURL key"
                 "and passing to simianimport"),
        },
        "gcloudapp": {
            "required": True,
            "description": "The name of the simian/appengine project",
        },
        "bucket": {
            "required": True,
            "description": "Your Cloudfronted S3 bucket name",
        },
        "cloudfrontprefix": {
            "required": True,
            "description": "URL prefix of your Cloudfronted S3 bucket",
        },
    }
    output_variables = {
    }

    description = __doc__

    def check(self, bukkit, path):
        """Check for already uploaded artifact"""
        cmd = ["/usr/local/bin/aws", "s3api", "head-object", "--bucket", bukkit, "--key", path]
        try:
            response_dict = subprocess.check_output(cmd)
            if response_dict:
                return False
        except subprocess.CalledProcessError as cperr:
            if cperr.returncode == 255:
                self.output("404'd, you're clear to upload")
                return True
        else:
            raise ProcessorError("Some other error occurred during check. Panic. Or, check your setup.")


    def shove_pcu(self, pkginfo_repo_path, cloudfrontprefix, justpkg, gcloudapp):
        """Takes munki-recipe-generated pkginfo and shoves in PackageCompleteURL,
           (overwriting original) and uploads via shoveplist (AKA simian-import).
           Also makes sure unstable as only catalog in case override missed it"""
        simimport = '/usr/local/bin/shoveplist'
        existing_dict = plistlib.readPlist(pkginfo_repo_path)
        justpkg_encoded = urllib.quote(justpkg)
        existing_dict['PackageCompleteURL'] = cloudfrontprefix + justpkg_encoded
        existing_dict['catalogs'] = ['unstable']
        plistlib.writePlist(existing_dict, pkginfo_repo_path)
        cmd = [simimport, '-pkgname', justpkg, '-pkgsinfo', pkginfo_repo_path, '-gcp.project', gcloudapp]
        if os.path.exists(simimport):
            try:
                subprocess.check_output(cmd)
                return
            except subprocess.CalledProcessError as cperr:
                raise ProcessorError("AppEngine Errorrror %s" % cperr)
        else:
            raise ProcessorError("No shoveplist...")


    def main(self):
        """Gimme some main"""
        aws = '/usr/local/bin/aws'
        pkg_repo_path = self.env['pkg_repo_path']
        pkginfo_repo_path = self.env['pkginfo_repo_path']
        justpkg = os.path.basename(pkg_repo_path)
        bukkit = self.env['bucket']
        gcloudapp = self.env['gcloudapp']
        shoveit = self.check(bukkit, justpkg)
        esthreeurl = 's3://'+ bukkit + '/'
        cloudfrontprefix = self.env['cloudfrontprefix']
        if shoveit:
            if os.path.exists(aws):
                cmd = [aws, 's3', 'cp', pkg_repo_path, esthreeurl + justpkg, "--acl", "public-read"]
                try:
                    subprocess.check_output(cmd)
                except subprocess.CalledProcessError as cperr:
                    raise ProcessorError("AWS Errorrror %s" % cperr)
                self.shove_pcu(pkginfo_repo_path, cloudfrontprefix, justpkg, gcloudapp)
            else:
                raise ProcessorError("No awscli...")
