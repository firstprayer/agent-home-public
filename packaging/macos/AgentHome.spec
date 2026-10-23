# Only explicit application resources enter the bundle; never copy the workspace.
from pathlib import Path
import tomllib

root = Path(SPECPATH).parent.parent
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
a = Analysis([str(root / "bridge.py")], pathex=[str(root)],
    hiddenimports=["certifi"],
    datas=[(str(root / "agent_home/static/build"), "agent_home/static/build"), (str(root / "LICENSE"), "."), (str(root / "NOTICE"), "."),
           (str(root / "THIRD_PARTY_NOTICES.txt"), ".")],
    runtime_hooks=[str(root / "packaging/macos/certificates.py")])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="agent-home-server", console=True)
collection = COLLECT(exe, a.binaries, a.datas, name="agent-home-server")
app = BUNDLE(collection, name="Agent Home.app", bundle_identifier="com.firstprayer.agent-home",
    icon=str(root / "build/AgentHome.icns"),
    version=version, info_plist={"CFBundleName": "Agent Home", "NSHighResolutionCapable": True,
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True}})
