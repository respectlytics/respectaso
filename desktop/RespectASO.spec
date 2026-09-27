# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for RespectASO native macOS app.

Build with:
    pyinstaller desktop/RespectASO.spec --noconfirm
"""

import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

block_cipher = None

BASE_DIR = Path(os.getcwd())

# Read VERSION from core/settings.py so Info.plist stays in sync
import re
_settings_text = (BASE_DIR / "core" / "settings.py").read_text()
_version_match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', _settings_text, re.MULTILINE)
VERSION = _version_match.group(1) if _version_match else "0.0.0"

shared_datas = [
    # Django templates
    (str(BASE_DIR / "aso" / "templates"), "aso/templates"),
    (str(BASE_DIR / "aso_pro" / "templates"), "aso_pro/templates"),
    # Static assets
    (str(BASE_DIR / "static"), "static"),
    (str(BASE_DIR / "staticfiles"), "staticfiles"),
    # Django template tags
    (str(BASE_DIR / "aso" / "templatetags"), "aso/templatetags"),
    # Django migrations
    (str(BASE_DIR / "aso" / "migrations"), "aso/migrations"),
    (str(BASE_DIR / "aso_pro" / "migrations"), "aso_pro/migrations"),
    # Core files
    (str(BASE_DIR / "core"), "core"),
    # Licensing — Ed25519 public key must be alongside validator.py
    (str(BASE_DIR / "licensing" / "public_key.pem"), "licensing"),
] + copy_metadata("fastmcp")

# Our own packages go in whole, collected rather than listed by hand.
# Django reaches some modules only through dotted strings in settings and
# urls (handler404, CSRF_FAILURE_VIEW, middleware, context processors),
# which PyInstaller cannot follow, and a hand list misses every new file:
# the first 2.28.0 build left out aso.error_views, so every 404 crashed.
# Tests stay out; migrations ship as source files through shared_datas.
FIRST_PARTY = ["core", "aso", "aso_pro", "licensing", "llm_providers"]


def _is_shipped(name):
    parts = name.split(".")
    return not any(
        part in ("tests", "migrations") or part.startswith("test_") for part in parts
    )


def _own_modules(package):
    # The public repo has no aso_pro, licensing or llm_providers.
    if not (BASE_DIR / package / "__init__.py").exists():
        return []
    return collect_submodules(package, filter=_is_shipped, on_error="raise")


shared_hiddenimports = [
        # Django core
        "django",
        "django.contrib.admin",
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "django.contrib.sessions",
        "django.contrib.messages",
        "django.contrib.staticfiles",
        "django.template.backends.django",
        "whitenoise",
        "whitenoise.middleware",
        "dotenv",
        "certifi",
        # Third-party LLM SDKs
        "openai",
        "anthropic",
        "google.genai",
        "tiktoken",
        "tiktoken_ext",
        "tiktoken_ext.openai_public",
        "jwt",
        "cryptography",
        "cryptography.hazmat.primitives.asymmetric.ed25519",
        "cryptography.hazmat.primitives.serialization",
        "httpx",
        # MCP server
        "fastmcp",
        "mcp",
    ] + [module for package in FIRST_PARTY for module in _own_modules(package)]

a = Analysis(
    [str(BASE_DIR / "desktop" / "main.py")],
    pathex=[str(BASE_DIR)],
    binaries=[],
    datas=shared_datas,
    hiddenimports=shared_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RespectASO",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=str(BASE_DIR / "desktop" / "entitlements.plist"),
)

# --- MCP CLI binary (no GUI, stdio transport) ---

mcp_a = Analysis(
    [str(BASE_DIR / "desktop" / "mcp_entry.py")],
    pathex=[str(BASE_DIR)],
    binaries=[],
    datas=shared_datas,
    hiddenimports=shared_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pywebview"],     # MCP binary does not need the GUI toolkit
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# De-duplicate shared binaries/datas between GUI and MCP analyses
for d in a.datas:
    if d not in mcp_a.datas:
        mcp_a.datas.append(d)
MERGE((a, "RespectASO", "RespectASO"), (mcp_a, "respectaso-mcp", "respectaso-mcp"))

mcp_pyz = PYZ(mcp_a.pure, mcp_a.zipped_data, cipher=block_cipher)

mcp_exe = EXE(
    mcp_pyz,
    mcp_a.scripts,
    [],
    exclude_binaries=True,
    name="respectaso-mcp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,                # CLI binary — needs console for stdio
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=str(BASE_DIR / "desktop" / "entitlements.plist"),
)

coll = COLLECT(
    exe,
    mcp_exe,
    a.binaries,
    mcp_a.binaries,
    a.zipfiles,
    mcp_a.zipfiles,
    a.datas,
    mcp_a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="RespectASO",
)

app = BUNDLE(
    coll,
    name="RespectASO.app",
    icon=str(BASE_DIR / "desktop" / "assets" / "RespectASO.icns"),
    bundle_identifier="com.respectlytics.respectaso",
    info_plist={
        "CFBundleName": "RespectASO",
        "CFBundleDisplayName": "RespectASO",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "CFBundleIdentifier": "com.respectlytics.respectaso",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "NSAppTransportSecurity": {
            "NSAllowsLocalNetworking": True,
        },
    },
)
