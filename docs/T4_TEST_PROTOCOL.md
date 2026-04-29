# Protocole de test T4 — APK Android sans crash 24h

> **Statut** : à exécuter manuellement par le propriétaire après chaque
> tag CI vert produisant un APK (cf. workflow `.github/workflows/android.yml`).
> Critère doc 06 : **T4 = APK Android sans crash 24h**.

Ce document encapsule la procédure complète de validation de l'APK
debug : récupération depuis GitHub Actions, sideload, smoke test
5 minutes, observation 24h, template bug report. Iter #70 ne **livre
pas** un test runtime — il livre **le protocole** pour l'exécuter.

---

## Pré-requis

### Device

- Android **API 24+** (Android 7.0 Nougat ou plus récent — couvre
  ~95 % des devices actifs)
- ~100 MB libres pour install + DB SQLite
- Connexion Internet (pour mode REAL ; mode PAPER fonctionne offline
  sauf prix marché si on les ajoute plus tard)

### Outils côté testeur

**Option A — ADB (recommandé pour les développeurs)** :

```bash
# Sur la machine de dev (Windows / Linux / macOS)
adb install bin/emeraude-0.0.XX-arm64-v8a_armeabi-v7a-debug.apk
```

Pré-requis : USB debugging activé sur le device + drivers ADB
installés (Windows) ou `android-sdk-platform-tools` (Linux/macOS).

**Option B — Sideload manuel (utilisateurs lambda)** :

1. Settings → Security → Activer "Install from unknown sources"
   (ou par-app permission selon Android version)
2. Transférer l'APK vers le device (USB / Drive / Telegram / etc.)
3. Ouvrir le fichier APK dans le file manager → "Installer"

---

## Récupération de l'APK depuis CI

### Via GitHub UI

1. Aller sur https://github.com/Mikaelarth/Emeraude/actions
2. Onglet **"Android APK"** workflow
3. Cliquer le run **vert** le plus récent (typiquement le tag
   `v0.0.XX` correspondant à la dernière release)
4. Section **Artifacts** en bas → télécharger
   `emeraude-debug-apk-<sha>.zip`
5. Dézipper → `emeraude-0.0.XX-arm64-v8a_armeabi-v7a-debug.apk`

### Via `gh` CLI (rapide)

```bash
# Lister les runs Android
gh run list --workflow=android.yml --limit 5

# Télécharger l'artifact du run le plus récent
gh run download <RUN_ID> --name "emeraude-debug-apk-<sha>"

# Ou tous les artifacts du dernier run vert
gh run download $(gh run list --workflow=android.yml --status=success \
    --limit=1 --json databaseId --jq '.[0].databaseId')
```

L'APK apparaît dans le répertoire courant.

---

## Smoke test 5 minutes (premier lancement)

**Objectif** : vérifier que l'app démarre, les 3 écrans s'affichent,
la navigation fonctionne, le cycle pump tourne. **5 minutes max.**

### Checklist

- [ ] **L'APK s'installe sans erreur** (pas de "App not installed",
      "Parse error", etc.)
- [ ] **L'app se lance dans les 3 secondes** après tap sur l'icône
- [ ] **Écran Dashboard apparaît par défaut** (premier onglet) :
  - [ ] `Capital : 20.00 USDT` visible (paper-mode cold start)
  - [ ] `Mode : Paper` badge visible
  - [ ] `Aucune position ouverte`
  - [ ] `0 trade fermé`
  - [ ] `P&L cumulé : 0.00 USDT`
- [ ] **Bottom nav 3 onglets visibles** : Tableau / Journal / Config
- [ ] **Tap sur Journal** → écran change, affiche "Aucun événement
      enregistré pour l'instant." (cold start, audit log vide)
- [ ] **Tap sur Config** → écran change, affiche les 5 status rows
      (Mode / Capital de demarrage / Version / Evenements audit /
      Stockage)
- [ ] **Tap retour sur Tableau** → re-affiche le Dashboard
- [ ] **Active tab styling** : l'onglet courant est en couleur
      `COLOR_PRIMARY` (vert émeraude), les autres en
      `COLOR_TEXT_SECONDARY` (gris)
- [ ] **Aucun crash, aucune force-close** pendant cette navigation

### Si l'app crash au démarrage

1. Capturer le `logcat` :
   ```bash
   adb logcat -d | grep -i "emeraude\|kivy\|sigsegv" > t4_crash_log.txt
   ```
2. Ouvrir un issue GitHub avec le contenu du log + le model du
   device + version Android (`adb shell getprop ro.build.version.release`)
3. Iter de fix dédié (typiquement : recipe manquante, permissions
   Android oubliées, path filesystem incompatible)

---

## Observation 24h (test T4 stricto sensu)

**Objectif** : valider que l'app tourne **sans crash** pendant 24h
en mode paper, avec le cycle pump actif (refresh toutes les 5 s).

### Setup

- L'app doit rester **ouverte au premier plan** ou en background
  selon le mode de test :
  - **Foreground 24h** : strict, drain batterie. Idéal si on veut
    observer le cycle pump visuellement.
  - **Background 24h** : Android peut killer l'app après quelques
    heures de standby (battery optimizations). C'est OK — au
    relaunch, l'app doit redémarrer **proprement** sans crash.
- Désactiver les power-saving optimisations Emeraude-spécifiques :
  Settings → Apps → Emeraude → Battery → "Unrestricted" si possible.

### Checklist 24h

