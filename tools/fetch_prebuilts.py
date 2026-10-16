#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fetch immutable build inputs and verify their bytes before making them available."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def entries(root=ROOT):
    data = json.loads((root / "prebuilts.lock.json").read_text())
    if data.get("schema") != 1:
        raise ValueError("Unsupported prebuilt lock format")
    seen = set()
    for item in data["artifacts"]:
        destination = Path(item["destination"])
        source = Path(item["path"])
        if (destination.is_absolute() or ".." in destination.parts
                or len(destination.parts) < 2 or destination.parts[0] != "proprietary"):
            raise ValueError("Unsafe prebuilt destination")
        if source.is_absolute() or ".." in source.parts or not source.parts:
            raise ValueError("Unsafe prebuilt source path")
        if destination in seen:
            raise ValueError("Duplicate prebuilt destination")
        seen.add(destination)
        if (not re.fullmatch(r"[0-9a-f]{40}", item["revision"])
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                or not isinstance(item["size"], int) or item["size"] <= 0):
            raise ValueError("Prebuilt must be pinned to a commit and SHA-256")
    return data["artifacts"]


def verify(item, path):
    return path.is_file() and path.stat().st_size == item["size"] and digest(path) == item["sha256"]


def verify_all(root=ROOT):
    for item in entries(root):
        if not verify(item, root / item["destination"]):
            raise ValueError("Missing or changed prebuilt: " + item["destination"])


def fetch(root=ROOT, offline=None, verify_only=False):
    offline = offline or {}
    for item in entries(root):
        path = root / item["destination"]
        if verify(item, path):
            print("Verified:", item["destination"])
            continue
        if verify_only:
            raise ValueError("Missing or changed prebuilt: " + item["destination"])
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".download-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                if item["repository"] in offline:
                    source = offline[item["repository"]] / item["path"]
                    with source.open("rb") as stream:
                        shutil.copyfileobj(stream, output, 1024 * 1024)
                else:
                    quoted = urllib.parse.quote(item["path"], safe="/")
                    url = f"https://raw.githubusercontent.com/{item['repository']}/{item['revision']}/{quoted}"
                    request = urllib.request.Request(url, headers={"User-Agent": "Floral-build-plugin/1"})
                    with urllib.request.urlopen(request, timeout=30) as stream:
                        shutil.copyfileobj(stream, output, 1024 * 1024)
            staged = Path(temporary)
            if not verify(item, staged):
                raise ValueError("Downloaded bytes do not match the lock: " + item["destination"])
            os.replace(staged, path)
            print("Fetched and verified:", item["destination"])
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    verify_all(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--source", action="append", default=[], metavar="OWNER/REPO=PATH",
                        help="Use a checked-out repository instead of downloading its inputs")
    args = parser.parse_args()
    try:
        offline = {}
        for value in args.source:
            repository, folder = value.split("=", 1)
            offline[repository] = Path(folder).resolve()
        fetch(offline=offline, verify_only=args.verify_only)
    except (OSError, ValueError) as error:
        print("Prebuilt setup:", error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
