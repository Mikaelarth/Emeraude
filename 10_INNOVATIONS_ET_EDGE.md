# 10 — Innovations & Edge concurrentiel

> **Objectif** : faire d'Emeraude un agent qui a **plusieurs coups
> d'avance** sur les bots retail standards, en s'attaquant aux **lacunes
> structurelles** que 99 % des concurrents ignorent.
>
> **Règle absolue** : aucune fonctionnalité fictive, aucun buzzword sans
> implémentation. Toute technique listée ici doit être :
> - implémentable dans notre stack (pure Python, sans NumPy, Android-compatible)
> - mesurable (un critère de succès chiffré)
> - documentée (référence académique ou industrielle)

---

## 1. Les 12 lacunes structurelles du trading algo retail

Documentées dans la littérature (López de Prado, Chan, Bailey & Borwein) et observables dans la majorité des bots open-source crypto :

| # | Lacune | Conséquence typique |
|:-:|---|---|
| L1 | **Confiance non calibrée** | Le bot affiche "85 %" mais ses 85 % gagnent 50 % du temps. |
| L2 | **Backtest optimiste** | Slippage/fees sous-estimés → résultats live ≈ 30 % en dessous du backtest. |
| L3 | **Drift de concept ignoré** | Paramètres figés sur 2021–2023 → désastre quand le régime change. |
| L4 | **Sur-apprentissage paramétrique** | Grille optimisée sur 1 historique → "champion" qui sur-fit le bruit. |
| L5 | **Black swan non préparé** | VaR Gaussienne aveugle aux queues → max DD ≫ VaR théorique. |
| L6 | **Microstructure ignorée** | Pas d'order flow, pas de spread monitoring → mauvaises entrées. |
| L7 | **Corrélations supposées stables** | En stress crypto, toutes les paires → corr 1 → portefeuille concentré. |
| L8 | **Overtrading systémique** | Pas de filtre "faut-il trader maintenant ?" → fees mangent l'edge. |
| L9 | **Exécution naïve** | Market order systématique → slippage évitable. |
| L10 | **Mémoire absente** | Aucun apprentissage cross-session significatif → bot reste novice. |
| L11 | **Pas de garanties statistiques** | Paramètres updatés sur le bruit → instabilité, oscillation. |
| L12 | **Reporting vanity** | Métriques décoratives, pas d'expectancy, pas de Kelly utilisé vs optimal. |

---

## 2. Nos 12 réponses (mappées 1-1 sur les lacunes)

### R1 — Calibration tracking (Brier score + ECE)

**Lacune adressée** : L1 (confiance non calibrée).

**Principe** : à chaque trade fermé, on enregistre `(confidence_prédite, succès_réel)`. Toutes les 50 fenêtres glissantes, on calcule :
- **Brier score** : `mean((p - outcome)²)` — proche de 0 = bien calibré
- **Expected Calibration Error (ECE)** : binning de la confiance, écart absolu confiance vs taux réel par bin
- **Reliability diagram** : visualisation utilisateur-friendly

Si ECE > 10 %, on **rescale** automatiquement les confidences via Platt scaling ou isotonic regression (online).

**Module** : `core/calibration.py` (à créer, pure Python ~150 lignes).
**Critère mesurable** : **ECE < 5 % sur 100 trades** consécutifs.
**Référence** : Niculescu-Mizil & Caruana 2005, *Predicting Good Probabilities With Supervised Learning*.

---

### R2 — Backtest adversarial (pessimisme par défaut)

**Lacune adressée** : L2 (backtest optimiste).

**Principe** : le backtest standard suppose une exécution parfaite. Nous adoptons l'hypothèse adverse :
- **Slippage** : 2× le slippage médian observé live (et non pas 0.05 % théorique)
- **Fees** : 1.1× les fees Binance (couvre les frais réseau et conversions)
- **Fill price** : pour un BUY, fill au **plus haut** du bar suivant (pessimiste). Pour SELL, au **plus bas**.
- **Gap risk** : ouverture du bar tirée d'une distribution empirique des gaps observés
- **Latency** : décalage de 1 bar entre signal et exécution (réaliste pour cycle 60 min)

