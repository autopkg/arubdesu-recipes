#!/usr/bin/env python
#
# Copyright 2014 Allister Banks - See bottom for license
#
import datetime
import os.path
import subprocess
from Foundation import NSDate

import FoundationPlist
from autopkglib import Processor, ProcessorError, get_pref

__all__ = ["MunkiAutoPromoter"]

class MunkiAutoPromoter(Processor):
    """Checks if pkginfo creation date is greater than 'cooling-off' period,
    adds next catalog to array on specific day of week."""
    input_variables = {
        "release_catalog": {
            "required": False,
            "description": ("Catalog where products approved for 'mass consumption' "
                "get released to. Defaults to 'production'."),
        },
        "days_until_catalog_added": {
            "required": False,
            "description": ("Integer number of days since pkginfo is created, "
                "after which product is 'released'."),
        },
        "allowed_weekdays_for_promotion": {
            "required": False,
            "description": ("Day of week on which it's considered 'safe' to "
                "promote items. Defaults to Wed, others: Sun, Mon, Tue, Thu, Fri, Sat."),
        },
        "testing_catalog": {
            "required": False,
            "description": ("Optional additional catalog for initial testing"),
        },
        "days_until_test": {
            "required": False,
            "description": ("Integer number of days since pkginfo is created, "
                "after which product is sent to wider range of testers. Defaults to 4 if testing_catalog present"),
        },
        "allowed_weekdays_for_test": {
            "required": False,
            "description": ("Day of week on which it's considered 'safe' to "
                "promote items. Defaults to Tue, Wed, Thurs if testing_catalog present "),
        },
    }
    output_variables = {
        "makecatalogs_resultcode": {
            "description": "Result code from the makecatalogs operation.",
        },
        "makecatalogs_stderr": {
            "description": "Error output (if any) from makecatalogs.",
        },
    }
    description = __doc__

    def rebuildCatalogs(self):
        '''Does actual MakeCatalogs if pkginfo added/modified'''
        args = ["/usr/local/munki/makecatalogs", 
                self.env["MUNKI_REPO"]]
        # Call makecatalogs.
        try:
            proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            (unused_output, err_out) = proc.communicate()
        except OSError as err:
            raise ProcessorError(
                "makecatalog execution failed with error code %d: %s"
                % (err.errno, err.strerror))

        self.env["makecatalogs_resultcode"] = proc.returncode
        self.env["makecatalogs_stderr"] = err_out
        if proc.returncode != 0:
            raise ProcessorError("makecatalogs failed: %s" % err_out)
        else:
            self.output("Munki catalogs rebuilt!")

    def main(self):
        # Opening code lifted directly from MakeCatalogs processor
        cache_dir = get_pref("CACHE_DIR") or os.path.expanduser(
                    "~/Library/AutoPkg/Cache")
        current_run_results_plist = os.path.join(
                                        cache_dir, "autopkg_results.plist")
        try:
            run_results = FoundationPlist.readPlist(current_run_results_plist)
        except IOError:
            run_results = []        
        repo_modified = False
        for result in run_results:
            for item in result:
                if item.get("Processor") == "MunkiImporter":
                    if item["Output"].get("pkginfo_repo_path"):
                        repo_modified = True
                        break
                    else:
                        path_to_pkg = item["Output"].get("pkg_repo_path")
                        pkginfo_path_with_ext = path_to_pkg.replace('pkgs', 'pkgsinfo', 1)
                        pkginfo_path_minus = pkginfo_path_with_ext.rsplit( ".", 1 )[ 0 ]
                        extension = "plist"
                        if item["Input"].get("MUNKI_PKGINFO_FILE_EXTENSION"):
                            extension = item["Input"].get("MUNKI_PKGINFO_FILE_EXTENSION").strip(".")
                        pkginfo_path = "%s.%s" % (pkginfo_path_minus, extension)
                        try:
                            pkginfo_dict = FoundationPlist.readPlist(pkginfo_path)
                        except:
                            raise ProcessorError("Could not read plist from %s"
                                                  % (pkginfo_path))#, err.strerror))
                        track = 'release'
                        catalog_to_promote_to = 'production'
                        prod_prom_int = 7
                        promote_weekday = ['Wed']
                        if not pkginfo_dict.get("autopromote"):
                            autopromote_stanza_present = False
                            if self.env.get("release_catalog"):
                                catalog_to_promote_to = self.env["release_catalog"]
                            if self.env.get("days_until_catalog_added"):
                                prod_prom_int = self.env["days_until_catalog_added"]
                            if self.env.get("allowed_weekdays_for_promotion"):
                                promote_weekday = self.env["allowed_weekdays_for_promotion"]
                            creation_date_full = str(pkginfo_dict['_metadata']['creation_date'])
                            created_date_obj = datetime.datetime.strptime(creation_date_full[:10], '%Y-%m-%d')
                            promote_date = created_date_obj + datetime.timedelta(days=prod_prom_int)
                            pkginfo_dict['autopromote'] = {
                                track : {
                                    'catalog_name': catalog_to_promote_to,
                                    'days_until_catalog_added': prod_prom_int,
                                    'allowed_weekdays_for_promotion': promote_weekday,
                                    'prod_sched_promotion_date': promote_date,
                                }
                            }
                            if self.env.get("testing_catalog"):
                                catalog_to_promote_to = self.env["testing_catalog"]
                                prod_prom_int = 4
                                if self.env.get("days_until_test"):
                                    prod_prom_int = self.env["days_until_test"]
                                promote_weekday = ['Tue', 'Wed', 'Thu']
                                if self.env.get("allowed_weekdays_for_test"):
                                    promote_weekday = self.env["allowed_weekdays_for_test"]
                                    promote_date = created_date_obj + datetime.timedelta(days=prod_prom_int)
                                    track = 'testing'
                                    pkginfo_dict['autopromote'][track] = {
                                        'catalog_name': catalog_to_promote_to,
                                        'days_until_catalog_added': prod_prom_int,
                                        'allowed_weekdays_for_promotion': promote_weekday,
                                        'prod_sched_promotion_date': promote_date,
                                    }
                        else:
                            autopromote_stanza_present = True
                        today_datetime = datetime.datetime.now()
                        today_date_obj = NSDate.new()
                        if self.env.get("testing_catalog"):
                            tracks = ['testing', 'release']
                        else:
                            tracks = ['release']
                        for track in tracks:
                            self.output("Processing track %s" % track)
                            if today_datetime.strftime('%a') in pkginfo_dict['autopromote'][track]['allowed_weekdays_for_promotion']:
                                if today_date_obj > pkginfo_dict['autopromote'][track]['prod_sched_promotion_date']:
                                    catalog_to_promote_to = pkginfo_dict['autopromote'][track]['catalog_name']
                                    if not catalog_to_promote_to in pkginfo_dict['catalogs']:
                                        pkginfo_dict['autopromote'][track]['actual_promote_date'] = today_date_obj
                                        pkginfo_dict['catalogs'].append(catalog_to_promote_to)
                                        FoundationPlist.writePlist(pkginfo_dict, pkginfo_path)
                                        autopromote_stanza_present = True
                                        repo_modified = True
                                    else:
                                        self.output("Already promoted to %s on %s" % (catalog_to_promote_to, pkginfo_dict['autopromote'][track]['actual_promote_date']))
                                else:
                                    if autopromote_stanza_present:
                                        promote_date = pkginfo_dict['autopromote'][track]['prod_sched_promotion_date']
                                    self.output("Will promote to %s after %s" % (track, promote_date))
                            else:
                                self.output("Today's not a promotion weekday for %s, skipping" % track)
                            if not autopromote_stanza_present:
                                FoundationPlist.writePlist(pkginfo_dict, pkginfo_path)
                            if repo_modified:
                                self.rebuildCatalogs()

if __name__ == "__main__":
    processor = MunkiAutoPromoter()
    processor.execute_shell()

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