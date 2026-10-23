"""Build a self-contained native app and drag-to-Applications disk image on macOS."""
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import ssl
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[2]
MINIMUM_MACOS = "15.0"


def run(*args):
    subprocess.run(args, cwd=ROOT, check=True)


def verify_deployment_targets(app):
    checked = 0
    target = (tuple(map(int, MINIMUM_MACOS.split('.'))) + (0, 0))[:3]
    for path in app.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as stream:
            magic = stream.read(4)
        if magic not in {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce",
                         b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}:
            continue
        output = subprocess.check_output(["otool", "-l", str(path)], text=True)
        versions = re.findall(r"\bminos\s+(\d+(?:\.\d+)+)", output)
        versions += re.findall(r"cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version (\d+(?:\.\d+)+)", output)
        if not versions:
            raise RuntimeError(f"Cannot determine the deployment target of {path.name}")
        if any(tuple(map(int, version.split('.'))) > target for version in versions):
            highest = max(versions, key=lambda version: tuple(map(int, version.split('.'))))
            raise RuntimeError(f"{path.name} requires macOS {highest}; rebuild using uv's managed Python instead of host system libraries")
        checked += 1
    if checked < 2:
        raise RuntimeError("The package is missing its native executables")
    print(f"Validated {checked} Mach-O binaries for macOS {MINIMUM_MACOS}")


def main():
    if sys.platform != "darwin":
        raise SystemExit("Build the macOS package on a Mac with Xcode Command Line Tools.")
    os.environ["MACOSX_DEPLOYMENT_TARGET"] = MINIMUM_MACOS
    dist = ROOT / "dist"
    stage = ROOT / "build/dmg"
    app = dist / "Agent Home.app"
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    iconset = ROOT / "build/AgentHome.iconset"
    run("swift", "packaging/macos/Icon.swift", str(iconset))
    run("iconutil", "--convert", "icns", "--output", str(ROOT / "build/AgentHome.icns"), str(iconset))
    # PyInstaller's explicit inputs exclude local databases, recordings, and tokens.
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "packaging/macos/AgentHome.spec")
    launcher = app / "Contents/MacOS/AgentHome"
    run("swiftc", "-O", "-parse-as-library", "-target", f"{platform.machine()}-apple-macosx{MINIMUM_MACOS}", "-framework", "AppKit", "-framework", "WebKit",
        "packaging/macos/Launcher.swift", "-o", str(launcher))
    plist = app / "Contents/Info.plist"
    metadata = plistlib.loads(plist.read_bytes())
    metadata["CFBundleExecutable"] = "AgentHome"
    metadata["LSMinimumSystemVersion"] = MINIMUM_MACOS
    metadata["NSHumanReadableCopyright"] = "Copyright © 2026 firstprayer. GNU AGPLv3."
    icon = app / "Contents/Resources" / metadata["CFBundleIconFile"]
    if icon.read_bytes()[:4] != b"icns":
        raise RuntimeError("The app must include its custom macOS icon.")
    plist.write_bytes(plistlib.dumps(metadata))
    verify_deployment_targets(app)
    run("codesign", "--force", "--deep", "--sign", "-", str(app))
    run("codesign", "--verify", "--deep", "--strict", str(app))
    certificates = next(app.rglob("cacert.pem"), None)
    if certificates is None or not ssl.create_default_context(cafile=str(certificates)).cert_store_stats()["x509_ca"]:
        raise RuntimeError("The app must include valid TLS roots for Telegram HTTPS.")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    run("ditto", str(app), str(stage / app.name))
    os.symlink("/Applications", stage / "Applications")
    (stage / "Install.txt").write_text(
        "Drag Agent Home to Applications, then open it.\n"
        "The dashboard opens inside the app; Python, uv, and Node are not required.\n"
        "Close the window to keep Telegram relaying. Use Quit Agent Home to stop.\n"
        "Codex must be installed and running separately for Codex sleeves.\n"
        "Settings and data stay in ~/.config/agent-communication/.\n"
        "This preview is ad-hoc signed and is not Apple notarized.\n"
        "If macOS blocks it, review the source before choosing Privacy & Security > Open Anyway.\n"
        "See https://support.apple.com/en-us/102445 for Apple's instructions.\n")
    image = dist / f"Agent-Home-{version}-macOS-{platform.machine()}.dmg"
    run("hdiutil", "create", "-volname", "Agent Home", "-srcfolder", str(stage),
        "-ov", "-format", "UDZO", str(image))
    run("hdiutil", "verify", str(image))
    print(f"Built {image}")


if __name__ == "__main__":
    main()