Un mode `--adversarial` ajoute ces hypothèses ; le mode standard reste pour comparaison.

**Module** : extension `core/backtest.py`.
**Critère mesurable** : **écart backtest_adversarial vs trading réel ≤ 15 %** sur 30 jours.
**Référence** : Bailey, Borwein, López de Prado 2014, *The Probability of Backtest Overfitting*.

---

### R3 — Détection de drift de concept (Page-Hinkley + ADWIN)

**Lacune adressée** : L3 (drift ignoré).

**Principe** : le marché change silencieusement. Sans détection, le bot continue avec des paramètres obsolètes jusqu'au crash.

Nous implémentons **deux détecteurs en parallèle** sur la série des R-multiples par trade :
- **Page-Hinkley test** : détecte un changement de moyenne avec délai borné statistiquement
- **ADWIN (Adaptive Windowing)** : maintient deux sous-fenêtres et déclenche si leur moyenne diverge significativement

Quand un drift est détecté :
1. Réduction immédiate du `risk_pct` à 50 % de sa valeur courante
2. Notification Telegram à l'utilisateur (mode explication)
3. Trigger d'un `reoptimize` partiel (champion local recalibré sur fenêtre récente)

**Module** : `core/drift.py` (à créer, ~200 lignes).
**Critère mesurable** : **drift détecté ≤ 72 h après début de la dégradation** (testable via injection synthétique).
**Référence** : Bifet & Gavaldà 2007, *Learning from Time-Changing Data with Adaptive Windowing*.

---

### R4 — Walk-forward + parameter robustness check

**Lacune adressée** : L4 (sur-apprentissage paramétrique).

**Principe** : un "champion" trouvé par grid search est suspect tant qu'il n'a pas survécu à :
1. **Walk-forward analysis** : test out-of-sample sur fenêtres glissantes (déjà codé)
2. **Robustness check** : la perf doit tenir sur **perturbation ±20 %** de chaque paramètre individuellement

Si une petite perturbation détruit la perf → c'est un overfit local → on rejette ce champion.

Le rapport produit est une **heatmap de stabilité** : pour chaque paramètre, fraction des perturbations qui dégradent la perf > 30 %.

**Module** : extension `optimize_params.py` (option `--robustness-check`).
**Critère mesurable** : **fraction de perturbations destructives ≤ 25 %** pour le champion publié.
**Référence** : López de Prado 2018, *Advances in Financial Machine Learning*, ch. 11.

---

### R5 — Risque de queue (Cornish-Fisher VaR + bootstrap)

**Lacune adressée** : L5 (black swan non préparé).

**Principe** : la VaR Gaussienne suppose une distribution normale. En crypto, les pertes extrêmes sont **systématiquement sous-estimées** par cette hypothèse.

Nous calculons :
- **VaR 99 % Cornish-Fisher** : ajuste la VaR Gaussienne par la skewness et la kurtosis empiriques
- **CVaR (Expected Shortfall)** : moyenne des pertes au-delà de la VaR
- **Max position size** capée par : `max_position = min(kelly_fractional, capital × CVaR_99 × 0.5)`

→ Le bot **refuse** d'exposer plus de capital que ce que le pire 1 % historique permet de supporter.

**Module** : `core/risk_tail.py` (à créer, ~120 lignes), intégré dans `position_sizing.optimal_position_size`.
**Critère mesurable** : **max DD réel ≤ 1.2 × CVaR_99 prédit** sur 90 jours.
**Référence** : Favre & Galeano 2002, *Mean-Modified Value-at-Risk Optimization with Hedge Funds*.

---

### R6 — Microstructure : order flow + spread

**Lacune adressée** : L6 (microstructure ignorée).

