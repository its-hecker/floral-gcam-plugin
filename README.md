# Floral GCam plugin — Android 17

Reusable stock Google Camera build integration for **Pixel 4 (flame)** and
**Pixel 4 XL (coral)**. Add this repository to an existing ROM checkout, prepare
it against that ROM's selected Floral vendor and SELinux policy, then inherit
the product and board files.

Maintainer: **Hecker** — https://github.com/its-hecker

## Camera payload

- Google Camera **8.7.250.494820638.44** (version code `65822832`).
- Package `com.google.android.GoogleCamera`, retaining Google's original signature.
- Untouched base APK plus nine required/common feature splits.
- Tensor-specific `p21`, `p22` and `cheetah` feature modules excluded.
- Installation under `/product/app/FloralGoogleCamera/` using `android_app_set`.

The payload is the same one confirmed working on Coral in the experimental
Infinity-X build on **2026-10-16**, before promotion to stable. See
[immutable APK provenance](provenance/source.json) and the
[archive/chunk fingerprints](provenance/payload.json). Neither the APKs nor
their manifests, native code or signatures are changed.

This is a **ROM source plugin**. It is built into the ROM; this repository
does not provide a recovery-flashable ZIP or a Magisk module.

## Prerequisites

- A working Android 17 Floral device/vendor/kernel base with its real camera
  HAL, Hexagon/Airbrush services, firmware and device calibration.
- The base's `google_camera_app` domain, its GoogleCamera `seapp_contexts`
  mapping, and the existing Airbrush/DSP access policy. Setup validates the
  selected roots instead of duplicating these hardware policies.
- The base vendor's `com.google.android.camera.experimental2019` runtime JAR,
  its permission XML registration, and Pixel Camera Services APK/module.
  Inherit that base vendor product normally so its runtime XML is installed.
- Python **3.11+**, and Soong support for `android_app_set`.
- The source payload was tested in an Android 17 GApps build. Operation on a
  vanilla ROM without Google services has not been validated.

