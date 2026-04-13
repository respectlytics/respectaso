# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for RespectASO native macOS app.

Build with:
    pyinstaller desktop/RespectASO.spec --noconfirm
"""

import os
from pathlib import Path

block_cipher = None

BASE_DIR = Path(os.getcwd())

# Read VERSION from core/settings.py so Info.plist stays in sync
import re
_settings_text = (BASE_DIR / "core" / "settings.py").read_text()
_version_match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', _settings_text, re.MULTILINE)
VERSION = _version_match.group(1) if _version_match else "0.0.0"

a = Analysis(
    [str(BASE_DIR / "desktop" / "main.py")],
    pathex=[str(BASE_DIR)],
    binaries=[],
    datas=[
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
    ],
    hiddenimports=[
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
        # Core app
        "core.settings",
        "core.urls",
        "core.wsgi",
        "core.context_processors",
        # Free features (aso)
        "aso",
        "aso.apps",
        "aso.models",
        "aso.views",
        "aso.urls",
        "aso.scoring",
        "aso.services",
        "aso.scheduler",
        "aso.templatetags",
        "aso.templatetags.aso_tags",
        # Pro features (aso_pro)
        "aso_pro",
        "aso_pro.apps",
        "aso_pro.models",
        "aso_pro.views",
        "aso_pro.urls",
        "aso_pro.researcher",
        "aso_pro.competitor_analyzer",
        "aso_pro.simulator",
        "aso_pro.agents",
        "aso_pro.constraints",
        "aso_pro.keyword_pipeline",
        "aso_pro.prompts",
        "aso_pro.schemas",
        "aso_pro.metadata_evaluator",
        "aso_pro.settings_storage",
        # LLM providers
        "llm_providers",
        "llm_providers.base",
        "llm_providers.factory",
        "llm_providers.openai_provider",
        "llm_providers.anthropic_provider",
        "llm_providers.gemini_provider",
        "llm_providers.token_counter",
        # Licensing
        "licensing",
        "licensing.apps",
        "licensing.decorators",
        "licensing.middleware",
        "licensing.storage",
        "licensing.validator",
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
    ],
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

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
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