**Principe** : avant d'entrer, le bot consulte des signaux de microstructure **gratuits** sur Binance :
- **aggTrades** (`/api/v3/aggTrades`) : ratio achats/ventes sur les 60 dernières secondes
- **bookTicker** : bid-ask spread instantané ; rejet d'entrée si spread > 0.15 %
- **klines 1m** : volume du bar courant vs moyenne 20-bar ; rejet si volume < 30 % de la moyenne

Ces filtres s'ajoutent **après** le signal multi-stratégies, comme garde-fou d'exécution.

**Module** : `core/microstructure.py` (à créer, ~200 lignes).
**Critère mesurable** : **+0.1 Sharpe minimum** apporté en walk-forward vs version sans microstructure.
**Référence** : Cont, Kukanov, Stoikov 2014, *The Price Impact of Order Book Events*.

---

### R7 — Régime de stress de corrélation

**Lacune adressée** : L7 (corrélations supposées stables).

**Principe** : en bull tranquille, BTC/ETH/SOL ont une corrélation ~0.5. En crash, elles vont à ~0.95 → diversification illusoire.

Nous calculons en continu la **corrélation moyenne par paires** sur les retours 1 h des coins suivis. Quand cette moyenne dépasse **0.8** (stress regime) :
- Réduction agressive du nombre max de positions simultanées (de 3 à 1)
- Pas de nouvelle entrée tant que ce régime persiste
- Notification utilisateur "régime de stress détecté"

**Module** : extension `core/correlation.py`.
**Critère mesurable** : **détection ≤ 1 cycle (60 min)** après franchissement du seuil ; **réduction effective des positions** dans le cycle suivant.
**Référence** : Forbes & Rigobon 2002, *No Contagion, Only Interdependence*.

---

### R8 — Meta-décision "should we trade now ?"

**Lacune adressée** : L8 (overtrading).

**Principe** : 99 % des bots se demandent "quel coin acheter ?". La meilleure question est souvent "**faut-il acheter quoi que ce soit aujourd'hui ?**".

Un meta-classifier (régression logistique online, ~100 lignes pure Python) prend en entrée :
- volatilité réalisée 24h
- distance au plus haut 30j
- volume vs moyenne 7j
- régime + transition
- corrélation moyenne (R7)
- heure UTC (les vendredis soir crypto sont volatiles)

Et produit un score `tradability ∈ [0, 1]`. Si `tradability < 0.4` → cycle skip (aucune entrée, gestion exits seulement).

**Module** : `core/meta_gate.py` (à créer, ~250 lignes).
**Critère mesurable** : **réduction du nombre de trades ≥ 30 %** sans réduction du PnL net (élimine le "trading dans le bruit").
**Référence** : López de Prado 2018, *Advances in Financial Machine Learning*, ch. 3 (Meta-Labeling).

---

### R9 — Exécution intelligente (smart limit + fallback)

**Lacune adressée** : L9 (exécution naïve).

**Principe** : un market order paie systématiquement le spread + slippage. Stratégie alternative :

1. **Place limit** au mid-price (ou mid - 1 tick côté BUY, mid + 1 tick côté SELL)
2. **TTL 30 secondes** : si non rempli, annulation
3. **Fallback market** si la fenêtre d'opportunité est encore valide (sinon abandon de l'ordre)

Cette stratégie capte le spread sur 60–80 % des ordres typiques en crypto liquide (BTC, ETH, top 10 alts).

**Module** : `core/execution.py` (à créer, ~180 lignes), wrapper autour de `exchange.place_market_order`.
**Critère mesurable** : **slippage moyen ≤ 0.05 % par trade** (vs ~0.15 % en market pur).
**Référence** : Almgren & Chriss 2001, *Optimal Execution of Portfolio Transactions*.

---

### R10 — Mémoire long-terme + checkpoint étendu

**Lacune adressée** : L10 (mémoire absente).

**Principe** : aujourd'hui, `core/checkpoint.py` sauvegarde l'état volatile. Nous étendons avec une **table `learning_history`** qui persiste :
- Tous les trades historiques avec features de contexte (régime, microstructure, méta-score)
- L'évolution des poids adaptatifs (Thompson α/β par stratégie × régime, par snapshot mensuel)
- Les décisions de drift detection (R3) avec timestamps
- Les calibration scores (R1) par fenêtre

