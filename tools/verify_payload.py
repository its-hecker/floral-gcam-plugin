#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify the original Google-signed base and every required feature split."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "GoogleCameraCoral.apks."


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def selected_files(lock):
    # These six asset modules are specific to Tensor Pixel 6/7 hardware.
    return [n for n in lock["files"] if not any(
        token in n for token in ("_p21.apk", "_p22.apk", "_cheetah_"))]


def varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def field(number, value):
    if isinstance(value, str):
        value = value.encode()
    return varint(number * 8 + 2) + varint(len(value)) + value


def archive_name(name):
    if name == "base.apk":
        return "splits/base-master.apk"
    return "splits/base-" + name.removeprefix("split_")


def table_of_contents(names, package):
    # AOSP extract_apks supports one install group. All feature APKs belong
    # to that group; their original manifest split names remain untouched.
    # Wire fields: BuildApksResult.variant=1, package_name=4;
    # Variant.apk_set=2; ApkSet.module_metadata=1, apk_description=2.
    # See build/soong/cmd/extract_apks/bundle_proto/commands.proto.
    metadata = field(1, "base") + varint(6 * 8) + varint(1)  # INSTALL_TIME
    apk_set = field(1, metadata)
    for name in names:
        split_id = "" if name == "base.apk" else name[6:-4]
        split = field(1, split_id) + varint(2 * 8) + varint(1)
        description = field(2, archive_name(name)) + field(3, split)
        apk_set += field(2, description)
    return field(1, field(2, apk_set)) + field(4, package)


def verify_apk(path, name, lock, aapt2, apksigner):
    expected = lock["files"][name]
    if path.stat().st_size != expected["size"] or digest(path) != expected["sha256"]:
        raise ValueError(f"APK bytes differ from the pinned Google release: {name}")
    if aapt2 is None and apksigner is None:
        print(f"Verified pinned APK SHA-256: {name}", flush=True)
        return
    result = subprocess.run(
        [apksigner, "verify", "--print-certs", str(path)],
        text=True, capture_output=True, check=True)
    signers = re.findall(r"^Signer #\d+ certificate SHA-256 digest: (\w+)$",
                         result.stdout, re.M)
    if signers != [lock["signer_sha256"]]:
        raise ValueError(f"Unexpected signing certificate: {name}: {signers}")
    result = subprocess.run([aapt2, "dump", "badging", str(path)],
                            text=True, capture_output=True, check=True)
    line = result.stdout.splitlines()[0]
    if (f"name='{lock['package']}'" not in line or
            f"versionCode='{lock['version_code']}'" not in line):
        raise ValueError(f"Wrong APK package/version: {name}: {line}")
    if name == "base.apk":
        if f"versionName='{lock['version_name']}'" not in line or " split=" in line:
            raise ValueError("Incorrect base APK")
    elif f"split='{name[6:-4]}'" not in line:
        raise ValueError(f"Incorrect split manifest: {name}")
    with zipfile.ZipFile(path) as apk:
        if apk.testzip() is not None:
            raise ValueError(f"Corrupt APK: {name}")
        if name == "split_startup_jni_split_module.apk":
            if "lib/arm64-v8a/libgcastartup.so" not in apk.namelist():
                raise ValueError("Missing camera startup native library")
    print(f"Verified Google signature, manifest and SHA-256: {name}", flush=True)


def check_payload(destination, lock, tools, payload_path=None):
    payload = json.loads((payload_path or ROOT / "provenance/payload.json").read_text())
    if payload["files"] != selected_files(lock):
        raise ValueError("Incorrect camera split selection")
    chunks = payload["chunks"]
    if set(chunks) != {p.name for p in destination.glob(PREFIX + "*")}:
        raise ValueError("Payload source chunks are missing or unexpected")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        archive = tmp / "GoogleCameraCoral.apks"
        with archive.open("wb") as target:
            for index, (name, expected) in enumerate(sorted(chunks.items())):
                path = destination / name
                if (name != PREFIX + f"{index:02d}" or
                        path.stat().st_size != expected["size"] or
                        digest(path) != expected["sha256"]):
                    raise ValueError(f"Damaged/incorrect source chunk: {name}")
                with path.open("rb") as stream:
                    shutil.copyfileobj(stream, target)
        if digest(archive) != payload["archive_sha256"]:
            raise ValueError("APK set SHA-256 mismatch")
        with zipfile.ZipFile(archive) as bundle:
            expected = {archive_name(n) for n in payload["files"]} | {"toc.pb"}
            if set(bundle.namelist()) != expected:
                raise ValueError("APK set has missing/extra entries")
            if bundle.read("toc.pb") != table_of_contents(payload["files"], lock["package"]):
                raise ValueError("APK set table of contents differs from the install group")
            for name in payload["files"]:
                path = tmp / name
                with bundle.open(archive_name(name)) as src, path.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                verify_apk(path, name, lock, *tools)
        print(f"PASS: Camera {lock['version_name']}, base + {len(payload['files'])-1} splits", flush=True)



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-hashes", action="store_true", help="Verify exact bytes without Android SDK tools")
    parser.add_argument("--aapt2", default="aapt2")
    parser.add_argument("--apksigner", default="apksigner")
    args = parser.parse_args()
    try:
        lock = json.loads((ROOT / "provenance/source.json").read_text())
        check_payload(ROOT / "proprietary", lock, (None, None) if args.check_hashes else (args.aapt2, args.apksigner))
    except (OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print("Camera payload:", error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