The [Motion Sense](https://github.com/its-hecker/floral-motionsense-plugin) and
[3D Face ID](https://github.com/its-hecker/floral-3D-faceid-plugin) plugins are
independent. This plugin supplies Camera packaging and app configuration;
the existing Floral base supplies the hardware stack.

## Integration

Run from your ROM source root:

```bash
git clone https://github.com/its-hecker/floral-gcam-plugin \
    vendor/hecker/floral/gcam
python3 vendor/hecker/floral/gcam/tools/fetch_prebuilts.py
```

The first preparation downloads three immutable APK-set chunks from the tested
vendor commit and validates their sizes and SHA-256 hashes. Cached files are
reused. Android builds perform no network downloads.

For an existing local copy of the source vendor repository:

```bash
python3 vendor/hecker/floral/gcam/tools/fetch_prebuilts.py \
    --source its-hecker/infinity_cnb_proprietary_vendor_google_coral=vendor/google/coral
```

Prepare against the **active** policy roots and the actual common vendor tree.
For the common Coral layout, start with:

```bash
python3 vendor/hecker/floral/gcam/tools/prepare.py \
    --device coral \
    --vendor-tree vendor/google/coral \
    --policy-dir system/sepolicy/private \
    --policy-dir device/google/coral/sepolicy/public \
    --policy-dir device/google/coral/sepolicy/private \
    --policy-dir device/google/coral/sepolicy/vendor/google
```

Include any additional selected policy roots that provide Google/GApps signer
rules or Camera labels using repeated `--policy-dir` arguments. Pass the roots
selected by your BoardConfig/product, rather than unrelated device policies.
For Flame, use `--device flame` and its actual common vendor/policy paths; the
vendor tree does not need to be named `vendor/google/coral`.

Setup checks the original Google certificate and reuses an existing compatible
Camera signer mapping. When no mapping exists, it generates one scoped to
**that Google certificate and that package only**, targeting the base's
`google_camera_app` domain. It adds no broad signer grant, platform signature,
privileged-app flag or permissive policy.

Literal `ALL` certificate paths in the selected `keys.conf` files are resolved
automatically. For variable/variant-dependent Google aliases, supply the actual
public certificate explicitly, for example:

```bash
python3 vendor/hecker/floral/gcam/tools/prepare.py \
    --device coral \
    --vendor-tree vendor/google/coral \
    --policy-dir device/google/coral/sepolicy \
    --sepolicy-key GOOGLE_CAMERA=vendor/hecker/floral/gcam/configs/GoogleCamera.x509.pem
```

Use the **actual alias and public certificate** from the selected base, and
include its other active roots as above. Setup refuses an unresolved Google/
Camera alias or an incompatible existing Camera label. Existing package-specific
Camera rules take precedence over global signer rules, matching Android's policy
selection. A new Camera-only entry can coexist with disjoint package rules.

After the base device/vendor product inheritance, add to the product/device
makefile:

```make
$(call inherit-product, vendor/hecker/floral/gcam/gcam.mk)
```

After the base board configuration, add to `BoardConfig.mk`:

```make
include vendor/hecker/floral/gcam/BoardConfigGCam.mk
```

The generated `FloralGoogleCamera` module overrides `Aperture`, `GoogleCamera`
and `GoogleCameraCoral`. Product filtering and Soong overrides are both used,
so inherited package selections are covered. Other camera apps are retained.
For another older module with the same Camera package, pass
`--replace-module ItsModuleName` during preparation. If the base has multiple
Pixel Camera Services imports, select its active one with
`--camera-services-module ItsServicesModule`.

The prepared vendor namespace is exported automatically. The base
experimental2019 and Pixel Camera Services modules remain in use, under their
own module names. The plugin never copies the complete vendor tree.

When moving from the existing Infinity-X Camera integration, replace the old
`device/google/coral/camera/camera.mk` inheritance with the plugin's `gcam.mk`.
Keep the compatible base camera policy; preparation reuses its signer mapping.
This standalone repository does not change the Infinity-X stable trees.

Use your ROM's normal lunch target and build command. When reusing an output
directory containing the older cameras, run `m installclean` once after target
selection to clear staged packages while retaining compiled intermediates.

Rerun preparation after syncing/changing the plugin, selected vendor modules,
or active policy. The product's read-only preflight rejects changed cached
bytes, inputs, generated files or policy/Blueprint file sets.

## Local manifest

```xml
<manifest>
    <remote name="hecker" fetch="https://github.com/its-hecker/" />
    <project name="floral-gcam-plugin"
             path="vendor/hecker/floral/gcam"
             remote="hecker"
             revision="main" />
</manifest>
```

If your manifest already defines the `hecker` remote, reuse it.

## Verification

From the ROM source root, verify the cache and prepared inputs without SDK tools:

```bash
python3 vendor/hecker/floral/gcam/tools/fetch_prebuilts.py --verify-only
python3 vendor/hecker/floral/gcam/tools/verify_ready.py
python3 vendor/hecker/floral/gcam/tools/verify_payload.py --check-hashes
```

For independent signature and manifest checks, supply `aapt2` and `apksigner`
from Android Build Tools, or put them in `PATH`:

```bash
python3 vendor/hecker/floral/gcam/tools/verify_payload.py \
    --aapt2 /path/to/build-tools/35.0.0/aapt2 \
    --apksigner /path/to/build-tools/35.0.0/apksigner
```

The verifier checks every installed APK's bytes, package, version, Google
certificate, the split install group and the startup JNI library. CI also
tests conflicting policies, cache corruption, input drift, product bootstrap
and preparation against the immutable source vendor/policy files.

After flashing:

```bash
adb shell dumpsys package com.google.android.GoogleCamera
adb shell pm path com.google.android.GoogleCamera
adb shell am start -n com.google.android.GoogleCamera/com.android.camera.CameraLauncher
adb shell "ps -AZ | grep com.google.android.GoogleCamera"
adb shell pm list packages org.lineageos.aperture
```

Expect version `8.7.250.494820638.44`, ten package paths (base plus nine splits),
and the `google_camera_app` process domain. Aperture should be absent from the
new ROM image. A newer Camera update in user data can take precedence over the
bundled version; inspect the active version when testing.

Test rear 1x/2x and front photos, HDR+, Night Sight, Portrait, video with audio,
slow motion, panorama/Photo Sphere, gallery preview and lock-screen launch.
Check Face ID before and after Camera use when the ROM supplies it.

```bash
adb logcat -b all -d > floral-gcam-logcat.txt
adb shell dumpsys media.camera > floral-camera-service.txt
```

| Validation | Status |
| --- | --- |
| Identical stock Camera payload in Coral / Infinity-X | Boot and general GCam operation confirmed on the phone |
| Original APK signatures, splits and startup native payload | Checked with hashes, aapt2 and apksigner |
| Plugin setup and product integration | Automated checks; full ROM build still required |
| Standalone plugin on Coral or Flame | Phone validation required |
| Individual shooting modes and other Android 17 ROMs | Builder verification required |

## Source and license

Integration and packaging: **Hecker**. Google supplies the original Camera APKs
and Floral components; AOSP/LineageOS supply the platform integration. Integration
source is Apache-2.0. The original APKs remain proprietary; see [NOTICE](NOTICE).