**Au démarrage**, le bot reconstruit son état complet depuis cette table — pas seulement les positions ouvertes.

**Module** : extension `core/database.py` + `core/checkpoint.py`.
**Critère mesurable** : **100 % des états critiques restaurés** après kill -9 + relancement (testable).

---

### R11 — Hoeffding bounds sur les updates de paramètres

**Lacune adressée** : L11 (updates sur le bruit).

**Principe** : les bots adaptatifs naïfs updatent leurs paramètres après chaque trade. Sur 5 trades, c'est du bruit. Le test de Hoeffding donne une **borne statistique** sur la taille d'échantillon nécessaire pour différencier deux moyennes avec confiance `1 - δ`.

Concrètement : on n'update les poids d'une stratégie que si `|win_rate_observée - win_rate_prior| > ε(n, δ)` où `ε(n, δ) = sqrt(ln(2/δ) / (2n))`.

→ Pas d'updates avant ~30 trades. Stabilité garantie.

**Module** : extension `core/adaptive.py`.
**Critère mesurable** : **0 % d'updates basés sur < 30 trades** (audit trail trace chaque update + son sample size).
**Référence** : Domingos & Hulten 2000, *Mining High-Speed Data Streams (Hoeffding Trees)*.

---

### R12 — Reporting opérationnel (anti-vanity)

**Lacune adressée** : L12 (reporting fake).

**Principe** : un dashboard qui montre seulement "+12 % ROI" est vanity. Un dashboard pro doit montrer :

| Métrique | Pourquoi |
|---|---|
| **Expectancy par trade** ($) | Le bot gagne-t-il plus qu'il ne perd, en moyenne ? |
| **Sharpe ratio** (annualisé) | Rendement ajusté au risque |
| **Sortino ratio** | Pénalise seulement la volatilité baissière |
| **Calmar ratio** | Rendement / Max DD |
| **Win rate** + **R/R moyen** | Décomposition de l'expectancy |
| **Kelly utilisé vs optimal** | Sommes-nous trop prudent ou trop agressif ? |
| **Vs benchmark HODL BTC** | Le bot bat-il un simple "j'achète et j'attends" ? |
| **Slippage observé vs modélisé** | Notre exécution est-elle proche du backtest ? |
| **Calibration (ECE)** | Notre confiance est-elle honnête ? |
| **Tradability moyenne** (R8) | Sommes-nous dans un régime tradable ? |

→ Tout ça en **un écran lisible en 5 secondes**, pas 12 onglets.

**Module** : extension `core/metrics.py` + nouvel écran `screens/performance_screen.py`.
**Critère mesurable** : **temps de lecture utilisateur ≤ 5 s** pour comprendre l'état du bot.

---

### R13 — Probabilistic Sharpe Ratio + Deflated Sharpe Ratio

**Lacune adressée** : L4 / L11 — un Sharpe nu ne dit pas si la performance est statistiquement significative compte tenu de la taille d'échantillon, de la skewness/kurtosis observées, et du multiple testing inhérent au grid search.

**Principe** :

- **Probabilistic Sharpe Ratio (PSR)** : probabilité que le vrai Sharpe d'une stratégie excède un benchmark `SR*`, compte tenu des moments d'ordre supérieur observés. Formule :

  ```
  PSR(SR*) = Φ( (SR_hat - SR*) × sqrt(N - 1) / sqrt(1 - γ_3 × SR_hat + ((γ_4 - 1) / 4) × SR_hat²) )
  ```

  où `γ_3` = skewness, `γ_4` = kurtosis, `N` = nombre de retours observés, `Φ` = CDF gaussienne standard.

- **Deflated Sharpe Ratio (DSR)** : corrige le PSR pour le multiple testing inhérent à un grid search de `K` configurations. Évite de promouvoir un "champion" qui n'est qu'un artefact de l'optimisation.

