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
# Iter #78 (cf. ADR-0004) : ajout html, js, css, json, woff2, woff,
# ttf, svg, png pour bundler la web app (Vue 3 + Vuetify) qui vit
# désormais sous src/emeraude/web/. ico exclu : pas de favicon (la
# WebView Android ne l'affiche jamais en fullscreen).
source.include_exts = py,sql,html,js,css,json,woff2,woff,ttf,svg,png

# (list) List of source files to include — paths relative to source.dir.
# Migrations SQL must ship inside the APK (loaded at first DB use).
# Iter #78 : web/** ajouté pour la WebView. La répétition est utile
# car ``source.include_exts`` filtre par extension globalement, alors
# que les patterns ciblent explicitement les sous-arborescences.
source.include_patterns = emeraude/infra/migrations/*.sql,emeraude/web/index.html,emeraude/web/static/**/*

# (list) Source files to exclude (let empty to not exclude anything)
source.exclude_exts = pyc

# (list) List of directory to exclude (let empty to not exclude anything)
source.exclude_dirs = tests, docs, .venv, .buildozer, bin, __pycache__

# (str) Application versioning (method 1)
# Manual sync with pyproject.toml. We don't use ``version.regex`` because
# emeraude/__init__.py reads its version dynamically via importlib.metadata
# (works in pip-installed contexts but not parseable by buildozer).
version = 0.0.84

# (list) Application requirements
# Iter #79 (cf. ADR-0004) : bascule de bootstrap ``sdl2`` -> ``webview``.
# Le bootstrap ``webview`` p4a fournit déjà une Activity Java qui crée
# une WebView fullscreen, lance Python en thread, et redirige la
# WebView sur ``http://127.0.0.1:<port>/`` une fois le serveur prêt.
# Le manifest généré inclut nativement ``android:usesCleartextTraffic=
# "true"`` — fini les ``ERR_CLEARTEXT_NOT_PERMITTED`` (cf. iter #78
# multi-step debug).
# Conséquences sur les requirements :
# * ``kivy`` retiré : plus utilisé (le SPA Vuetify côté JS rend
#   l'UI, le coeur Python sert le HTTP via ``http.server`` stdlib).
# * ``filetype`` retiré : c'était une transitive de Kivy 2.3.x.
# Restent : ``requests`` + ``certifi`` (utilisés par les services
# Binance et le résolveur SSL infra).
requirements = python3,requests==2.32.3,certifi==2024.8.30

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

# Note iter #78quater : la tentative iter #78ter d'utiliser
# ``android.extra_manifest_application_arguments`` pour injecter
# ``android:usesCleartextTraffic="true"`` a cassé le manifest merger
# Gradle (``ManifestMerger2$MergeFailureException`` v0.0.80 build).
# Le mécanisme exact d'échec n'est pas trivialement reproductible.
# Plutôt que de continuer à debug, on bascule sur HTTPS auto-signé
# (cf. iter #78quater) — l'app sert son propre cert au boot et la
# WebView est configurée pour l'accepter via
# ``WebViewClient.onReceivedSslError``. Pas d'attribut manifest à
# injecter, et c'est plus propre architecturellement.

# (str) Bootstrap to use for android builds
# Iter #79 : bascule ``sdl2`` -> ``webview``. Cf. ADR-0004 :
# * ``sdl2`` était la voie pour les apps Kivy avec rendering OpenGL
#   custom. Notre UI est désormais en HTML/Vue/Vuetify dans une
#   WebView (iter #78), donc SDL2 + Kivy event loop ne servent plus
#   à rien.
# * ``webview`` ship une PythonActivity Java qui crée la WebView
#   nativement, lance Python en thread, et redirige sur
#   ``http://127.0.0.1:<port>/`` quand le serveur Python répond. Le
#   manifest auto-généré inclut ``android:usesCleartextTraffic="true"``,
#   ce qui résout le ``ERR_CLEARTEXT_NOT_PERMITTED`` qu'on a passé
#   3 iters à essayer de bypasser.
p4a.bootstrap = webview

# (int) Port utilisé par le bootstrap webview pour parler au serveur
# Python local. Doit matcher :data:`emeraude.api.server.DEFAULT_PORT`.
# Buildozer le passe à p4a via ``--port=<value>`` qui est interpolé
# dans le template ``WebViewLoader.tmpl.java`` (cf. p4a master).
p4a.port = 8765

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
