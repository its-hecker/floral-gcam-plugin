# SPDX-License-Identifier: Apache-2.0
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from fetch_prebuilts import entries, fetch
from integration import PACKAGE, certificate
from prepare import prepare
from verify_ready import verify


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.rom = Path(self.temp.name) / "rom"
        self.plugin = self.rom / "vendor/hecker/floral/gcam"
        shutil.copytree(ROOT, self.plugin,
                        ignore=shutil.ignore_patterns(".git", "proprietary", "generated",
                                                     "Android.bp", "__pycache__"))
        # Tiny cache fixtures isolate setup logic; CI separately verifies real APKs.
        lock = json.loads((self.plugin / "prebuilts.lock.json").read_text())
        for index, artifact in enumerate(lock["artifacts"]):
            path = self.plugin / artifact["destination"]
            path.parent.mkdir(parents=True, exist_ok=True)
            content = f"fixture chunk {index}".encode()
            path.write_bytes(content)
            artifact["size"] = len(content)
            artifact["sha256"] = hashlib.sha256(content).hexdigest()
        (self.plugin / "prebuilts.lock.json").write_text(json.dumps(lock))
        self.vendor = self.rom / "vendor/another/floral"
        self.vendor.mkdir(parents=True)
        (self.vendor / "Android.bp").write_text('''
soong_namespace {}
dex_import {
    name: "com.google.android.camera.experimental2019",
    jars: ["proprietary/vendor/framework/com.google.android.camera.experimental2019.jar"],
    soc_specific: true,
}
android_app_import {
    name: "OtherPixelCameraServices",
    apk: "proprietary/product/app/PixelCameraServices/PixelCameraServices.apk",
    preprocessed: true,
    product_specific: true,
}
''')
        self.jar = self.vendor / "proprietary/vendor/framework/com.google.android.camera.experimental2019.jar"
        self.jar.parent.mkdir(parents=True)
        with zipfile.ZipFile(self.jar, "w") as archive:
            archive.writestr("classes.dex", b"dex fixture")
        apk = self.vendor / "proprietary/product/app/PixelCameraServices/PixelCameraServices.apk"
        apk.parent.mkdir(parents=True)
        with zipfile.ZipFile(apk, "w") as archive:
            archive.writestr("AndroidManifest.xml", b"manifest fixture")
        xml = self.vendor / "proprietary/vendor/etc/permissions/com.google.android.camera.experimental2019.xml"
        xml.parent.mkdir(parents=True)
        xml.write_text('<permissions><library name="com.google.android.camera.experimental2019" '
                       'file="/vendor/framework/com.google.android.camera.experimental2019.jar"/></permissions>')
        self.policy = self.rom / "device/another/floral/sepolicy"
        self.policy.mkdir(parents=True)
        (self.policy / "camera.te").write_text("""
type google_camera_app, coredomain, domain;
app_domain(google_camera_app)
allow google_camera_app qdsp_device:chr_file r_file_perms;
allow google_camera_app airbrush_device:chr_file rw_file_perms;
allow google_camera_app hal_airbrush_hwservice:hwservice_manager find;
""")
        self.seapp = self.policy / "seapp_contexts"
        self.seapp.write_text("user=_app seinfo=google name=" + PACKAGE +
                             " domain=google_camera_app type=app_data_file levelFrom=all\n")
        self.mac = self.policy / "mac_permissions.xml"
        self.mac.write_text("<policy/>")
        self.der = certificate(self.plugin / "configs/GoogleCamera.x509.pem")

    def run_prepare(self, **kwargs):
        return prepare(self.rom, kwargs.pop("device", "coral"), self.vendor,
                       [self.policy], root=self.plugin, check_apks=False, **kwargs)

    def signer(self, label="google", package=True, signature=None):
        root = ET.Element("policy")
        signer = ET.SubElement(root, "signer", signature=signature or self.der.hex())
        parent = ET.SubElement(signer, "package", name=PACKAGE) if package else signer
        ET.SubElement(parent, "seinfo", value=label)
        self.mac.write_text(ET.tostring(root, encoding="unicode"))

    def make(self, device="coral"):
        script = (
            f"PRODUCT_DEVICE := {device}\nTARGET_ARCH := arm64\nTARGET_COPY_OUT_PRODUCT := product\n"
            "PRODUCT_PACKAGES := Aperture GoogleCameraCoral OtherApp\n"
            f"include {self.plugin}/gcam.mk\ninclude {self.plugin}/BoardConfigGCam.mk\n"
            "all:\n\t@echo PACKAGES=$(PRODUCT_PACKAGES)\n"
            "\t@echo NAMESPACES=$(PRODUCT_SOONG_NAMESPACES)\n"
            "\t@echo POLICY=$(PRODUCT_PRIVATE_SEPOLICY_DIRS)\n"
        )
        return subprocess.run(["make", "--no-print-directory", "-f", "-", "all"],
                              input=script, text=True, capture_output=True, cwd=self.rom)

    def test_new_binding_is_certificate_and_package_scoped(self):
        profile = self.run_prepare(replace_modules=["AnotherCamera"])
        self.assertTrue(profile["policy"]["add_signer"])
        xml = ET.parse(self.plugin / "generated/sepolicy/mac_permissions.xml").getroot()
        signer = xml.find("signer")
        self.assertEqual(signer.get("signature"), self.der.hex())
        self.assertIsNone(signer.find("seinfo"))
        self.assertEqual(signer.find("package").get("name"), PACKAGE)
        self.assertEqual(signer.find("package/seinfo").get("value"), "google")
        self.assertEqual(verify(self.plugin)["device"], "coral")
        blueprint = (self.plugin / "Android.bp").read_text()
        self.assertIn('"AnotherCamera"', blueprint)
        self.assertNotIn("certificate: \"platform\"", blueprint)
        self.assertNotIn("privileged: true", blueprint)
        result = self.make()
        self.assertEqual(result.returncode, 0, result.stderr)
        packages = result.stdout.splitlines()[0]
        self.assertIn("FloralGoogleCamera", packages)
        self.assertIn("OtherPixelCameraServices", packages)
        self.assertIn("OtherApp", packages)
        self.assertNotIn("GoogleCameraCoral", packages)
        self.assertNotIn("Aperture", packages)
        self.assertIn("vendor/another/floral", result.stdout)
        self.assertIn("/generated/sepolicy", result.stdout)

    def test_existing_scoped_or_global_google_binding_is_reused(self):
        for package in [True, False]:
            with self.subTest(package=package):
                self.signer(package=package)
                profile = self.run_prepare()
                self.assertFalse(profile["policy"]["add_signer"])
                xml = ET.parse(self.plugin / "generated/sepolicy/mac_permissions.xml").getroot()
                self.assertEqual(len(xml), 0)
                self.assertEqual(verify(self.plugin)["policy"]["signer_source"], str(self.mac))
                result = self.make()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("POLICY=\n", result.stdout)

    def test_google_alias_requires_the_actual_public_certificate(self):
        self.signer(signature="@GOOGLE_CAMERA")
        with self.assertRaisesRegex(ValueError, "Resolve"):
            self.run_prepare()
        profile = self.run_prepare(aliases={"GOOGLE_CAMERA": self.plugin / "configs/GoogleCamera.x509.pem"})
        self.assertFalse(profile["policy"]["add_signer"])

    def test_literal_keys_conf_alias_and_scoped_over_global_precedence(self):
        self.signer()
        extra = self.policy / "vendor"
        extra.mkdir()
        cert_path = extra / "app.x509.pem"
        shutil.copy(self.plugin / "configs/GoogleCamera.x509.pem", cert_path)
        (extra / "keys.conf").write_text("[@GOOGLE]\nALL : " +
                                          str(cert_path.relative_to(self.rom)) + "\n")
        (extra / "mac_permissions.xml").write_text(
            '<policy><signer signature="@GOOGLE"><seinfo value="other"/></signer></policy>')
        profile = self.run_prepare()
        self.assertFalse(profile["policy"]["add_signer"])
        self.assertEqual(profile["policy"]["signer_source"], str(self.mac))
        self.assertTrue(any(p["path"] == str(cert_path) for p in profile["input_files"]))
        cert_path.write_bytes(b"changed public certificate")
        with self.assertRaisesRegex(ValueError, "input changed"):
            verify(self.plugin)

    def test_disjoint_package_signer_does_not_block_a_camera_only_binding(self):
        self.signer()
        self.mac.write_text(self.mac.read_text().replace(PACKAGE, "com.google.another"))
        profile = self.run_prepare()
        self.assertTrue(profile["policy"]["add_signer"])

    def test_conflicting_signer_or_seapp_mapping_is_refused_without_writes(self):
        self.signer(label="platform")
        with self.assertRaisesRegex(ValueError, "seinfo=google"):
            self.run_prepare()
        self.assertFalse((self.plugin / "generated/profile.json").exists())
        self.mac.write_text("<policy/>")
        self.seapp.write_text(self.seapp.read_text().replace("domain=google_camera_app", "domain=platform_app"))
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.run_prepare()
        self.assertFalse((self.plugin / "Android.bp").exists())

    def test_duplicate_selected_policy_is_refused(self):
        (self.policy / "other").mkdir()
        shutil.copy(self.seapp, self.policy / "other/seapp_contexts")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.run_prepare()

    def test_flame_uses_selected_base_and_rejects_a_different_product(self):
        self.run_prepare(device="flame")
        self.assertEqual(self.make(device="flame").returncode, 0)
        result = self.make(device="coral")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("prepared for flame", result.stderr)

    def test_vendor_native_and_services_inputs_must_exist(self):
        self.jar.unlink()
        with self.assertRaisesRegex(ValueError, "JAR"):
            self.run_prepare()

    def test_new_policy_files_and_changed_vendor_inputs_invalidate_preparation(self):
        self.run_prepare()
        extra = self.policy / "new.te"
        extra.write_text("# newly selected policy\n")
        with self.assertRaisesRegex(ValueError, "file set changed"):
            verify(self.plugin)
        extra.unlink()
        self.jar.write_bytes(b"changed library")
        with self.assertRaisesRegex(ValueError, "input changed"):
            verify(self.plugin)

    def test_generated_metadata_and_relocated_profiles_are_rejected(self):
        self.run_prepare()
        path = self.plugin / "generated/config.mk"
        path.write_text(path.read_text() + "# changed\n")
        with self.assertRaisesRegex(ValueError, "Generated output changed"):
            verify(self.plugin)
        self.run_prepare()
        profile_path = self.plugin / "generated/profile.json"
        profile = json.loads(profile_path.read_text())
        profile["plugin_root"] = str(self.rom / "elsewhere")
        profile_path.write_text(json.dumps(profile))
        with self.assertRaisesRegex(ValueError, "another checkout"):
            verify(self.plugin)

    def test_partial_fetch_never_replaces_a_valid_cache_with_corrupt_bytes(self):
        lock_path = self.plugin / "prebuilts.lock.json"
        artifact = json.loads(lock_path.read_text())["artifacts"][0]
        destination = self.plugin / artifact["destination"]
        destination.write_bytes(b"old cache")
        source = self.rom / "offline-source" / artifact["path"]
        source.parent.mkdir(parents=True)
        source.write_bytes(b"wrong download")
        with self.assertRaisesRegex(ValueError, "do not match"):
            fetch(self.plugin, {artifact["repository"]: self.rom / "offline-source"})
        self.assertEqual(destination.read_bytes(), b"old cache")
        self.assertFalse(list(destination.parent.glob(".download-*")))

    def test_lock_traversal_and_floating_revisions_are_refused(self):
        lock_path = self.plugin / "prebuilts.lock.json"
        lock = json.loads(lock_path.read_text())
        lock["artifacts"][0]["destination"] = "../outside"
        lock_path.write_text(json.dumps(lock))
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            entries(self.plugin)
        lock["artifacts"][0]["destination"] = "proprietary/test"
        lock["artifacts"][0]["revision"] = "main"
        lock_path.write_text(json.dumps(lock))
        with self.assertRaisesRegex(ValueError, "pinned"):
            entries(self.plugin)


if __name__ == "__main__":
    unittest.main()