Le bot affiche pour chaque champion : Sharpe nu, PSR(SR* = 0), DSR. **Aucune promotion sans DSR > 0.95**.

**Module** : `core/statistical_significance.py` (à créer, pure Python ~80 lignes).
**Critère mesurable** : **DSR ≥ 0.95** pour le champion en production ; tracé dans le rapport walk-forward.
**Référence** : Bailey & López de Prado 2012, *The Sharpe Ratio Efficient Frontier* ; Bailey & López de Prado 2014, *The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality*.

---

### R14 — Bandit contextuel (LinUCB) au lieu d'UCB1 plat

**Lacune adressée** : L3 / L10 — UCB1 + RegimeMemory séparé est une factorisation grossière du problème. Un **bandit contextuel** unifie ces deux mécanismes en apprenant `argmax_strategy(régime, volatilité, heure UTC, distance ATH, corrélation moyenne)` au lieu de `argmax_strategy(régime)` puis `argmax_param(stratégie)` séparément.

**Principe** : LinUCB suppose une espérance linéaire de la récompense en fonction du contexte `x_t ∈ R^d` :

```
E[r_t | a, x_t] = θ_a^T × x_t
```

À chaque cycle, il met à jour `θ_a` par moindres carrés régularisés (Ridge), et choisit l'action `a` qui maximise :

```
score_a = θ_a^T × x_t  +  α × sqrt(x_t^T × A_a^{-1} × x_t)
```

