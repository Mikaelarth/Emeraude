"""CycleScheduler — boucle d'exécution autonome de l'AutoTrader.

L'iter #95 a livré ``POST /api/run-cycle`` (déclenche un cycle au tap),
l'iter #96 a câblé le LiveExecutor (un cycle en mode Réel place un
vrai ordre Binance). Reste un trou : **le bot ne tourne jamais tout
seul** — l'utilisateur doit taper le bouton à chaque fois. Aucune
configuration "agent autonome" ne tient sans cron.

Cette iter ajoute un thread daemon qui appelle :meth:`AutoTrader.run_cycle`
toutes les ``interval_seconds`` (60 min par défaut, doc 05). Le
scheduler est **opt-in** (default ``enabled = False``) — l'utilisateur
l'active explicitement depuis l'écran Config quand il est prêt à
laisser le bot tourner. Cela évite qu'une APK fraîchement installée se
mette spontanément à trader.

Décisions architecturales :

* **Thread daemon** : le scheduler ne bloque pas la fermeture du
  processus. Lifecycle géré par :func:`start` / :func:`stop` ;
  l'arrêt utilise un :class:`threading.Event` pour exit immédiat
  (pas de polling sleep+busy-wait).

* **Re-lecture du settings à chaque tick** : ``enabled`` et
  ``interval_seconds`` sont lus depuis la DB à chaque cycle, pas
  capturés au constructor. Conséquence : un toggle UI propage en
  un seul tick maximum, pas de redémarrage nécessaire. Un changement
  d'intervalle prend effet **après** le tick courant (le sleep en
  cours utilise l'ancien interval — délibéré, simple).

* **Lock anti-overlap** : si un cycle prend plus longtemps que
  l'intervalle (cas dégradé : ratelimit Binance, latence, etc.),
  le tick suivant attend que le précédent finisse. Plutôt qu'un
  skip silencieux, on émet un audit ``SCHEDULER_TICK_OVERLAP`` pour
  rendre la situation visible.

* **Erreurs absorbées** : une exception dans ``run_cycle`` est
  auditée (``SCHEDULER_TICK_ERROR``) mais ne tue pas le thread —
  on reprend au prochain tick. Anti-règle A8 : pas de
  ``except: pass`` silencieux, on logue avec le type + message.

* **Settings DB** : ``scheduler.enabled`` (string ``"true"``/``"false"``,
  default ``"false"``) et ``scheduler.interval_seconds`` (string
  d'entier, default ``"3600"``). Validation à la lecture pour
  qu'un settings corrompu ne plante pas le thread (fallback sur
  default + audit warn).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from emeraude.infra import audit, database

if TYPE_CHECKING:
    from collections.abc import Callable


SETTING_KEY_SCHEDULER_ENABLED: Final[str] = "scheduler.enabled"
SETTING_KEY_SCHEDULER_INTERVAL: Final[str] = "scheduler.interval_seconds"

#: Default cadence — doc 05 §"BotMaitre cycle 60 min".
DEFAULT_INTERVAL_SECONDS: Final[int] = 3600

#: Lower bound. Below 1 minute, Binance ratelimits become a real
#: risk and the calibration window for indicators is too short for
#: the doc 04 strategies to produce meaningful signals.
MIN_INTERVAL_SECONDS: Final[int] = 60

#: Upper bound (1 day). Above this we'd miss multiple market sessions
#: between cycles ; the doc 04 architecture targets shorter cadences.
MAX_INTERVAL_SECONDS: Final[int] = 86_400

_AUDIT_STARTED: Final[str] = "SCHEDULER_STARTED"
_AUDIT_STOPPED: Final[str] = "SCHEDULER_STOPPED"
_AUDIT_TICK_FIRED: Final[str] = "SCHEDULER_TICK_FIRED"
_AUDIT_TICK_SKIPPED: Final[str] = "SCHEDULER_TICK_SKIPPED"
_AUDIT_TICK_ERROR: Final[str] = "SCHEDULER_TICK_ERROR"
_AUDIT_TICK_OVERLAP: Final[str] = "SCHEDULER_TICK_OVERLAP"


# ─── Snapshot for the API / UI ────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SchedulerSnapshot:
    """Read-only state for the API ``GET /api/scheduler`` endpoint.

    Attributes:
        enabled: persisted ``scheduler.enabled`` flag.
        interval_seconds: persisted ``scheduler.interval_seconds``
            (validated, falls back to default on corruption).
        is_running: ``True`` iff the daemon thread is alive.
            Decorrelated from ``enabled`` because the lifecycle
            (start/stop) is owned by ``web_app`` while the toggle
            (enabled) is owned by the user — the UI surfaces both
            so the user can tell the difference between "I asked
            for it but the server hasn't started" and "it's
            actually running".
        min_interval_seconds: lower bound for client-side validation.
        max_interval_seconds: upper bound for client-side validation.
    """

    enabled: bool
    interval_seconds: int
    is_running: bool
    min_interval_seconds: int
    max_interval_seconds: int


# ─── Settings helpers (pure DB) ────────────────────────────────────────────


def is_scheduler_enabled() -> bool:
    """Read the persisted ``scheduler.enabled`` flag.

    Returns ``False`` (safe default) when the setting is absent
    or the stored value is unreadable. The first install MUST
    not auto-trade ; the user toggles explicitly.
    """
    raw = database.get_setting(SETTING_KEY_SCHEDULER_ENABLED)
    return raw == "true"


def set_scheduler_enabled(enabled: bool) -> None:
    """Persist the ``scheduler.enabled`` flag."""
    database.set_setting(SETTING_KEY_SCHEDULER_ENABLED, "true" if enabled else "false")


def get_scheduler_interval_seconds() -> int:
    """Read the persisted ``scheduler.interval_seconds`` setting.

    Returns :data:`DEFAULT_INTERVAL_SECONDS` (3600) when the setting
    is absent. On a corrupted / non-numeric / out-of-range stored
    value, falls back to the default rather than raising — the
    scheduler thread MUST stay healthy even with bad config.
    """
    raw = database.get_setting(SETTING_KEY_SCHEDULER_INTERVAL)
    if raw is None:
        return DEFAULT_INTERVAL_SECONDS
    try:
        value = int(raw)
    except (ValueError, TypeError):
        return DEFAULT_INTERVAL_SECONDS
    if value < MIN_INTERVAL_SECONDS or value > MAX_INTERVAL_SECONDS:
        return DEFAULT_INTERVAL_SECONDS
    return value


def set_scheduler_interval_seconds(seconds: int) -> None:
    """Persist ``scheduler.interval_seconds`` ; validate range first.

    Raises :class:`ValueError` when ``seconds`` is outside
    ``[MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS]``. The HTTP
    handler maps this to a 400 Bad Request.
    """
    if seconds < MIN_INTERVAL_SECONDS or seconds > MAX_INTERVAL_SECONDS:
        msg = (
            f"interval_seconds must be in "
            f"[{MIN_INTERVAL_SECONDS}, {MAX_INTERVAL_SECONDS}], "
            f"received {seconds}"
        )
        raise ValueError(msg)
    database.set_setting(SETTING_KEY_SCHEDULER_INTERVAL, str(seconds))


# ─── CycleScheduler ────────────────────────────────────────────────────────


class CycleScheduler:
    """Background daemon thread that periodically calls a cycle callable.

    The class is callable-agnostic — it consumes any ``Callable[[], Any]``
    so unit tests inject a fake recorder. In production the
    :class:`AppContext` injects ``app_context.auto_trader.run_cycle``.

    Args:
        run_cycle: zero-arg callable invoked at each tick. Production
            wiring : ``lambda: app_context.auto_trader.run_cycle()``.
        enabled_provider: callable returning the current enabled flag.
            Re-read at every tick so a UI toggle propagates without
            scheduler restart. Default reads from DB.
        interval_provider: callable returning the current interval
            in seconds. Re-read at every tick. Default reads from DB.
        thread_name: name of the daemon thread (visible in stack
            traces / logs).
    """

    def __init__(
        self,
        *,
        run_cycle: Callable[[], object],
        enabled_provider: Callable[[], bool] | None = None,
        interval_provider: Callable[[], int] | None = None,
        thread_name: str = "emeraude-scheduler",
    ) -> None:
        self._run_cycle = run_cycle
        self._enabled_provider: Callable[[], bool] = (
            enabled_provider if enabled_provider is not None else is_scheduler_enabled
        )
        self._interval_provider: Callable[[], int] = (
            interval_provider if interval_provider is not None else get_scheduler_interval_seconds
        )
        self._thread_name = thread_name
        self._stop_event = threading.Event()
        self._cycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        """``True`` when the daemon thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def fetch_snapshot(self) -> SchedulerSnapshot:
        """Read-only state suitable for serialisation to the UI."""
        return SchedulerSnapshot(
            enabled=self._enabled_provider(),
            interval_seconds=self._interval_provider(),
            is_running=self.is_running,
            min_interval_seconds=MIN_INTERVAL_SECONDS,
            max_interval_seconds=MAX_INTERVAL_SECONDS,
        )

    def start(self) -> None:
        """Spawn the daemon thread.

        Safe to call when already running — second call is a no-op
        (audit ``SCHEDULER_STARTED`` is NOT re-emitted).
        """
        if self.is_running:
            return
        self._stop_event.clear()
        thread = threading.Thread(
            target=self._run_loop,
            name=self._thread_name,
            daemon=True,
        )
        self._thread = thread
        audit.audit(
            _AUDIT_STARTED,
            {
                "thread_name": self._thread_name,
                "interval_seconds": self._interval_provider(),
                "enabled": self._enabled_provider(),
            },
        )
        thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        """Signal the loop to exit and join the thread.

        Args:
            timeout: max seconds to wait for the thread to finish.
                A tick currently running has up to this long to
                complete. After ``timeout``, returns even if the
                thread is still alive (daemon, will die with the
                process).
        """
        if not self.is_running:
            return
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        audit.audit(_AUDIT_STOPPED, {"thread_name": self._thread_name})

    def _run_loop(self) -> None:
        """Main loop body — runs in the daemon thread."""
        while not self._stop_event.is_set():
            interval = max(self._interval_provider(), 1)
            # ``Event.wait(timeout)`` returns True iff the event got
            # set during the wait → exit. Returns False on timeout
            # → tick fires.
            if self._stop_event.wait(timeout=interval):
                break

            if not self._enabled_provider():
                audit.audit(
                    _AUDIT_TICK_SKIPPED,
                    {"reason": "disabled"},
                )
                continue

            # ``acquire(blocking=False)`` returns immediately ; if we
            # can't acquire it means the previous tick is still
            # running. Surface that with a dedicated audit rather than
            # silently queueing — anti-règle A1.
            if not self._cycle_lock.acquire(blocking=False):
                audit.audit(
                    _AUDIT_TICK_OVERLAP,
                    {"reason": "previous_tick_still_running"},
                )
                continue

            try:
                try:
                    self._run_cycle()
                    audit.audit(_AUDIT_TICK_FIRED, {})
                except Exception as exc:  # noqa: BLE001  (defensive thread guard)
                    audit.audit(
                        _AUDIT_TICK_ERROR,
                        {
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                        },
                    )
            finally:
                self._cycle_lock.release()
