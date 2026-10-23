#!/usr/local/autopkg/python
# -*- coding: utf-8 -*-
#
#  Copyright (c) 2015, Facebook, Inc.
#  All rights reserved.
#
#  This source code is licensed under the BSD-style license found in the
#  LICENSE file in the root directory of this source tree. An additional grant
#  of patent rights can be found in the PATENTS file in the same directory.
#
# A copy of Shared_Processors/Rsync.py from autopkg/nmcspadden-recipes, by Nick McSpadden
# (nmcspadden), at commit 355a14dd702ebf28834d56ed20b849ac0abf634b (2022-08-22). The header
# above is upstream's; GitHub reports that repo's LICENSE as Apache-2.0. The upstream copy
# raises NameError on Python 3 (`basestring`) and judges success by stderr rather than the exit
# code; this copy fixes both. Delete it, and point recipes at com.github.nmcspadden.shared/Rsync,
# once the fix is merged there.
"""See docstring for Rsync class."""

import subprocess
from typing import Any, Dict

# pyright: reportMissingImports=false
from autopkglib import (  # ty: ignore[unresolved-import]  # pylint: disable=import-error
    Processor,
    ProcessorError,
)

__all__ = ["Rsync"]


class Rsync(Processor):
    """Rsyncs a path to another path."""

    description = __doc__
    input_variables = {
        "source_path": {
            "required": True,
            "description": ("Path to file or directory to copy from."),
        },
        "destination_path": {
            "required": True,
            "description": ("Path to file or directory to copy to."),
        },
        "rsync_arguments": {
            "required": False,
            "description": ("List of arguments passed to rsync directly."),
        },
        "rsync_path": {
            "required": False,
            "description": ("Custom path to rsync. Defaults to /usr/bin/rsync."),
        },
    }
    output_variables: Dict[str, Any] = {}

    __doc__ = description

    def main(self) -> None:
        rsync_location = self.env.get("rsync_path", "/usr/bin/rsync")
        rsync_args = self.env.get("rsync_arguments", [])
        if isinstance(rsync_args, str):
            raise ProcessorError("rsync_args must be a list!")
        cmd = [rsync_location]
        if rsync_args:
            cmd.extend(rsync_args)
        cmd.extend([self.env["source_path"], self.env["destination_path"]])
        try:
            completed = subprocess.run(cmd, capture_output=True, check=False, text=True)
        except OSError as rsync_error:
            raise ProcessorError(f"Running {rsync_location} failed: {rsync_error}") from rsync_error
        # rsync reports failure through its exit status; stderr can also carry warnings.
        if completed.returncode:
            raise ProcessorError(f"rsync exited {completed.returncode}: {completed.stderr.strip()}")
        if completed.stderr.strip():
            self.output(completed.stderr.strip())
        if completed.stdout.strip():
            self.output(completed.stdout.strip())


if __name__ == "__main__":
    PROCESSOR = Rsync()
    PROCESSOR.execute_shell()
