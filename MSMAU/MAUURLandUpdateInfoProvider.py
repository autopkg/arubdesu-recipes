#!/usr/bin/env python
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


import plistlib
import urllib2

from distutils.version import LooseVersion
from operator import itemgetter

from autopkglib import Processor, ProcessorError


__all__ = ["MAUURLandUpdateInfoProvider"]

CULTURE_CODE = "0409"
BASE_URL = "http://www.microsoft.com/mac/autoupdate/%sMSau03.xml"
MUNKI_UPDATE_NAME = "Microsoft Auto Update"

class MAUURLandUpdateInfoProvider(Processor):
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
    }
    description = __doc__
    
    def sanityCheckExpectedTriggers(self, item):
        """Raises an exeception if the Trigger Condition or
        Triggers for an update don't match what we expect.
        Protects us if these change in the future."""
        if not item.get("Trigger Condition") == ["and", "Registered File"]:
            raise ProcessorError(
                "Unexpected Trigger Condition in item %s: %s" 
                % (item["Title"], item["Trigger Condition"]))
    
    def getInstallsItems(self, item):
        """Attempts to parse the Triggers to create an installs item"""
        self.sanityCheckExpectedTriggers(item)
        triggers = item.get("Triggers", {})
        paths = [triggers[key].get("File") for key in triggers.keys()]
        if "Contents/Info.plist" in paths:
            # use the apps info.plist as installs item
            installs_item = {
                "CFBundleShortVersionString": self.getVersion(item),
                "CFBundleVersion": self.getVersion(item),
                "path": ("/Applications/Lync/"
                         "Contents/Info.plist"),
                "type": "bundle",
                "version_comparison_key": "CFBundleShortVersionString"
            }
            return [installs_item]
        return None
    
    def getVersion(self, item):
        """Extracts the version of the update item."""
        # currently relies on the item having a title in the format
        # "Microsoft AutoUpdate x.y.z "
        title = item.get("Title", "")
        version_str = title[21:]
        return version_str
    
    def valueToOSVersionString(self, value):
        """Converts a value to an OS X version number"""
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
        # Get metadata URL
        req = urllib2.Request(base_url)
        # Add the MAU User-Agent, since MAU feed server seems to explicitly block
        # a User-Agent of 'Python-urllib/2.7' - even a blank User-Agent string
        # passes.
        req.add_header("User-Agent",
            "Microsoft%20AutoUpdate/3.4 CFNetwork/760.2.6 Darwin/15.4.0 (x86_64)")
        try:
            f = urllib2.urlopen(req)
            data = f.read()
            f.close()
        except BaseException as err:
            raise ProcessorError("Can't download %s: %s" % (base_url, err))
        
        metadata = plistlib.readPlistFromString(data)
        # Lync 'update' metadata is a list of dicts.
        # we need to sort by date.
        sorted_metadata = sorted(metadata, key=itemgetter('Date'))
        # choose the last item, which should be most recent.
        item = sorted_metadata[-1]
        
        self.env["url"] = item["Location"]
        self.env["pkg_name"] = item["Payload"]
        self.output("Found URL %s" % self.env["url"])
        self.output("Got update: '%s'" % item["Title"])
        # now extract useful info from the rest of the metadata that could
        # be used in a pkginfo
        pkginfo = {}
        pkginfo["description"] = "<html>%s</html>" % item["Short Description"]
        pkginfo["display_name"] = item["Title"]
        max_os = self.valueToOSVersionString(item['Max OS'])
        min_os = self.valueToOSVersionString(item['Min OS'])
        if max_os != "0.0.0":
            pkginfo["maximum_os_version"] = max_os
        if min_os != "0.0.0":
            pkginfo["minimum_os_version"] = min_os
        installs_items = self.getInstallsItems(item)
        if installs_items:
            pkginfo["installs"] = installs_items

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