- [ ] **H+0** : app lancée, screenshot du Dashboard
- [ ] **H+1** : app toujours active, vérifier `Capital` inchangé
      (pas de trade en paper sans bot actif)
- [ ] **H+6** : app vérifiée, screenshot
- [ ] **H+12** : app vérifiée, screenshot
- [ ] **H+24** : app vérifiée, **screenshot + capture logcat**
- [ ] **Aucun crash forcé** détecté (pas de "Emeraude has stopped")
- [ ] **Aucun ANR** (Application Not Responding) détecté
- [ ] **Mémoire stable** : `adb shell dumpsys meminfo org.mikaelarth.emeraude`
      à H+0 et H+24, deltas raisonnables (< 50 MB drift)
- [ ] **Battery drain** : noter le % à H+0 et H+24, calculer le
      drain par heure (cible E4 doc 06 : ≤ 3 % / 24h en background)

### Capture des artifacts

Après le test 24h, archiver dans un dossier
`docs/t4_runs/v0.0.XX_<date>/` :

- `smoke_screenshot_dashboard.png`
- `smoke_screenshot_journal.png`
- `smoke_screenshot_config.png`
- `h24_screenshot_dashboard.png`
- `h24_logcat.txt`
- `h24_meminfo_h0.txt` + `h24_meminfo_h24.txt`
- `RUN_NOTES.md` : observations libres + verdict T4

---

## Test mode REAL (optionnel, post-smoke)

**Pré-requis** : avoir des clés API Binance read-only (pas
WITHDRAW). Compte spot avec un solde USDT > 0 (peut être 0.01 USDT).

**Sécurité** : **NE PAS** utiliser des clés Binance avec des fonds
significatifs sur le device test tant que la chaîne complète n'est
pas auditée. L'iter #66 chiffre les clés mais le device test
peut être compromis.

### Set passphrase

Sur Android, pas de moyen direct de set un env var pour une app
graphique. Options :

**Option A — Termux** :
1. Installer Termux depuis F-Droid
2. `pkg install android-tools`
3. Exporter la passphrase + lancer Emeraude :
   ```bash
   export EMERAUDE_API_PASSPHRASE="ma-passphrase-secrete"
   am start -n org.mikaelarth.emeraude/org.kivy.android.PythonActivity
   ```

**Option B — Modifier `main.py` temporairement** :
   - Pas recommandé en prod (passphrase en clair dans l'APK)
   - OK pour test isolé : ajouter
     `os.environ["EMERAUDE_API_PASSPHRASE"] = "test"` au début de
     `src/main.py`, rebuild APK, sideload, tester, puis revenir

### Checklist mode REAL

- [ ] App lancée avec passphrase set
- [ ] Onglet Config → section "Cles API Binance" visible (form actif,
      pas de hint "définissez `EMERAUDE_API_PASSPHRASE`")
- [ ] Saisie API key (visible) + API secret (masqué)
- [ ] Tap "Sauvegarder les cles" → arme
- [ ] Second tap dans 5s → "Cles sauvegardees (chiffrees)"
- [ ] Status row API Key affiche `...XXXX [definie]` avec les 4
      derniers chars
- [ ] Toggle mode → "Passer en mode Reel" → arme → confirme
- [ ] Dashboard refresh tick (~5s) → `Mode : Reel` + `Capital : <balance>`
- [ ] Capital affiché correspond à `adb` ou Binance UI manuel

---

## Template bug report

Si un bug est détecté pendant le test :

```markdown
## Bug iter #XX — [titre court]

**Device** : <model> / Android <version> / API <level>
**APK version** : 0.0.XX
**Test phase** : Smoke / 24h / mode REAL
**Reproductibilité** : 1 fois / occasionnel / systématique

### Steps to reproduce
1. ...
2. ...
3. ...

### Expected
...

### Actual
...

### Logcat extract
```
<paste logcat -d ouput here>
```

### Screenshots
- ...
```

---

## Critères de succès T4

T4 est **atteint** quand un APK passe les conditions suivantes :

| # | Critère | Mesure |
|---|---|---|
| T4.1 | APK s'installe sans erreur | Smoke check |
| T4.2 | Smoke test 5 min : 3 écrans + nav OK | Smoke checklist ✅ |
| T4.3 | 24h sans crash forcé | Observation ✅ |
| T4.4 | Mémoire stable (drift < 50 MB) | meminfo h0 vs h24 |
| T4.5 | (Optionnel) Mode REAL fonctionnel | Mode REAL checklist ✅ |

T4 est **échoué** si :

- Crash au démarrage ou pendant la navigation
- Force-close récurrent (> 1× / 6h)
- ANR détecté
- Memory leak avéré (> 100 MB drift sur 24h)
- Mode REAL : balance Binance incorrecte / clés rejetées

---

## Politique de re-test

T4 doit être ré-exécuté à chaque tag stable produisant un APK
substantiellement différent. Heuristique :

- **Smoke 5 min** : à chaque tag `v*` (rapide, vaut le coût)
- **24h observation** : à chaque tag avec changement runtime
  significatif (nouveau screen, refresh policy, persistence,
  Binance wiring, etc.)
- **Mode REAL** : à chaque tag touchant `BinanceClient`,
  `BinanceCredentialsService`, ou `BinanceBalanceProvider`

Les résultats sont archivés dans `docs/t4_runs/`. La dernière
run réussie peut être référencée dans le doc 06 §"Score MVP T4".
