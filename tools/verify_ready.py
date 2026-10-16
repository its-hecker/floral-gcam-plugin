#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read-only preflight: verify cached bytes and detect changed base configuration."""
import json
from pathlib import Path
import sys
from fetch_prebuilts import ROOT, digest, verify_all
from integration import policy_files, vendor_inputs


def verify(root=ROOT):
    verify_all(root)
    profile = json.loads((root / "generated/profile.json").read_text())
    if profile.get("schema") != 1 or profile.get("device") not in {"coral", "flame"}:
        raise ValueError("Unsupported setup profile; rerun tools/prepare.py")
    if profile.get("plugin_root") != str(root.resolve()):
        raise ValueError("Plugin moved to another checkout; rerun tools/prepare.py")
    current = [str(p) for p in policy_files([Path(p) for p in profile["policy_dirs"]])]
    if current != profile["policy_files"]:
        raise ValueError("Selected policy file set changed; rerun tools/prepare.py")
    if [str(p) for p in vendor_inputs(Path(profile["vendor_tree"]))] != profile["vendor_blueprints"]:
        raise ValueError("Vendor Blueprint file set changed; rerun tools/prepare.py")
    for item in profile["input_files"]:
        if digest(Path(item["path"])) != item["sha256"]:
            raise ValueError("Prepared base/input changed; rerun tools/prepare.py: " + item["path"])
    for item in profile["output_files"]:
        path = Path(item["path"])
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Invalid generated output path")
        if digest(root / path) != item["sha256"]:
            raise ValueError("Generated output changed; rerun tools/prepare.py: " + str(path))
    return profile


def main():
    try:
        verify()
        print("ready")
    except (OSError, ValueError) as error:
        print("Floral GCam setup:", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
