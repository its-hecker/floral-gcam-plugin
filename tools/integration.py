#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read the selected base policy and static vendor Blueprint declarations."""
import hashlib
from pathlib import Path
import re
import ssl
import xml.etree.ElementTree as ET
import zipfile

PACKAGE = "com.google.android.GoogleCamera"
SIGNER_SHA256 = "f0fd6c5b410f25cb25c3b53346c8972fae30f8ee7411df910480ad6b2d60db83"
LIBRARY = "com.google.android.camera.experimental2019"
POLICY_NAMES = {"seapp_contexts", "mac_permissions.xml", "keys.conf"}
DEFAULT_REPLACEMENTS = ["Aperture", "GoogleCamera", "GoogleCameraCoral"]


def policy_files(directories):
    paths = set()
    for directory in directories:
        if not directory.is_dir():
            raise ValueError("Policy directory is missing: " + str(directory))
        paths.update(p.resolve() for p in directory.rglob("*")
                     if p.is_file() and (p.suffix == ".te" or p.name in POLICY_NAMES))
    if not paths:
        raise ValueError("No policy files found in the selected policy directories")
    return sorted(paths)


def certificate(path):
    data = path.read_bytes()
    return (ssl.PEM_cert_to_DER_cert(data.decode("ascii"))
            if data.startswith(b"-----BEGIN CERTIFICATE-----") else data)


def resolve_keys(rom, paths, overrides):
    """Resolve literal ALL entries; variable/variant-dependent keys stay explicit."""
    candidates = {}
    for path in paths:
        if path.name != "keys.conf":
            continue
        tag = None
        for line in path.read_text().splitlines():
            line = line.partition("#")[0].strip()
            section = re.fullmatch(r"\[@([A-Za-z0-9_]+)\]", line)
            if section:
                tag = section[1]
            elif tag and ":" in line:
                variant, value = (part.strip() for part in line.split(":", 1))
                candidates.setdefault(tag, []).append((variant.upper(), value))
    result = dict(overrides)
    for tag, definitions in candidates.items():
        if len(definitions) != 1 or definitions[0][0] != "ALL":
            continue
        value = definitions[0][1]
        if "$" in value or not value:
            continue
        path = Path(value)
        path = (path if path.is_absolute() else rom / path).resolve()
        if not path.is_file():
            continue
        if tag in overrides and certificate(overrides[tag]) != certificate(path):
            raise ValueError("Explicit " + tag + " certificate differs from selected keys.conf")
        result[tag] = path
    return result


def no_comments(text):
    return re.sub(r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\])*\"",
                  lambda m: m[0] if m[0].startswith('"') else " ", text, flags=re.S)


def modules(path):
    """Read static top-level modules; preserve braces inside quoted strings."""
    text = no_comments(path.read_text())
    result = []
    pattern = re.compile(r"(?m)^\s*(\w+)\s*\{")
    position = 0
    while match := pattern.search(text, position):
        start = match.end()
        depth, quoted, escaped = 1, False, False
        end = start
        while end < len(text) and depth:
            char = text[end]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            end += 1
        if depth:
            raise ValueError("Unbalanced Blueprint module: " + str(path))
        body = text[start:end-1]
        name = re.search(r'\bname\s*:\s*"([^"]+)"', body)
        result.append({"kind": match[1], "name": name[1] if name else None,
                       "body": body, "path": path.resolve()})
        position = end
    return result


def static_string(body, key):
    match = re.search(r"\b" + re.escape(key) + r'\s*:\s*"([^"]+)"', body)
    if not match:
        raise ValueError("Expected a static Blueprint string for " + key)
    return match[1]


def static_array(body, key):
    match = re.search(r"\b" + re.escape(key) + r"\s*:\s*\[([^\]]*)\]", body, re.S)
    if not match or re.sub(r'"(?:\\.|[^"\\])*"|[,\s]', "", match[1]):
        raise ValueError("Expected a static Blueprint array for " + key)
    return re.findall(r'"([^"]+)"', match[1])


def vendor_inputs(vendor):
    if not (vendor / "Android.bp").is_file():
        raise ValueError("Select the actual common vendor tree containing Android.bp")
    return sorted(p.resolve() for p in vendor.rglob("Android.bp"))


def vendor_dependencies(vendor, rom, services_module=None):
    bp_paths = vendor_inputs(vendor)
    by_path = {p: modules(p) for p in bp_paths}
    all_modules = [m for values in by_path.values() for m in values]
    libraries = [m for m in all_modules if m["name"] == LIBRARY]
    if len(libraries) != 1 or libraries[0]["kind"] not in {"dex_import", "java_import"}:
        raise ValueError("Base vendor must provide exactly one " + LIBRARY + " import")
    library = libraries[0]
    jars = static_array(library["body"], "jars")
    if len(jars) != 1:
        raise ValueError("The experimental2019 import must select one JAR")
    jar = (library["path"].parent / jars[0]).resolve()
    if not jar.is_relative_to(vendor) or not zipfile.is_zipfile(jar):
        raise ValueError("Missing or invalid Floral experimental2019 JAR: " + str(jar))
    with zipfile.ZipFile(jar) as archive:
        if "classes.dex" not in archive.namelist():
            raise ValueError("experimental2019 JAR must contain its runtime DEX")
    xmls = sorted(vendor.rglob(LIBRARY + ".xml"))
    if len(xmls) != 1:
        raise ValueError("Base vendor must supply the experimental2019 permission XML")
    mappings = ET.parse(xmls[0]).getroot().findall("library")
    if not any(n.get("name") == LIBRARY and
               n.get("file") == "/vendor/framework/" + LIBRARY + ".jar" for n in mappings):
        raise ValueError("experimental2019 XML does not register the Floral vendor JAR")
    apps = []
    for module in all_modules:
        if module["kind"] != "android_app_import":
            continue
        if services_module:
            selected = module["name"] == services_module
        else:
            selected = "PixelCameraServices" in module["body"]
        if selected:
            apps.append(module)
    if len(apps) != 1:
        raise ValueError("Select one base Pixel Camera Services import with --camera-services-module")
    service = apps[0]
    apk = (service["path"].parent / static_string(service["body"], "apk")).resolve()
    if not apk.is_relative_to(vendor) or not zipfile.is_zipfile(apk):
        raise ValueError("Missing or invalid base Pixel Camera Services APK: " + str(apk))
    with zipfile.ZipFile(apk) as archive:
        if "AndroidManifest.xml" not in archive.namelist():
            raise ValueError("Pixel Camera Services APK has no manifest")
    namespaces = set()
    for module in [library, service]:
        for directory in [module["path"].parent, *module["path"].parent.parents]:
            if not directory.is_relative_to(vendor):
                break
            bp = directory / "Android.bp"
            if bp in by_path and any(m["kind"] == "soong_namespace" for m in by_path[bp]):
                namespaces.add(directory.relative_to(rom).as_posix())
                break
    return {"modules": [LIBRARY, service["name"]], "namespaces": sorted(namespaces),
            "files": sorted(set(bp_paths + [jar, apk, xmls[0].resolve()])),
            "jar": jar, "services_apk": apk, "permission_xml": xmls[0].resolve()}


