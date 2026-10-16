#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise setup with immutable real vendor inputs and the tested Floral policy."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from integration import PACKAGE, modules, static_array
from prepare import prepare
from verify_ready import verify

DEVICE_REV = "1316c7fe6ef5bededcc542144768d56da202c114"
VENDOR_REV = "d1409c6066f977639a99d276709e3a70e7a9b3c0"
SOURCES = [
    ("infinity_cnb_android_device_google_coral", DEVICE_REV, "device/google/coral", [
        "sepolicy/public/google_camera_app.te",
        "sepolicy/private/google_camera_app.te",
        "sepolicy/private/seapp_contexts",
        "sepolicy/private/mac_permissions.xml",
        "sepolicy/vendor/google/google_camera_app.te",
        "sepolicy/vendor/google/mac_permissions.xml",
        "sepolicy/vendor/google/keys.conf",
        "sepolicy/vendor/google/certs/app.x509.pem",
        "sepolicy/vendor/google/certs/pulse-release.x509.pem",
        "sepolicy/vendor/google/certs/com_google_mds.x509.pem",
    ]),
    ("infinity_cnb_proprietary_vendor_google_coral", VENDOR_REV, "vendor/google/coral", [
        "Android.bp",
        "proprietary/vendor/framework/com.google.android.camera.experimental2019.jar",
        "proprietary/vendor/etc/permissions/com.google.android.camera.experimental2019.xml",
        "proprietary/product/app/PixelCameraServicesCoral/PixelCameraServicesCoral.apk",
    ]),
]


def main():
    with tempfile.TemporaryDirectory(prefix="floral-gcam-reference-") as folder:
        rom = Path(folder)
        tasks = [(repository, revision, destination, path)
                 for repository, revision, destination, paths in SOURCES for path in paths]

        def download(item):
            repository, revision, destination, path = item
            url = f"https://raw.githubusercontent.com/its-hecker/{repository}/{revision}/{path}"
            output = rom / destination / path
            output.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(url, timeout=45) as response:
                output.write_bytes(response.read())

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(download, tasks))
        plugin = rom / "vendor/hecker/floral/gcam"
        shutil.copytree(ROOT, plugin,
                        ignore=shutil.ignore_patterns(".git", "generated", "Android.bp", "__pycache__"))
        policy = rom / "device/google/coral/sepolicy"
        directories = [policy / "public", policy / "private", policy / "vendor/google"]

        def check(device, add_signer):
            profile = prepare(rom, device, rom / "vendor/google/coral", directories, root=plugin)
            assert profile["policy"]["add_signer"] == add_signer
            verify(plugin)
            app = [m for m in modules(plugin / "Android.bp") if m["name"] == "FloralGoogleCamera"]
            assert len(app) == 1 and app[0]["kind"] == "android_app_set"
            assert {"Aperture", "GoogleCameraCoral"}.issubset(
                static_array(app[0]["body"], "overrides"))
            makefile = (
                f"PRODUCT_DEVICE := {device}\nTARGET_ARCH := arm64\nTARGET_COPY_OUT_PRODUCT := product\n"
                "PRODUCT_PACKAGES := Aperture GoogleCameraCoral OtherApp\n"
                "include vendor/hecker/floral/gcam/gcam.mk\n"
                "include vendor/hecker/floral/gcam/BoardConfigGCam.mk\n"
                "all:\n\t@echo $(PRODUCT_PACKAGES)\n"
                "\t@echo $(PRODUCT_SOONG_NAMESPACES)\n"
                "\t@echo $(PRODUCT_PRIVATE_SEPOLICY_DIRS)\n"
            )
            result = subprocess.run(["make", "--no-print-directory", "-f", "-", "all"],
                                    input=makefile, text=True, capture_output=True, cwd=rom, check=True)
            selected = result.stdout.splitlines()[0].split()
            assert "FloralGoogleCamera" in selected and "OtherApp" in selected
            assert "PixelCameraServicesCoral" in selected
            assert "com.google.android.camera.experimental2019" in selected
            assert "Aperture" not in selected and "GoogleCameraCoral" not in selected
            assert "vendor/google/coral" in result.stdout
            print(f"PASS: real base setup/product parsing for {device}; add signer={add_signer}", flush=True)

        # The real base has both @GOOGLE and a package-specific Camera mapping.
        check("coral", False)
        check("flame", False)
        # Exercise an older base without either Google Camera signer mapping.
        private = policy / "private/mac_permissions.xml"
        tree = ET.parse(private)
        for signer in list(tree.getroot()):
            if signer.find(f"package[@name='{PACKAGE}']") is not None:
                tree.getroot().remove(signer)
        tree.write(private, encoding="unicode")
        vendor = policy / "vendor/google/mac_permissions.xml"
        tree = ET.parse(vendor)
        for signer in list(tree.getroot()):
            if signer.get("signature") == "@GOOGLE":
                tree.getroot().remove(signer)
        tree.write(vendor, encoding="unicode")
        check("coral", True)
        generated = ET.parse(plugin / "generated/sepolicy/mac_permissions.xml").getroot()
        assert len(generated.findall("signer")) == 1
        assert generated.find("signer/seinfo") is None
        assert generated.find("signer/package").get("name") == PACKAGE
        print("PASS: package-only Google certificate binding; base hardware policy retained", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
