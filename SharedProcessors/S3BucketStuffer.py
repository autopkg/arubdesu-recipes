#!/usr/local/autopkg/python
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



__all__ = ["S3BucketStuffer"]

class S3BucketStuffer(Processor):
    """Checks for pkg on AWS S3 bucket at optional prefix & uploads if not present,
       optionally sets public read acl. Shells out to awscli binary to do the dirty work, assumes auth is config'd in env.
       Want it boto3'd? PR's accepted!
       Assumes previous recipe is flat download, so pathname var would be present"""
    input_variables = {
        "awscli": {
            "required": False,
            "description": "If not present at /usr/local/bin/aws, gimme where to find it pls",
        },
        "bucket": {
            "required": True,
            "description": "Your S3 bucket name",
        },
        "prefix": {
            "required": False,
            "description":
                ("If you want it to check for/write to a path like a 'subfolder'."
                 "Note that this is being lazily string-concat'd to an s3 'url', so measure twice"),
        },
        "s3pubReadAcl": {
            "required": False,
            "description": "I may not need this, but maybe you want public read?",
        },
    }
    output_variables = {
    }

    description = __doc__

    def check(self, bukkit, path):
        """Check for already present artifact"""
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


    def main(self):
        """Gimme some main"""
        bukkit, prefix, awscli = self.env['bucket'], self.env.get('prefix', False), self.env.get('awscli', False)
        if not awscli:
            aws = '/usr/local/bin/aws'
        justpkg = os.path.basename(pathname)
        shoveit = self.check(bukkit, justpkg)
        esthreeurl = 's3://'+ bukkit + '/'
        if prefix:
            esthreeurl += prefix
        if shoveit:
            if os.path.exists(aws):
                cmd = [aws, 's3', 'cp', esthreeurl + justpkg]
                if self.env['s3pubReadAcl']:
                    cmd.extend(["--acl", "public-read"])
                try:
                    subprocess.check_output(cmd)
                except subprocess.CalledProcessError as cperr:
                    raise ProcessorError("AWS Errorrror %s" % cperr)
            else:
                raise ProcessorError("No awscli...")
