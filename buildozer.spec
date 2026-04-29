[app]

# (str) Title of your application
title = Emeraude

# (str) Package name
package.name = emeraude

# (str) Package domain (needed for android/ios packaging)
# Reverse DNS of the maintainer ; ne pas changer après le 1er publish.
package.domain = org.mikaelarth

# (str) Source code where the main.py live
source.dir = src

# (list) Source files to include (let empty to include all the files)
source.include_exts = py,sql

# (list) List of source files to include — paths relative to source.dir.
# Migrations SQL must ship inside the APK (loaded at first DB use).
source.include_patterns = emeraude/infra/migrations/*.sql

# (list) Source files to exclude (let empty to not exclude anything)
source.exclude_exts = pyc

# (list) List of directory to exclude (let empty to not exclude anything)
source.exclude_dirs = tests, docs, .venv, .buildozer, bin, __pycache__

# (str) Application versioning (method 1)
# Manual sync with pyproject.toml. We don't use ``version.regex`` because
# emeraude/__init__.py reads its version dynamically via importlib.metadata
# (works in pip-installed contexts but not parseable by buildozer).
version = 0.0.75

# (list) Application requirements
# Pinned to the same versions as pyproject.toml's runtime deps (kivy 2.3,
# certifi, requests). Python is implicit.
# Iter #74 : ``filetype`` ajouté car Kivy 2.3.x importe
# ``filetype`` au load de ``kivy.core.image`` (transitive dep).
# Sans, la première import de Kivy lève ``ModuleNotFoundError: No
# module named 'filetype'`` — c'est le crash qu'on a capturé sur
# l'émulateur (run 25115412399, last_crash.log iter #71).
# La recette p4a de kivy ne bundle pas filetype par défaut.
requirements = python3,kivy==2.3.1,requests==2.32.3,certifi==2024.8.30,filetype==1.2.0

# (str) Custom source folders for requirements
# Set this if you want to include some custom python distribution
#requirements.source.kivy = ../../kivy

# (str) Presplash of the application
# Default Kivy presplash will be used until we ship a custom asset.
#presplash.filename = %(source.dir)s/data/presplash.png

# (str) Icon of the application
# Default Kivy icon will be used until we ship a custom asset.
#icon.filename = %(source.dir)s/data/icon.png

# (str) Supported orientation (one of landscape, sensorLandscape, portrait or all)
orientation = portrait

# (bool) Indicate if the application should be fullscreen or not
fullscreen = 0

# (str) Path to a custom main file (relative to source.dir)
# Buildozer convention : looks for src/main.py by default, but our entry
# point lives at src/emeraude/main.py. The ``main.py`` shim at the root
# of source.dir does the import + run.
# We use the existing src/emeraude/main.py via a top-level main.py shim.
# See ``[app] presplash.filename`` notes below.

#
# Android specific
#

# (list) Permissions
# INTERNET only — Emeraude needs to call Binance / market data APIs.
# Aucune permission étendue (READ_EXTERNAL_STORAGE, etc.) — anti-règle
# A1 : ne pas demander ce qu'on n'utilise pas.
android.permissions = INTERNET

# (int) Target Android API
# 33 = Android 13 (Tiramisu) — Google Play minimum target as of 2025.
android.api = 33

# (int) Minimum API your APK / AAB will support
# 24 = Android 7.0 Nougat — couvre ~95 % des devices actifs en 2026.
android.minapi = 24

# (str) Android NDK version to use
# 25b is the Buildozer 1.5 default ; pinned for reproducibility.
android.ndk = 25b

# (list) The Android archs to build for
# arm64-v8a : tous les smartphones modernes (Redmi, Samsung, etc.).
# armeabi-v7a : devices 32-bit anciens (~10 % du parc en 2026).
# x86_64 : ajouté iter #73 pour permettre aux émulateurs CI x86_64
# de lancer l'app NATIVEMENT, sans passer par libndk_translation.
# Iter #72 a montré que le translator AOSP API 30 (v0.2.2) crash
# avec SIGILL sur certaines instructions ARM NEON SIMD utilisées
# par Python/Kivy (run 25108820295, backtrace 100 % dans
# libndk_translation::DecodeSimdScalarTwoRegMisc). API 33 google_apis
# x86_64 n'a même plus de translation et refuse l'install d'un APK
# arm-only avec INSTALL_FAILED_NO_MATCHING_ABIS (run 25109106985).
# Conséquence : APK +30 % de taille (~50 MB vs ~35 MB) mais c'est le
# prix d'un workflow CI émulateur fiable. Production-only build via
# split-by-abi pourra retirer x86_64 plus tard si besoin.
android.archs = arm64-v8a,armeabi-v7a,x86_64

# (bool) If True, then automatically accept SDK license agreements.
# Required for headless CI builds.
android.accept_sdk_license = True

# (str) Bootstrap to use for android builds
p4a.bootstrap = sdl2

# (str) python-for-android branch to use
# ``master`` = latest stable Buildozer pulls. python-for-android tags
# its releases (2024.1.21, etc.) but Buildozer's ``p4a.branch`` does
# a ``git clone -b BRANCH --single-branch`` which doesn't fetch tags.
# Stick to ``master`` until we want a deeper pin (would require
# changing the Buildozer p4a fetch strategy).
p4a.branch = master

# (bool) Skip byte compile for .py files
# False = compile to .pyo for smaller APK. True = include .py source.
android.no-byte-compile-python = False


[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 2

# (int) Display warning if buildozer is run as root (0 = False, 1 = True)
warn_on_root = 1
