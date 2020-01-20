#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2016 Allister Banks, wholesale lifted from code by Greg Neagle
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

from autopkglib import Processor, ProcessorError, URLGetter

# from distutils.version import LooseVersion
# from operator import itemgetter

try:
    from plistlib import loads as readPlistFromString  # Python 3
except ImportError:
    from plistlib import readPlistFromString  # Python 2

__all__ = ["MAUURLandUpdateInfoProvider"]

CULTURE_CODE = "0409"
BASE_URL = (
    "https://officecdn-microsoft-com.akamaized.net/pr/"
    "C1297A47-86C4-4C1F-97FA-950631F94777/MacAutoupdate/%sMSau04.xml"
)
MUNKI_UPDATE_NAME = "Microsoft Auto Update"


class MAUURLandUpdateInfoProvider(URLGetter):
    """Provides a download URL for the most recent version of MAU."""
    input_variables = {
        "culture_code": {
            "required": False,
            "description": ("See "
                "http://msdn.microsoft.com/en-us/library/ee825488(v=cs.20).aspx"
                " for a table of CultureCodes Defaults to 0409, which "
                "corresponds to en-US (English - United States)"),
        },
        "base_url": {
            "required": False,
            "description": ("Default is %s. If this is given, culture_code "
                "is ignored." % (BASE_URL % CULTURE_CODE)),
        },
    }
    output_variables = {
        "url": {
            "description": "URL to the latest MAU installer.",
        },
        "additional_pkginfo": {
            "description":
                "Some pkginfo fields extracted from the Microsoft metadata.",
        },
        "display_name": {
            "description":
                "The name of the package that includes the version.",
        },
        "version": {
            "description": "Version, as per Title key in MAU feed",
        },
    }
    description = __doc__

    def getAppPath(self, item):
        """less hardcoding, finds path to app via feed metadata item"""
        app_path = item.get("UpdateBaseSearchPath",
                            "/Library/Application Support/Microsoft/MAU2.0")
        return app_path

    def getInstallsItems(self, item):
        """Returns remi-hardcoded installs item + version"""
        app_path = self.getAppPath(item)
        installs_item = {
            "CFBundleShortVersionString": self.getVersion(item),
            "CFBundleVersion": self.getVersion(item),
            "path": ("%s/Contents/Info.plist" % app_path),
            "type": "bundle",
            "version_comparison_key": "CFBundleShortVersionString"
        }
        return [installs_item]

    def getVersion(self, item):
        """Extracts the version of the item."""
        version_str = item.get("Update Version", "")
        return version_str

    def valueToOSVersionString(self, value):
        """Converts a value to an OS X version number"""
        # Map string type for both Python 2 and Python 3.
        try:
            _ = basestring
        except NameError:
            basestring = str  # pylint: disable=W0622

        if isinstance(value, int):
            version_str = hex(value)[2:]
        elif isinstance(value, basestring):
            if value.startswith('0x'):
                version_str = value[2:]
        # OS versions are encoded as hex:
        # 4184 = 0x1058 = 10.5.8
        # not sure how 10.4.11 would be encoded;
        # guessing 0x104B ?
        major = 0
        minor = 0
        patch = 0
        try:
            if len(version_str) == 1:
                major = int(version_str[0])
            if len(version_str) > 1:
                major = int(version_str[0:2])
            if len(version_str) > 2:
                minor = int(version_str[2], 16)
            if len(version_str) > 3:
                patch = int(version_str[3], 16)
        except ValueError:
            raise ProcessorError("Unexpected value in version: %s" % value)
        return "%s.%s.%s" % (major, minor, patch)

    def get_mauInstaller_info(self):
        """Gets info about a MAU installer from MS metadata."""
        if "base_url" in self.env:
            base_url = self.env["base_url"]
        else:
            culture_code = self.env.get("culture_code", CULTURE_CODE)
            base_url = BASE_URL % culture_code
        # Add the MAU User-Agent, out of date but still fine
        headers = {
            "User-Agent": "Microsoft%20AutoUpdate/3.4 CFNetwork/760.2.6 Darwin/15.4.0 (x86_64)",
        }
        try:
            data = self.download(base_url, headers=headers)
        except Exception as err:
            raise ProcessorError("Can't download %s: %s" % (base_url, err))

        item = readPlistFromString(data)[-1]

        self.env["url"] = item["Location"]
        self.env["pkg_name"] = item["Payload"]
        self.output("Found URL %s" % self.env["url"])
        self.output("Got update: '%s'" % item["Title"])
        # now extract useful info from the rest of the metadata that could
        # be used in a pkginfo
        pkginfo = {}
        pkginfo["description"] = "<html>%s</html>" % item["Short Description"]
        pkginfo["display_name"] = item["Title"]
        pkginfo["version"] = self.getVersion(item)
        max_os = self.valueToOSVersionString(item['Max OS'])
        min_os = self.valueToOSVersionString(item['Min OS'])
        if max_os != "0.0.0":
            pkginfo["maximum_os_version"] = max_os
        if min_os != "0.0.0":
            pkginfo["minimum_os_version"] = min_os
        installs_items = self.getInstallsItems(item)
        if installs_items:
            pkginfo["installs"] = installs_items
            self.env["version"] = self.getVersion(item)

        pkginfo['name'] = self.env.get("munki_update_name", MUNKI_UPDATE_NAME)
        self.env["additional_pkginfo"] = pkginfo
        self.env["display_name"] = pkginfo["display_name"]
        self.output("Additional pkginfo: %s" % self.env["additional_pkginfo"])

    def main(self):
        """Get information about an update"""
        self.get_mauInstaller_info()


if __name__ == "__main__":
    processor = MAUURLandUpdateInfoProvider()
    processor.execute_shell()