(terme d'exploration analogue à UCB1, mais **modulé par le contexte**).

Avantages sur UCB1 + RegimeMemory séparé :
- Une seule structure unifiée (plus simple à raisonner et à auditer)
- Apprentissage cross-régime : un bon trend follower observé en bull peut prédire son comportement en neutral
- Continuité naturelle entre régimes (pas de bascule brutale au franchissement de seuil)

L'inversion de matrice (taille d×d, typiquement d=5 à 10) est trivialement faisable sans NumPy via la formule de Sherman-Morrison appliquée à chaque update — coût O(d²) par update, négligeable.

**Module** : `core/contextual_bandit.py` (à créer, ~200 lignes pure Python).
**Critère mesurable** : **+0.15 Sharpe minimum** vs UCB1 + RegimeMemory séparé en walk-forward sur 90 j.
**Référence** : Li, Chu, Langford, Schapire 2010, *A Contextual-Bandit Approach to Personalized News Article Recommendation*.

---

### R15 — Conformal Prediction pour intervalles de confiance distribution-free

**Lacune adressée** : L1 — la calibration via Brier/ECE (R1) mesure la qualité moyenne ; la **conformal prediction** fournit des **intervalles de prédiction avec garantie de couverture finie**, sans hypothèse distributionnelle.

**Principe** : pour une nouvelle prédiction `ŷ_t` (PnL anticipé d'un trade), on construit un intervalle :

```
[ŷ_t - q_(1-α), ŷ_t + q_(1-α)]
```

où `q_(1-α)` est le quantile `(1-α)` des **résidus de calibration** observés sur les N derniers trades. Garantie théorique :

```
P( y_real ∈ intervalle )  ≥  1 - α
```

à échantillon fini, **sans hypothèse Gaussienne ni stationnarité forte** (seulement échangeabilité asymptotique).

Application Emeraude :
- À chaque signal qualifié, le bot affiche : "PnL anticipé +0.8R [intervalle 90 % : -0.2R à +1.6R]"
- Si l'intervalle franchit majoritairement zéro → signal **dégradé en HOLD** (cohérent avec A4)
- Calibration online : la fenêtre des résidus glisse, garantissant l'adaptation aux régimes
- Variante **adaptive conformal prediction** (Gibbs & Candès 2021) pour gérer le drift, en synergie avec R3

**Module** : `core/conformal.py` (à créer, ~150 lignes pure Python).
**Critère mesurable** : **couverture empirique ∈ [85 %, 95 %]** sur 100 trades pour un niveau cible de 90 % (preuve que la garantie tient en pratique).
**Référence** : Vovk, Gammerman, Shafer 2005, *Algorithmic Learning in a Random World* ; Angelopoulos & Bates 2021, *A Gentle Introduction to Conformal Prediction and Distribution-Free Uncertainty Quantification* ; Gibbs & Candès 2021, *Adaptive Conformal Inference Under Distribution Shift*.

---

## 3. Synthèse : maturité actuelle vs cible

| # | Réponse | Module | État | Complexité | Priorité |
|:-:|---|---|:-:|:-:|:-:|
| R1 | Calibration tracking | `calibration.py` | ❌ | Moyenne | Haute |
| R2 | Backtest adversarial | extension `backtest.py` | ⚠️ partiel | Moyenne | Haute |
| R3 | Drift detection | `drift.py` | ❌ | Élevée | Haute |
| R4 | Robustness check | extension `optimize_params.py` | ⚠️ partiel | Faible | Moyenne |
| R5 | Risque de queue | `risk_tail.py` | ❌ | Moyenne | Haute |
| R6 | Microstructure | `microstructure.py` | ❌ | Élevée | Moyenne |
| R7 | Corrélation stress | extension `correlation.py` | ⚠️ partiel | Faible | Haute |
| R8 | Meta-gate | `meta_gate.py` | ❌ | Élevée | Haute |
| R9 | Exécution intelligente | `execution.py` | ❌ | Moyenne | Moyenne |
| R10 | Mémoire long-terme | extension DB + checkpoint | ⚠️ partiel | Moyenne | Haute |
| R11 | Hoeffding bounds | extension `adaptive.py` | ❌ | Faible | Haute |
| R12 | Reporting opérationnel | extension `metrics.py` + écran | ⚠️ partiel | Faible | Haute |
| R13 | PSR + Deflated Sharpe | `statistical_significance.py` | ❌ | Faible | Haute |
| R14 | LinUCB contextuel | `contextual_bandit.py` | ❌ | Moyenne | Moyenne |
| R15 | Conformal Prediction | `conformal.py` | ❌ | Moyenne | Haute |

**Score actuel** : **0/15 ✅, 5/15 ⚠️, 10/15 ❌**.

---

## 4. Critères de mesure d'avantage (I1–I12)

À ajouter aux critères de terminaison (document 06) :

| # | Critère | Validation |
|:-:|---|---|
| I1 | ECE de calibration < 5 % sur 100 trades | Tracé reliability diagram |
| I2 | Écart backtest adversarial vs réel ≤ 15 % | Comparaison sur 30j live |
| I3 | Drift détecté ≤ 72h sur injection synthétique | Test reproductible |
| I4 | Champion robuste à ±20 % perturbation paramètres | Heatmap stabilité |
| I5 | Max DD réel ≤ 1.2 × CVaR_99 | 90j historique |
| I6 | Microstructure apporte ≥ +0.1 Sharpe | Walk-forward A/B |
| I7 | Régime stress détecté ≤ 1 cycle | Test reproductible |
| I8 | Meta-gate réduit trades ≥ 30 % sans baisse PnL | Backtest A/B |
| I9 | Slippage moyen ≤ 0.05 % par trade | Audit live 100 trades |
| I10 | 100 % états critiques restaurés après kill -9 | Test E2E |
| I11 | 0 % updates de poids sur < 30 trades | Audit trail |
| I12 | Dashboard performance lisible ≤ 5 s | Test utilisateur |
| I13 | DSR ≥ 0.95 pour champion en production | Rapport walk-forward |
| I14 | LinUCB +0.15 Sharpe minimum vs UCB1+RegimeMemory séparé | Walk-forward A/B sur 90 j |
| I15 | Couverture conformale empirique ∈ [85 %, 95 %] sur 100 trades | Audit live |

---

## 5. Phase d'implémentation suggérée

### Phase A — Fondations statistiques (1 mois)
R1 (calibration), R11 (Hoeffding), R5 (tail risk), R12 (reporting).
→ **Honnêteté** d'abord : on ne peut pas s'améliorer si on ne mesure pas correctement.

### Phase B — Détection de régime (1 mois)
R3 (drift), R7 (corrélation stress), R8 (meta-gate).
→ **Savoir quand ne pas trader** est la moitié de l'edge.

### Phase C — Exécution & microstructure (1 mois)
R6 (microstructure), R9 (exécution), R2 (backtest adversarial).
→ Optimiser le **dernier pourcent** : différence entre amateur et pro.

### Phase D — Mémoire & robustesse (continu)
R4 (robustness), R10 (mémoire long-terme).
→ **Pérennité** : éviter que le bot redevienne novice à chaque crash.

---

## 6. Anti-vanity : ce que nous ne ferons PAS

Pour rester honnête et focalisé, voici les techniques **explicitement écartées** :

| Technique | Pourquoi rejetée |
|---|---|
| ❌ LLM-based trading | Latence + coût + pas d'edge documenté en intraday crypto |
| ❌ Deep RL (DQN, PPO) | Incompatible Android pure Python sans NumPy/PyTorch |
| ❌ Sentiment Twitter/Reddit | Signal trop bruité, dépendances externes fragiles |
| ❌ HFT / scalping ms | Impossible sur smartphone Android, latence réseau |
| ❌ Copy trading | L'utilisateur veut un agent **indépendant**, pas un suiveur |
| ❌ Marketing "quantum" / "neural" | Si on ne peut pas le coder en pure Python, on ne le claim pas |
| ❌ Indicateurs ésotériques (Ichimoku 6 lignes, Gann, Elliott) | Pas de validation académique solide en crypto |
| ❌ Promesses de % de rendement | Honnêteté > marketing |

---

## 7. Champion lifecycle (cycle de vie d'une configuration)

Le champion actuel (Sharpe walk-forward +0.93) a été trouvé sur un
historique fini. **Aucune configuration n'est éternelle**. Sans
politique d'expiration, un bot finit par trader avec des paramètres
obsolètes — la cause #1 de la mort silencieuse.

### Les 4 états d'un champion

```
┌──────────────┐    re-validation    ┌──────────────┐
│   ACTIF      │ ──── mensuelle ───► │   ACTIF      │
│  (en prod)   │ ◄─── passe ✅ ────  │  (renouvelé) │
└──────┬───────┘                     └──────────────┘
       │
       │ re-validation échoue 1×
       ▼
┌──────────────┐    re-validation    ┌──────────────┐
│   SUSPECT    │ ──── mensuelle ───► │   EXPIRÉ     │
│ (sizing /2)  │ ◄─── 2 échecs ────► │ (re-optim    │
└──────────────┘                     │  forcée)     │
                                     └──────────────┘
```

| État | Conditions | Conséquence |
|---|---|---|
| **ACTIF** | Live Sharpe ≥ 50 % du Sharpe walk-forward attendu | Position sizing nominal |
| **SUSPECT** | 1 mois consécutif < seuil OU drift détecté | Sizing /2, alerte utilisateur |
| **EXPIRÉ** | 2 mois consécutifs < seuil | **Re-optimisation forcée**, sizing /4 jusqu'à validation du nouveau champion |
| **EN VALIDATION** | Nouveau champion candidat trouvé | Walk-forward + robustness check (R4) requis avant promotion |

### Re-validation mensuelle automatique

Le 1er de chaque mois (ou au cycle suivant si offline), `core/champion_lifecycle.py`
(à créer) exécute :

1. **Walk-forward sur fenêtre roulante** des 90 derniers jours
2. Compare Sharpe live (30 derniers jours réels) vs Sharpe walk-forward attendu
3. Mesure le **drift cumulé** depuis le dernier champion (Page-Hinkley, cf. R3)
4. Décide la transition d'état (ACTIF → SUSPECT, etc.)
5. Persiste l'événement en `audit_log` type `champion_revalidation`
6. Notifie l'utilisateur si transition d'état

### Politique de re-optimisation

Quand l'état passe en EXPIRÉ :

1. Trigger automatique de `optimize_params.py --walk-forward --robustness-check`
2. Le candidat doit passer **les 3 tests** :
   - Walk-forward Sharpe ≥ seuil cible (cf. doc 06, T8 durci)
   - Robustness check ±20 % (cf. R4)
   - Out-of-sample sur fenêtre **post-champion-précédent** (pas de
     contamination par les données qui ont fait gagner l'ancien)
3. Si validé → promotion, ancien champion archivé en `champion_history`
4. Si aucun candidat ne passe → **bot reste en sizing /4** jusqu'à
   intervention utilisateur (l'edge est peut-être perdu, ne pas forcer)

### Audit trail

Table `champion_history` (à créer) :

```sql
champion_history (
    id INTEGER PRIMARY KEY,
    champion_id TEXT,           -- hash des paramètres
    state TEXT,                 -- ACTIVE / SUSPECT / EXPIRED
    promoted_at TIMESTAMP,
    expired_at TIMESTAMP,
    sharpe_walk_forward REAL,
    sharpe_live REAL,
    expiry_reason TEXT,
    parameters_json TEXT
)
```

→ Permet de **tracer l'historique** : quel champion a régné quand,
combien il a gagné/perdu, pourquoi il a été remplacé.

### Critères mesurables (CL1-CL4)

| # | Critère | Validation |
|:-:|---|---|
| CL1 | Re-validation exécutée 1×/mois minimum | audit trail |
| CL2 | Transition d'état ≤ 1 cycle après déclenchement | audit trail |
| CL3 | Aucun champion EXPIRÉ utilisé en sizing nominal | audit trail |
| CL4 | `champion_history` liste tous les champions passés | DB query |

### Anti-pattern : ce qu'on ne fera jamais

- ❌ Re-optimiser à chaque drawdown (chasse de bruit, sur-fit garanti)
- ❌ Promouvoir un champion sur la base d'un seul backtest in-sample
- ❌ Garder un champion EXPIRÉ "pour ne pas changer une équipe qui gagne"
  → c'est précisément quand elle ne gagne plus qu'il faut changer
- ❌ Re-optimiser silencieusement sans alerte utilisateur

---

## 8. Garantie d'authenticité

**Aucune ligne de ce document n'est aspirationnelle au sens marketing.** Chaque technique :

1. A une **référence académique ou industrielle** publique
2. Est **implémentable en pure Python** (pas de NumPy ni dépendance lourde)
3. A un **critère de mesure chiffré** (pas "améliore l'expérience")
4. Sera **auditable** dans le code (chaque module produit du logging structuré)

Si une technique ne peut pas être codée + mesurée + auditée, **elle ne fait pas partie du projet**.

---

## 9. Position concurrentielle visée

Après implémentation des 15 réponses + champion lifecycle, Emeraude sera :

- **Plus calibré** que 99 % des bots retail (R1, R11, R15)
- **Plus honnête en backtest et en promotion de champion** que la plupart des produits commerciaux (R2, R13)
- **Plus réactif aux changements de régime** que les bots à paramètres figés (R3, R7, R8, R14)
- **Plus efficace en exécution** que les bots full-market-order (R6, R9)
- **Plus robuste aux black swans** que les bots à VaR Gaussienne (R5)
- **Plus persistant en apprentissage** que les bots stateless (R10)
- **Plus rigoureux statistiquement** que les bots à updates ad-hoc (R11, R13, R15)

→ Position visée : **niveau institutional-grade sur smartphone Android, ouvert et auditable**.

---

*v1.2 — 2026-04-25 — ajout R13/R14/R15 + I13/I14/I15 (PSR/Deflated SR, LinUCB contextuel, Conformal Prediction)*

*v1.1 — 2026-04-25 — ajout §7 Champion lifecycle (CL1-CL4)*