def camera_policy(paths, aliases):
    te = "\n".join(re.sub(r"#.*", "", p.read_text())
                   for p in paths if p.suffix == ".te")
    declarations = re.findall(r"\btype\s+google_camera_app\s*,([^;]+);", te)
    if len(declarations) != 1 or "domain" not in re.findall(r"\w+", declarations[0]):
        raise ValueError("Select the base policy declaring google_camera_app")
    if (not re.search(r"\bcoredomain\b", declarations[0]) and
            not re.search(r"\btypeattribute\s+google_camera_app\s+coredomain\s*;", te)):
        raise ValueError("Base google_camera_app must be a platform coredomain")
    if not re.search(r"\bapp_domain\s*\(\s*google_camera_app\s*\)", te):
        raise ValueError("Base google_camera_app must retain app_domain policy")
    for target in ["qdsp_device", "airbrush_device", "hal_airbrush_hwservice"]:
        if not re.search(r"\ballow\s+google_camera_app\s+" + target + r"\s*:", te):
            raise ValueError("Include active Floral Google Camera policy for " + target)
    contexts = []
    for path in paths:
        if path.name != "seapp_contexts":
            continue
        for line in path.read_text().splitlines():
            line = line.partition("#")[0]
            fields = dict(part.split("=", 1) for part in line.split() if "=" in part)
            if (fields.get("name") == PACKAGE and fields.get("seinfo") == "google"
                    and fields.get("user") == "_app" and fields.get("isPrivApp") != "true"):
                contexts.append((path, fields))
    if len(contexts) != 1:
        raise ValueError("Select exactly one non-privileged GoogleCamera seinfo=google seapp mapping")
    fields = contexts[0][1]
    if fields.get("domain") != "google_camera_app" or fields.get("type") != "app_data_file":
        raise ValueError("Existing GoogleCamera seapp mapping conflicts with the tested domain")
    if any(k in fields for k in ["isSystemServer", "isEphemeralApp", "fromRunAs",
                                 "minTargetSdkVersion", "isIsolatedComputeApp"]):
        raise ValueError("Review extra selectors on the base GoogleCamera seapp mapping")
    scoped, global_rules = [], []
    for path in paths:
        if path.name != "mac_permissions.xml":
            continue
        for signer in ET.parse(path).getroot().findall("signer"):
            value = signer.get("signature", "")
            packages = signer.findall("package")
            camera = [p for p in packages if p.get("name") == PACKAGE]
            if value.startswith("@"):
                tag = value[1:]
                if tag not in aliases:
                    if camera or re.search(r"GOOGLE|GCAM|CAMERA|GMS", tag, re.I):
                        raise ValueError("Resolve " + value + " using --sepolicy-key TAG=PUBLIC_CERT")
                    continue
                der = certificate(aliases[tag])
            else:
                try:
                    der = bytes.fromhex(value)
                except ValueError as error:
                    raise ValueError("Invalid signer in " + str(path)) from error
            if hashlib.sha256(der).hexdigest() != SIGNER_SHA256:
                continue
            if signer.findall("cert"):
                raise ValueError("Review multi-certificate Google Camera signer policy")
            if packages and signer.find("seinfo") is not None:
                raise ValueError("Google signer mixes global and package mappings")
            if len(camera) > 1:
                raise ValueError("Duplicate GoogleCamera package signer entries")
            if packages and not camera:
                # A separate Camera-only stanza has disjoint package selectors.
                continue
            node = camera[0] if camera else signer
            labels = node.findall("seinfo")
            if len(labels) != 1:
                raise ValueError("Google signer must have one seinfo value in " + str(path))
            (scoped if camera else global_rules).append((path, labels[0].get("value")))
    if len(scoped) > 1 or len(global_rules) > 1:
        raise ValueError("Duplicate active policies for the original Google Camera certificate")
    # Android's PolicyComparator orders package-specific mappings before globals.
    # A global rule and one scoped rule for the same signer are valid together.
    selected = scoped or global_rules
    if selected and selected[0][1] != "google":
        raise ValueError("Existing Google certificate needs a Camera seinfo=google mapping in " + str(selected[0][0]))
    return {"add_signer": not selected,
            "signer_source": str(selected[0][0]) if selected else None,
            "seapp_source": str(contexts[0][0])}
