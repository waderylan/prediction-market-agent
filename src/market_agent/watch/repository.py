"""Storage-neutral watch repository with complete SQLite and Firestore adapters."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

from market_agent.watch.lifecycle import condition_summary
from market_agent.watch.models import (
    DeliveryChannel,
    OutboxItem,
    OutboxStatus,
    WatchEvent,
    WatchLifecycleEvent,
    WatchObservation,
    WatchRule,
    WatchRuntimeState,
    WatchRuntimeSummary,
    WatchStatus,
    WatchTrigger,
)


def _text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _outbox(data: dict[str, Any]) -> OutboxItem:
    available_at = data["available_at"]
    lease_until = data.get("lease_until")
    return OutboxItem(
        outbox_id=data["outbox_id"],
        trigger_id=data["trigger_id"],
        watch_id=data["watch_id"],
        status=OutboxStatus(data["status"]),
        attempts=data["attempts"],
        available_at=_time(available_at) if isinstance(available_at, str) else available_at,
        lease_owner=data.get("lease_owner"),
        lease_until=_time(lease_until) if isinstance(lease_until, str) else lease_until,
        provider_message_id=data.get("provider_message_id"),
        error_class=data.get("error_class"),
    )


class WatchRepository(Protocol):
    def save_rule(self, rule: WatchRule, *, due_at: datetime) -> None: ...
    def get_rule(self, watch_id: str, session_id: str) -> WatchRule | None: ...
    def list_rules(self, session_id: str) -> list[WatchRule]: ...
    def replace_rule(self, rule: WatchRule, session_id: str) -> None: ...
    def set_status(self, watch_id: str, session_id: str, status: WatchStatus) -> bool: ...
    def delete_rule(self, watch_id: str, session_id: str) -> bool: ...
    def claim_due(self, owner: str, now: datetime, limit: int = 100) -> list[WatchRule]: ...
    def release_claim(self, watch_id: str, owner: str, due_at: datetime) -> None: ...
    def add_observation(self, watch_id: str, observation: WatchObservation) -> None: ...
    def recent_observations(
        self, watch_id: str, since: datetime, limit: int = 120
    ) -> list[WatchObservation]: ...
    def condition_state(self, watch_id: str, condition_id: str) -> tuple[bool, datetime | None]: ...
    def set_condition_state(
        self, watch_id: str, condition_id: str, armed: bool, fired_at: datetime | None
    ) -> None: ...
    def record_trigger(self, rule: WatchRule, trigger: WatchTrigger) -> bool: ...
    def list_triggers(self, session_id: str, limit: int = 50) -> list[WatchTrigger]: ...
    def claim_outbox(self, owner: str, now: datetime, limit: int = 20) -> list[OutboxItem]: ...
    def finish_outbox(
        self,
        outbox_id: str,
        owner: str,
        status: OutboxStatus,
        *,
        available_at: datetime,
        provider_message_id: str | None = None,
        error_class: str | None = None,
    ) -> bool: ...
    def outbox_for_trigger(self, trigger_id: str) -> OutboxItem | None: ...
    def trigger_by_id(self, trigger_id: str) -> WatchTrigger | None: ...
    def cleanup(self, now: datetime) -> tuple[int, int]: ...
    def runtime(self, watch_id: str, session_id: str) -> WatchRuntimeSummary | None: ...
    def list_runtime(self, session_id: str, status: str = "all") -> list[WatchRuntimeSummary]: ...
    def record_runtime(
        self,
        rule: WatchRule,
        owner: str,
        runtime: WatchRuntimeSummary,
        event: WatchLifecycleEvent | None,
    ) -> bool: ...
    def list_events(self, session_id: str, limit: int = 50) -> list[WatchEvent]: ...
    def event_by_id(self, event_id: str) -> WatchEvent | None: ...


def _initial_runtime(rule: WatchRule, due_at: datetime) -> WatchRuntimeSummary:
    return WatchRuntimeSummary(
        watch_id=rule.watch_id,
        game=rule.game,
        condition_summary=condition_summary(rule),
        desired_status=rule.status,
        next_due_at=due_at,
    )


def _updated_runtime(
    rule: WatchRule,
    previous: WatchRuntimeSummary,
    kind: str,
) -> WatchRuntimeSummary:
    return _initial_runtime(rule, rule.monitoring_starts_at).model_copy(
        update={
            "activation_epoch": previous.activation_epoch + 1,
            "activation_kind": kind,
        }
    )


class SQLiteWatchRepository:
    """Transactional local store; every mutation uses an immediate write transaction."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as db:
            if self.path != ":memory:":
                db.execute("PRAGMA journal_mode = WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS watches (
                  watch_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, status TEXT NOT NULL,
                  schema_version INTEGER NOT NULL, data TEXT NOT NULL, due_at TEXT NOT NULL,
                  lease_owner TEXT, lease_until TEXT, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS watches_due ON watches(status, due_at);
                CREATE INDEX IF NOT EXISTS watches_session ON watches(session_id, created_at);
                CREATE TABLE IF NOT EXISTS observations (
                  observation_id TEXT NOT NULL, watch_id TEXT NOT NULL,
                  observed_at TEXT NOT NULL, terminal_evidence INTEGER NOT NULL DEFAULT 0,
                  data TEXT NOT NULL,
                  PRIMARY KEY(watch_id, observation_id),
                  FOREIGN KEY(watch_id) REFERENCES watches(watch_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS observations_watch_time
                  ON observations(watch_id, observed_at);
                CREATE TABLE IF NOT EXISTS condition_runtime (
                  watch_id TEXT NOT NULL, condition_id TEXT NOT NULL, armed INTEGER NOT NULL,
                  fired_at TEXT, PRIMARY KEY(watch_id, condition_id),
                  FOREIGN KEY(watch_id) REFERENCES watches(watch_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS triggers (
                  trigger_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
                  watch_id TEXT NOT NULL, session_id TEXT NOT NULL, triggered_at TEXT NOT NULL,
                  data TEXT NOT NULL,
                  FOREIGN KEY(watch_id) REFERENCES watches(watch_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS triggers_session_time
                  ON triggers(session_id, triggered_at DESC);
                CREATE TABLE IF NOT EXISTS trigger_evidence (
                  trigger_id TEXT NOT NULL, watch_id TEXT NOT NULL, observation_id TEXT NOT NULL,
                  PRIMARY KEY(trigger_id, observation_id),
                  FOREIGN KEY(trigger_id) REFERENCES triggers(trigger_id) ON DELETE CASCADE,
                  FOREIGN KEY(watch_id, observation_id)
                    REFERENCES observations(watch_id, observation_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS outbox (
                  outbox_id TEXT PRIMARY KEY, trigger_id TEXT NOT NULL, watch_id TEXT NOT NULL,
                  channel TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL,
                  available_at TEXT NOT NULL, lease_owner TEXT, lease_until TEXT,
                  provider_message_id TEXT, error_class TEXT,
                  UNIQUE(trigger_id, channel),
                  FOREIGN KEY(trigger_id) REFERENCES triggers(trigger_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS outbox_due ON outbox(status, available_at);
                CREATE TABLE IF NOT EXISTS lifecycle_events (
                  event_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
                  watch_id TEXT NOT NULL, session_id TEXT NOT NULL, occurred_at TEXT NOT NULL,
                  data TEXT NOT NULL,
                  FOREIGN KEY(watch_id) REFERENCES watches(watch_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS lifecycle_session_time
                  ON lifecycle_events(session_id, occurred_at DESC);
                CREATE TABLE IF NOT EXISTS lifecycle_evidence (
                  event_id TEXT NOT NULL, watch_id TEXT NOT NULL, observation_id TEXT NOT NULL,
                  PRIMARY KEY(event_id, observation_id),
                  FOREIGN KEY(event_id) REFERENCES lifecycle_events(event_id) ON DELETE CASCADE,
                  FOREIGN KEY(watch_id, observation_id)
                    REFERENCES observations(watch_id, observation_id) ON DELETE CASCADE
                );
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(watches)")}
            if "runtime_data" not in columns:
                db.execute("ALTER TABLE watches ADD COLUMN runtime_data TEXT")
            # Legacy outbox references triggers only. Lifecycle events need the same queue.
            if list(db.execute("PRAGMA foreign_key_list(outbox)")):
                db.execute("BEGIN IMMEDIATE")
                try:
                    db.execute("""CREATE TABLE outbox_events (
                      outbox_id TEXT PRIMARY KEY, trigger_id TEXT NOT NULL,
                      watch_id TEXT NOT NULL, channel TEXT NOT NULL, status TEXT NOT NULL,
                      attempts INTEGER NOT NULL, available_at TEXT NOT NULL,
                      lease_owner TEXT, lease_until TEXT, provider_message_id TEXT,
                      error_class TEXT, UNIQUE(trigger_id, channel),
                      FOREIGN KEY(watch_id) REFERENCES watches(watch_id) ON DELETE CASCADE
                    )""")
                    db.execute("INSERT INTO outbox_events SELECT * FROM outbox")
                    db.execute("DROP TABLE outbox")
                    db.execute("ALTER TABLE outbox_events RENAME TO outbox")
                    db.execute(
                        "CREATE INDEX IF NOT EXISTS outbox_due ON outbox(status, available_at)"
                    )
                    db.commit()
                except Exception:
                    db.rollback()
                    raise

    def _write(self, operation: Callable[[sqlite3.Connection], Any]) -> Any:
        with self._lock, self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                result = operation(db)
                db.commit()
                return result
            except Exception:
                db.rollback()
                raise

    @staticmethod
    def _runtime_from_row(row: sqlite3.Row) -> WatchRuntimeSummary:
        if row["runtime_data"]:
            return WatchRuntimeSummary.model_validate_json(row["runtime_data"])
        rule = WatchRule.model_validate_json(row["data"])
        initial = _initial_runtime(rule, _time(row["due_at"]))
        # Legacy active rules have no verified start. Do not issue historical start notices.
        return initial.model_copy(update={"activation_epoch": 0})

    def runtime(self, watch_id: str, session_id: str) -> WatchRuntimeSummary | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM watches WHERE watch_id = ? AND session_id = ?",
                (watch_id, session_id),
            ).fetchone()
        return self._with_delivery(self._runtime_from_row(row)) if row else None

    def _with_delivery(self, runtime: WatchRuntimeSummary) -> WatchRuntimeSummary:
        with self._connection() as db:
            row = db.execute(
                "SELECT outbox.status FROM lifecycle_events LEFT JOIN outbox "
                "ON outbox.trigger_id = lifecycle_events.event_id "
                "WHERE lifecycle_events.watch_id = ? ORDER BY lifecycle_events.occurred_at DESC "
                "LIMIT 1",
                (runtime.watch_id,),
            ).fetchone()
        return runtime.model_copy(
            update={
                "delivery_status": row["status"] if row and row["status"] else "inbox_only",
            }
        )

    def list_runtime(self, session_id: str, status: str = "all") -> list[WatchRuntimeSummary]:
        if status not in {"all", "active", "paused", "terminal"}:
            raise ValueError("status must be active, paused, terminal, or all")
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM watches WHERE session_id = ? "
                "AND (? = 'all' OR status = ?) ORDER BY created_at DESC LIMIT 100",
                (session_id, status, status),
            ).fetchall()
        return [self._with_delivery(self._runtime_from_row(row)) for row in rows]

    def record_runtime(
        self,
        rule: WatchRule,
        owner: str,
        runtime: WatchRuntimeSummary,
        event: WatchLifecycleEvent | None,
    ) -> bool:
        def operation(db: sqlite3.Connection) -> bool:
            row = db.execute(
                "SELECT lease_owner, runtime_data FROM watches WHERE watch_id = ?", (rule.watch_id,)
            ).fetchone()
            if not row or row["lease_owner"] != owner:
                return False
            db.execute(
                "UPDATE watches SET runtime_data = ? WHERE watch_id = ?",
                (runtime.model_dump_json(), rule.watch_id),
            )
            if event is None:
                return True
            inserted = db.execute(
                "INSERT OR IGNORE INTO lifecycle_events VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    event.fingerprint,
                    event.watch_id,
                    event.session_id,
                    _text(event.occurred_at),
                    event.model_dump_json(),
                ),
            ).rowcount
            if inserted and DeliveryChannel.TELEGRAM in rule.delivery.channels:
                db.execute(
                    "INSERT INTO outbox VALUES (?, ?, ?, 'telegram', 'pending', 0, ?, "
                    "NULL, NULL, NULL, NULL)",
                    (
                        f"outbox_{event.event_id[6:]}",
                        event.event_id,
                        rule.watch_id,
                        _text(event.occurred_at),
                    ),
                )
            if inserted:
                db.executemany(
                    "INSERT OR IGNORE INTO lifecycle_evidence "
                    "SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM observations "
                    "WHERE watch_id = ? AND observation_id = ?)",
                    [
                        (
                            event.event_id,
                            rule.watch_id,
                            observation_id,
                            rule.watch_id,
                            observation_id,
                        )
                        for observation_id in event.observation_ids
                    ],
                )
            return True

        return bool(self._write(operation))

    def list_events(self, session_id: str, limit: int = 50) -> list[WatchEvent]:
        with self._connection() as db:
            triggers = db.execute(
                "SELECT data FROM triggers WHERE session_id = ? ORDER BY triggered_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            lifecycle = db.execute(
                "SELECT data FROM lifecycle_events WHERE session_id = ? "
                "ORDER BY occurred_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        events: list[WatchEvent] = [WatchTrigger.model_validate_json(r["data"]) for r in triggers]
        events.extend(WatchLifecycleEvent.model_validate_json(r["data"]) for r in lifecycle)
        return sorted(
            events,
            key=lambda e: e.triggered_at if isinstance(e, WatchTrigger) else e.occurred_at,
            reverse=True,
        )[:limit]

    def event_by_id(self, event_id: str) -> WatchEvent | None:
        trigger = self.trigger_by_id(event_id)
        if trigger:
            return trigger
        with self._connection() as db:
            row = db.execute(
                "SELECT data FROM lifecycle_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return WatchLifecycleEvent.model_validate_json(row["data"]) if row else None

    def save_rule(self, rule: WatchRule, *, due_at: datetime) -> None:
        def operation(db: sqlite3.Connection) -> None:
            db.execute(
                "INSERT INTO watches (watch_id, session_id, status, schema_version, data, "
                "due_at, lease_owner, lease_until, created_at, runtime_data) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)",
                (
                    rule.watch_id,
                    rule.session_id,
                    rule.status.value,
                    rule.schema_version,
                    rule.model_dump_json(),
                    _text(due_at),
                    _text(rule.created_at),
                    _initial_runtime(rule, due_at).model_dump_json(),
                ),
            )
            db.executemany(
                "INSERT INTO condition_runtime VALUES (?, ?, 1, NULL)",
                [(rule.watch_id, condition.condition_id) for condition in rule.conditions],
            )

        self._write(operation)

    def get_rule(self, watch_id: str, session_id: str) -> WatchRule | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT data FROM watches WHERE watch_id = ? AND session_id = ?",
                (watch_id, session_id),
            ).fetchone()
        return WatchRule.model_validate_json(row["data"]) if row else None

    def list_rules(self, session_id: str) -> list[WatchRule]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT data FROM watches WHERE session_id = ? ORDER BY created_at", (session_id,)
            ).fetchall()
        return [WatchRule.model_validate_json(row["data"]) for row in rows]

    def replace_rule(self, rule: WatchRule, session_id: str) -> None:
        def operation(db: sqlite3.Connection) -> None:
            existing = db.execute(
                "SELECT 1 FROM watches WHERE watch_id = ? AND session_id = ?",
                (rule.watch_id, session_id),
            ).fetchone()
            if not existing:
                raise KeyError("watch not found")
            db.execute(
                "UPDATE watches SET status = ?, data = ?, due_at = ?, lease_owner = NULL, "
                "lease_until = NULL, runtime_data = ? WHERE watch_id = ?",
                (
                    rule.status.value,
                    rule.model_dump_json(),
                    _text(rule.monitoring_starts_at),
                    _updated_runtime(
                        rule,
                        self._runtime_from_row(
                            db.execute(
                                "SELECT * FROM watches WHERE watch_id = ?", (rule.watch_id,)
                            ).fetchone()
                        ),
                        "updated",
                    ).model_dump_json(),
                    rule.watch_id,
                ),
            )
            db.execute("DELETE FROM condition_runtime WHERE watch_id = ?", (rule.watch_id,))
            db.executemany(
                "INSERT INTO condition_runtime VALUES (?, ?, 1, NULL)",
                [(rule.watch_id, condition.condition_id) for condition in rule.conditions],
            )

        self._write(operation)

    def set_status(self, watch_id: str, session_id: str, status: WatchStatus) -> bool:
        def operation(db: sqlite3.Connection) -> bool:
            row = db.execute(
                "SELECT * FROM watches WHERE watch_id = ? AND session_id = ?",
                (watch_id, session_id),
            ).fetchone()
            if not row:
                return False
            rule = WatchRule.model_validate_json(row["data"]).model_copy(update={"status": status})
            runtime = self._runtime_from_row(row)
            if status == WatchStatus.ACTIVE and runtime.desired_status == WatchStatus.PAUSED:
                runtime = _updated_runtime(rule, runtime, "resumed")
            else:
                runtime = runtime.model_copy(
                    update={
                        "desired_status": status,
                        "runtime_state": WatchRuntimeState.TERMINAL
                        if status == WatchStatus.TERMINAL
                        else runtime.runtime_state,
                        "next_due_at": None
                        if status != WatchStatus.ACTIVE
                        else runtime.next_due_at,
                    }
                )
            db.execute(
                "UPDATE watches SET status = ?, data = ?, lease_owner = NULL, lease_until = NULL "
                ", runtime_data = ?, due_at = ? WHERE watch_id = ?",
                (
                    status.value,
                    rule.model_dump_json(),
                    runtime.model_dump_json(),
                    _text(rule.monitoring_starts_at)
                    if status == WatchStatus.ACTIVE
                    else _text(datetime.max.replace(tzinfo=UTC)),
                    watch_id,
                ),
            )
            if status == WatchStatus.ACTIVE and runtime.activation_kind == "resumed":
                db.execute(
                    "UPDATE condition_runtime SET armed = 1, fired_at = NULL WHERE watch_id = ?",
                    (watch_id,),
                )
            return True

        return bool(self._write(operation))

    def delete_rule(self, watch_id: str, session_id: str) -> bool:
        return bool(
            self._write(
                lambda db: (
                    db.execute(
                        "DELETE FROM watches WHERE watch_id = ? AND session_id = ?",
                        (watch_id, session_id),
                    ).rowcount
                )
            )
        )

    def claim_due(self, owner: str, now: datetime, limit: int = 100) -> list[WatchRule]:
        lease_until = now + timedelta(seconds=50)

        def operation(db: sqlite3.Connection) -> list[WatchRule]:
            rows = db.execute(
                "SELECT watch_id, data FROM watches WHERE status = 'active' AND due_at <= ? "
                "AND (lease_until IS NULL OR lease_until < ?) ORDER BY due_at LIMIT ?",
                (_text(now), _text(now), limit),
            ).fetchall()
            ids = [row["watch_id"] for row in rows]
            db.executemany(
                "UPDATE watches SET lease_owner = ?, lease_until = ? WHERE watch_id = ?",
                [(owner, _text(lease_until), watch_id) for watch_id in ids],
            )
            return [WatchRule.model_validate_json(row["data"]) for row in rows]

        return cast(list[WatchRule], self._write(operation))

    def release_claim(self, watch_id: str, owner: str, due_at: datetime) -> None:
        def operation(db: sqlite3.Connection) -> None:
            row = db.execute(
                "SELECT * FROM watches WHERE watch_id = ? AND lease_owner = ?", (watch_id, owner)
            ).fetchone()
            if row:
                runtime = self._runtime_from_row(row).model_copy(update={"next_due_at": due_at})
                db.execute(
                    "UPDATE watches SET due_at = ?, lease_owner = NULL, lease_until = NULL, "
                    "runtime_data = ? WHERE watch_id = ?",
                    (_text(due_at), runtime.model_dump_json(), watch_id),
                )

        self._write(operation)

    def add_observation(self, watch_id: str, observation: WatchObservation) -> None:
        self._write(
            lambda db: db.execute(
                "INSERT OR IGNORE INTO observations VALUES (?, ?, ?, ?, ?)",
                (
                    observation.observation_id,
                    watch_id,
                    _text(observation.retrieved_at),
                    0,
                    observation.model_dump_json(),
                ),
            )
        )

    def recent_observations(
        self, watch_id: str, since: datetime, limit: int = 120
    ) -> list[WatchObservation]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT data FROM observations WHERE watch_id = ? AND observed_at >= ? "
                "ORDER BY observed_at DESC LIMIT ?",
                (watch_id, _text(since), limit),
            ).fetchall()
        return [WatchObservation.model_validate_json(row["data"]) for row in reversed(rows)]

    def condition_state(self, watch_id: str, condition_id: str) -> tuple[bool, datetime | None]:
        with self._connection() as db:
            row = db.execute(
                "SELECT armed, fired_at FROM condition_runtime "
                "WHERE watch_id = ? AND condition_id = ?",
                (watch_id, condition_id),
            ).fetchone()
        if row is None:
            return True, None
        return bool(row["armed"]), _time(row["fired_at"]) if row["fired_at"] else None

    def set_condition_state(
        self, watch_id: str, condition_id: str, armed: bool, fired_at: datetime | None
    ) -> None:
        self._write(
            lambda db: db.execute(
                "UPDATE condition_runtime SET armed = ?, fired_at = ? "
                "WHERE watch_id = ? AND condition_id = ?",
                (int(armed), _text(fired_at) if fired_at else None, watch_id, condition_id),
            )
        )

    def record_trigger(self, rule: WatchRule, trigger: WatchTrigger) -> bool:
        def operation(db: sqlite3.Connection) -> bool:
            inserted = db.execute(
                "INSERT OR IGNORE INTO triggers VALUES (?, ?, ?, ?, ?, ?)",
                (
                    trigger.trigger_id,
                    trigger.fingerprint,
                    trigger.watch_id,
                    trigger.session_id,
                    _text(trigger.triggered_at),
                    trigger.model_dump_json(),
                ),
            ).rowcount
            if inserted and DeliveryChannel.TELEGRAM in rule.delivery.channels:
                db.execute(
                    "INSERT INTO outbox VALUES (?, ?, ?, 'telegram', 'pending', 0, ?, "
                    "NULL, NULL, NULL, NULL)",
                    (
                        f"outbox_{trigger.trigger_id[8:]}",
                        trigger.trigger_id,
                        rule.watch_id,
                        _text(trigger.triggered_at),
                    ),
                )
            if inserted:
                db.executemany(
                    "INSERT OR IGNORE INTO trigger_evidence "
                    "SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM observations "
                    "WHERE watch_id = ? AND observation_id = ?)",
                    [
                        (
                            trigger.trigger_id,
                            rule.watch_id,
                            value,
                            rule.watch_id,
                            value,
                        )
                        for value in trigger.observation_ids
                    ],
                )
            return bool(inserted)

        return bool(self._write(operation))

    def list_triggers(self, session_id: str, limit: int = 50) -> list[WatchTrigger]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT data FROM triggers WHERE session_id = ? ORDER BY triggered_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [WatchTrigger.model_validate_json(row["data"]) for row in rows]

    def claim_outbox(self, owner: str, now: datetime, limit: int = 20) -> list[OutboxItem]:
        lease_until = now + timedelta(seconds=30)

        def operation(db: sqlite3.Connection) -> list[OutboxItem]:
            rows = db.execute(
                "SELECT * FROM outbox WHERE status IN ('pending', 'retry_scheduled', 'leased') "
                "AND available_at <= ? AND (lease_until IS NULL OR lease_until < ?) "
                "ORDER BY available_at LIMIT ?",
                (_text(now), _text(now), limit),
            ).fetchall()
            db.executemany(
                "UPDATE outbox SET status = 'leased', attempts = MIN(attempts + 1, 20), "
                "lease_owner = ?, "
                "lease_until = ? WHERE outbox_id = ?",
                [(owner, _text(lease_until), row["outbox_id"]) for row in rows],
            )
            return [
                OutboxItem(
                    outbox_id=row["outbox_id"],
                    trigger_id=row["trigger_id"],
                    watch_id=row["watch_id"],
                    status=OutboxStatus.LEASED,
                    attempts=min(row["attempts"] + 1, 20),
                    available_at=_time(row["available_at"]),
                    lease_owner=owner,
                    lease_until=lease_until,
                    provider_message_id=row["provider_message_id"],
                    error_class=row["error_class"],
                )
                for row in rows
            ]

        return cast(list[OutboxItem], self._write(operation))

    def finish_outbox(
        self,
        outbox_id: str,
        owner: str,
        status: OutboxStatus,
        *,
        available_at: datetime,
        provider_message_id: str | None = None,
        error_class: str | None = None,
    ) -> bool:
        return bool(
            self._write(
                lambda db: (
                    db.execute(
                        "UPDATE outbox SET status = ?, available_at = ?, lease_owner = NULL, "
                        "lease_until = NULL, provider_message_id = ?, error_class = ? "
                        "WHERE outbox_id = ? AND lease_owner = ?",
                        (
                            status.value,
                            _text(available_at),
                            provider_message_id,
                            error_class,
                            outbox_id,
                            owner,
                        ),
                    ).rowcount
                )
            )
        )

    def outbox_for_trigger(self, trigger_id: str) -> OutboxItem | None:
        with self._connection() as db:
            row = db.execute("SELECT * FROM outbox WHERE trigger_id = ?", (trigger_id,)).fetchone()
        if not row:
            return None
        return _outbox(dict(row))

    def trigger_by_id(self, trigger_id: str) -> WatchTrigger | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT data FROM triggers WHERE trigger_id = ?", (trigger_id,)
            ).fetchone()
        return WatchTrigger.model_validate_json(row["data"]) if row else None

    def cleanup(self, now: datetime) -> tuple[int, int]:
        observations_before = now - timedelta(days=7)
        triggers_before = now - timedelta(days=30)

        def operation(db: sqlite3.Connection) -> tuple[int, int]:
            db.execute(
                "DELETE FROM outbox WHERE trigger_id IN "
                "(SELECT event_id FROM lifecycle_events WHERE occurred_at < ?) "
                "OR trigger_id IN (SELECT trigger_id FROM triggers WHERE triggered_at < ?)",
                (_text(triggers_before), _text(triggers_before)),
            )
            triggers = db.execute(
                "DELETE FROM triggers WHERE triggered_at < ?", (_text(triggers_before),)
            ).rowcount
            db.execute(
                "DELETE FROM lifecycle_events WHERE occurred_at < ?", (_text(triggers_before),)
            )
            observations = db.execute(
                "DELETE FROM observations WHERE observed_at < ? AND NOT EXISTS ("
                "SELECT 1 FROM trigger_evidence WHERE trigger_evidence.watch_id = "
                "observations.watch_id AND trigger_evidence.observation_id = "
                "observations.observation_id) AND NOT EXISTS ("
                "SELECT 1 FROM lifecycle_evidence WHERE lifecycle_evidence.watch_id = "
                "observations.watch_id AND lifecycle_evidence.observation_id = "
                "observations.observation_id)",
                (_text(observations_before),),
            ).rowcount
            return observations, triggers

        return cast(tuple[int, int], self._write(operation))


class FirestoreTransaction(Protocol):
    def get(self, collection: str, document: str) -> dict[str, Any] | None: ...
    def set(self, collection: str, document: str, data: dict[str, Any]) -> None: ...
    def delete(self, collection: str, document: str) -> None: ...
    def query(self, collection: str, **filters: Any) -> list[tuple[str, dict[str, Any]]]: ...


class FirestoreStore(Protocol):
    """Small injectable transaction boundary implemented by Google and test fakes."""

    def atomic(self, operation: Callable[[FirestoreTransaction], Any]) -> Any: ...
    def query(self, collection: str, **filters: Any) -> list[tuple[str, dict[str, Any]]]: ...


class FirestoreWatchRepository:
    """Firestore durable adapter using document transactions for leases and outbox commits."""

    def __init__(self, store: FirestoreStore) -> None:
        self.store = store

    @staticmethod
    def _rule_data(rule: WatchRule, due_at: datetime) -> dict[str, Any]:
        return {
            "watch_id": rule.watch_id,
            "session_id": rule.session_id,
            "status": rule.status.value,
            "schema_version": rule.schema_version,
            "due_at": _text(due_at),
            "lease_owner": None,
            "lease_until": None,
            "created_at": _text(rule.created_at),
            "rule": rule.model_dump_json(),
            "runtime_data": _initial_runtime(rule, due_at).model_dump_json(),
        }

    @staticmethod
    def _runtime_from_data(data: dict[str, Any]) -> WatchRuntimeSummary:
        if data.get("runtime_data"):
            return WatchRuntimeSummary.model_validate_json(data["runtime_data"])
        rule = WatchRule.model_validate_json(data["rule"])
        return _initial_runtime(rule, _time(data["due_at"])).model_copy(
            update={"activation_epoch": 0}
        )

    def runtime(self, watch_id: str, session_id: str) -> WatchRuntimeSummary | None:
        rows = self.store.query("watches", watch_id=watch_id, session_id=session_id, limit=1)
        return self._with_delivery(self._runtime_from_data(rows[0][1])) if rows else None

    def _with_delivery(self, runtime: WatchRuntimeSummary) -> WatchRuntimeSummary:
        events = self.store.query("watch_lifecycle_events", watch_id=runtime.watch_id, limit=100)
        events.sort(key=lambda item: item[1]["occurred_at"], reverse=True)
        if not events:
            return runtime
        outbox = self.outbox_for_trigger(events[0][0])
        return runtime.model_copy(
            update={
                "delivery_status": outbox.status.value if outbox else "inbox_only",
            }
        )

    def list_runtime(self, session_id: str, status: str = "all") -> list[WatchRuntimeSummary]:
        if status not in {"all", "active", "paused", "terminal"}:
            raise ValueError("status must be active, paused, terminal, or all")
        rows = self.store.query("watches", session_id=session_id, limit=100)
        return [
            self._with_delivery(self._runtime_from_data(data))
            for _, data in rows
            if status == "all" or data["status"] == status
        ]

    def record_runtime(
        self,
        rule: WatchRule,
        owner: str,
        runtime: WatchRuntimeSummary,
        event: WatchLifecycleEvent | None,
    ) -> bool:
        def operation(tx: FirestoreTransaction) -> bool:
            current = tx.get("watches", rule.watch_id)
            if not current or current.get("lease_owner") != owner:
                return False
            existing_event = tx.get("watch_lifecycle_events", event.event_id) if event else None
            evidence = (
                {
                    observation_id: tx.get(
                        "watch_observations", f"{rule.watch_id}:{observation_id}"
                    )
                    for observation_id in event.observation_ids
                }
                if event and not existing_event
                else {}
            )
            current["runtime_data"] = runtime.model_dump_json()
            tx.set("watches", rule.watch_id, current)
            if event is None or existing_event:
                return True
            tx.set(
                "watch_lifecycle_events",
                event.event_id,
                {
                    "event_id": event.event_id,
                    "watch_id": rule.watch_id,
                    "session_id": rule.session_id,
                    "occurred_at": _text(event.occurred_at),
                    "observation_ids": event.observation_ids,
                    "event": event.model_dump_json(),
                },
            )
            for observation_id, observation in evidence.items():
                if observation and observation.get("watch_id") == rule.watch_id:
                    observation["trigger_ids"] = sorted(
                        {*observation.get("trigger_ids", []), event.event_id}
                    )
                    observation["trigger_evidence"] = True
                    tx.set("watch_observations", f"{rule.watch_id}:{observation_id}", observation)
            if DeliveryChannel.TELEGRAM in rule.delivery.channels:
                outbox_id = f"outbox_{event.event_id[6:]}"
                tx.set(
                    "watch_outbox",
                    outbox_id,
                    {
                        "outbox_id": outbox_id,
                        "trigger_id": event.event_id,
                        "watch_id": rule.watch_id,
                        "channel": "telegram",
                        "status": "pending",
                        "deliverable": True,
                        "attempts": 0,
                        "available_at": _text(event.occurred_at),
                        "lease_owner": None,
                        "lease_until": None,
                        "provider_message_id": None,
                        "error_class": None,
                    },
                )
            return True

        return bool(self.store.atomic(operation))

    def list_events(self, session_id: str, limit: int = 50) -> list[WatchEvent]:
        events: list[WatchEvent] = list(self.list_triggers(session_id, limit))
        events.extend(
            WatchLifecycleEvent.model_validate_json(data["event"])
            for _, data in self.store.query(
                "watch_lifecycle_events", session_id=session_id, limit=limit
            )
        )
        return sorted(
            events,
            key=lambda e: e.triggered_at if isinstance(e, WatchTrigger) else e.occurred_at,
            reverse=True,
        )[:limit]

    def event_by_id(self, event_id: str) -> WatchEvent | None:
        trigger = self.trigger_by_id(event_id)
        if trigger:
            return trigger
        rows = self.store.query("watch_lifecycle_events", event_id=event_id, limit=1)
        return WatchLifecycleEvent.model_validate_json(rows[0][1]["event"]) if rows else None

    def save_rule(self, rule: WatchRule, *, due_at: datetime) -> None:
        def operation(tx: FirestoreTransaction) -> None:
            if tx.get("watches", rule.watch_id):
                raise ValueError("watch already exists")
            tx.set("watches", rule.watch_id, self._rule_data(rule, due_at))
            for condition in rule.conditions:
                tx.set(
                    "watch_runtime",
                    f"{rule.watch_id}:{condition.condition_id}",
                    {
                        "watch_id": rule.watch_id,
                        "condition_id": condition.condition_id,
                        "armed": True,
                        "fired_at": None,
                    },
                )

        self.store.atomic(operation)

    def get_rule(self, watch_id: str, session_id: str) -> WatchRule | None:
        rows = self.store.query("watches", watch_id=watch_id, session_id=session_id, limit=1)
        return WatchRule.model_validate_json(rows[0][1]["rule"]) if rows else None

    def list_rules(self, session_id: str) -> list[WatchRule]:
        return [
            WatchRule.model_validate_json(data["rule"])
            for _, data in self.store.query("watches", session_id=session_id)
        ]

    def replace_rule(self, rule: WatchRule, session_id: str) -> None:
        existing_runtime = self.store.query("watch_runtime", watch_id=rule.watch_id, limit=500)

        def operation(tx: FirestoreTransaction) -> None:
            current = tx.get("watches", rule.watch_id)
            if not current or current["session_id"] != session_id:
                raise KeyError("watch not found")
            current.update(
                status=rule.status.value,
                rule=rule.model_dump_json(),
                due_at=_text(rule.monitoring_starts_at),
                lease_owner=None,
                lease_until=None,
                runtime_data=_updated_runtime(
                    rule, self._runtime_from_data(current), "updated"
                ).model_dump_json(),
            )
            tx.set("watches", rule.watch_id, current)
            desired_documents = {
                f"{rule.watch_id}:{condition.condition_id}" for condition in rule.conditions
            }
            for document, _ in existing_runtime:
                if document not in desired_documents:
                    tx.delete("watch_runtime", document)
            for condition in rule.conditions:
                document = f"{rule.watch_id}:{condition.condition_id}"
                tx.set(
                    "watch_runtime",
                    document,
                    {
                        "watch_id": rule.watch_id,
                        "condition_id": condition.condition_id,
                        "armed": True,
                        "fired_at": None,
                    },
                )

        self.store.atomic(operation)

    def set_status(self, watch_id: str, session_id: str, status: WatchStatus) -> bool:
        def operation(tx: FirestoreTransaction) -> bool:
            current = tx.get("watches", watch_id)
            if not current or current["session_id"] != session_id:
                return False
            rule = WatchRule.model_validate_json(current["rule"]).model_copy(
                update={"status": status}
            )
            current.update(
                status=status.value,
                rule=rule.model_dump_json(),
                lease_owner=None,
                lease_until=None,
            )
            previous = self._runtime_from_data(current)
            runtime = (
                _updated_runtime(rule, previous, "resumed")
                if status == WatchStatus.ACTIVE and previous.desired_status == WatchStatus.PAUSED
                else previous.model_copy(
                    update={
                        "desired_status": status,
                        "runtime_state": WatchRuntimeState.TERMINAL
                        if status == WatchStatus.TERMINAL
                        else previous.runtime_state,
                        "next_due_at": None
                        if status != WatchStatus.ACTIVE
                        else previous.next_due_at,
                    }
                )
            )
            current["runtime_data"] = runtime.model_dump_json()
            current["due_at"] = (
                _text(rule.monitoring_starts_at)
                if status == WatchStatus.ACTIVE
                else _text(datetime.max.replace(tzinfo=UTC))
            )
            tx.set("watches", watch_id, current)
            if status == WatchStatus.ACTIVE and runtime.activation_kind == "resumed":
                for condition in rule.conditions:
                    tx.set(
                        "watch_runtime",
                        f"{watch_id}:{condition.condition_id}",
                        {
                            "watch_id": watch_id,
                            "condition_id": condition.condition_id,
                            "armed": True,
                            "fired_at": None,
                        },
                    )
            return True

        return bool(self.store.atomic(operation))

    def delete_rule(self, watch_id: str, session_id: str) -> bool:
        def mark_deleting(tx: FirestoreTransaction) -> bool:
            current = tx.get("watches", watch_id)
            if not current or current["session_id"] != session_id:
                return False
            paused = WatchRule.model_validate_json(current["rule"]).model_copy(
                update={"status": WatchStatus.PAUSED}
            )
            current.update(
                status="deleting",
                rule=paused.model_dump_json(),
                lease_owner=None,
                lease_until=None,
            )
            tx.set("watches", watch_id, current)
            return True

        if not self.store.atomic(mark_deleting):
            return False

        for collection in (
            "watch_runtime",
            "watch_observations",
            "watch_outbox",
            "watch_lifecycle_events",
        ):
            while documents := self.store.query(collection, watch_id=watch_id, limit=200):

                def delete_documents(
                    tx: FirestoreTransaction,
                    documents: list[tuple[str, dict[str, Any]]] = documents,
                    collection: str = collection,
                ) -> None:
                    for document, _ in documents:
                        tx.delete(collection, document)

                self.store.atomic(delete_documents)
        while triggers := self.store.query("watch_triggers", watch_id=watch_id, limit=200):

            def delete_triggers(
                tx: FirestoreTransaction,
                triggers: list[tuple[str, dict[str, Any]]] = triggers,
            ) -> None:
                for document, data in triggers:
                    tx.delete("trigger_fingerprints", data["fingerprint"])
                    tx.delete("watch_triggers", document)

            self.store.atomic(delete_triggers)

        def finish(tx: FirestoreTransaction) -> bool:
            current = tx.get("watches", watch_id)
            if not current or current["session_id"] != session_id:
                return False
            tx.delete("watches", watch_id)
            return True

        return bool(self.store.atomic(finish))

    def claim_due(self, owner: str, now: datetime, limit: int = 100) -> list[WatchRule]:
        candidates = self.store.query(
            "watches",
            status="active",
            due_at_lte=_text(now),
            order_by_ascending="due_at",
            limit=limit,
        )
        claimed: list[WatchRule] = []
        for watch_id, _ in candidates:

            def operation(tx: FirestoreTransaction, watch_id: str = watch_id) -> WatchRule | None:
                current = tx.get("watches", watch_id)
                if not current or current["status"] != "active" or current["due_at"] > _text(now):
                    return None
                if current.get("lease_until") and current["lease_until"] >= _text(now):
                    return None
                current.update(lease_owner=owner, lease_until=_text(now + timedelta(seconds=50)))
                tx.set("watches", watch_id, current)
                return WatchRule.model_validate_json(current["rule"])

            if rule := self.store.atomic(operation):
                claimed.append(rule)
        return claimed

    def release_claim(self, watch_id: str, owner: str, due_at: datetime) -> None:
        def operation(tx: FirestoreTransaction) -> None:
            current = tx.get("watches", watch_id)
            if current and current.get("lease_owner") == owner:
                runtime = self._runtime_from_data(current).model_copy(
                    update={"next_due_at": due_at}
                )
                current.update(due_at=_text(due_at), lease_owner=None, lease_until=None)
                current["runtime_data"] = runtime.model_dump_json()
                tx.set("watches", watch_id, current)

        self.store.atomic(operation)

    def add_observation(self, watch_id: str, observation: WatchObservation) -> None:
        document = f"{watch_id}:{observation.observation_id}"

        def operation(tx: FirestoreTransaction) -> None:
            if tx.get("watch_observations", document):
                return
            tx.set(
                "watch_observations",
                document,
                {
                    "watch_id": watch_id,
                    "observed_at": _text(observation.retrieved_at),
                    "trigger_evidence": False,
                    "trigger_ids": [],
                    "observation": observation.model_dump_json(),
                },
            )

        self.store.atomic(operation)

    def recent_observations(
        self, watch_id: str, since: datetime, limit: int = 120
    ) -> list[WatchObservation]:
        rows = self.store.query(
            "watch_observations",
            watch_id=watch_id,
            observed_at_gte=_text(since),
            order_by_ascending="observed_at",
            limit=limit,
        )
        return [WatchObservation.model_validate_json(data["observation"]) for _, data in rows]

    def condition_state(self, watch_id: str, condition_id: str) -> tuple[bool, datetime | None]:
        rows = self.store.query(
            "watch_runtime", watch_id=watch_id, condition_id=condition_id, limit=1
        )
        if not rows:
            return True, None
        data = rows[0][1]
        return bool(data["armed"]), _time(data["fired_at"]) if data.get("fired_at") else None

    def set_condition_state(
        self, watch_id: str, condition_id: str, armed: bool, fired_at: datetime | None
    ) -> None:
        document = f"{watch_id}:{condition_id}"
        self.store.atomic(
            lambda tx: tx.set(
                "watch_runtime",
                document,
                {
                    "watch_id": watch_id,
                    "condition_id": condition_id,
                    "armed": armed,
                    "fired_at": _text(fired_at) if fired_at else None,
                },
            )
        )

    def record_trigger(self, rule: WatchRule, trigger: WatchTrigger) -> bool:
        def operation(tx: FirestoreTransaction) -> bool:
            if tx.get("trigger_fingerprints", trigger.fingerprint):
                return False
            evidence = {
                observation_id: tx.get("watch_observations", f"{rule.watch_id}:{observation_id}")
                for observation_id in trigger.observation_ids
            }
            tx.set("trigger_fingerprints", trigger.fingerprint, {"trigger_id": trigger.trigger_id})
            tx.set(
                "watch_triggers",
                trigger.trigger_id,
                {
                    "session_id": trigger.session_id,
                    "watch_id": rule.watch_id,
                    "fingerprint": trigger.fingerprint,
                    "triggered_at": _text(trigger.triggered_at),
                    "observation_ids": trigger.observation_ids,
                    "trigger": trigger.model_dump_json(),
                },
            )
            for observation_id in trigger.observation_ids:
                document = f"{rule.watch_id}:{observation_id}"
                observation = evidence[observation_id]
                if observation and observation.get("watch_id") == rule.watch_id:
                    observation["trigger_evidence"] = True
                    observation["trigger_ids"] = sorted(
                        {*observation.get("trigger_ids", []), trigger.trigger_id}
                    )
                    tx.set("watch_observations", document, observation)
            if DeliveryChannel.TELEGRAM in rule.delivery.channels:
                outbox_id = f"outbox_{trigger.trigger_id[8:]}"
                tx.set(
                    "watch_outbox",
                    outbox_id,
                    {
                        "outbox_id": outbox_id,
                        "trigger_id": trigger.trigger_id,
                        "watch_id": rule.watch_id,
                        "channel": "telegram",
                        "status": "pending",
                        "deliverable": True,
                        "attempts": 0,
                        "available_at": _text(trigger.triggered_at),
                        "lease_owner": None,
                        "lease_until": None,
                        "provider_message_id": None,
                        "error_class": None,
                    },
                )
            return True

        return bool(self.store.atomic(operation))

    def list_triggers(self, session_id: str, limit: int = 50) -> list[WatchTrigger]:
        return [
            WatchTrigger.model_validate_json(data["trigger"])
            for _, data in self.store.query(
                "watch_triggers",
                session_id=session_id,
                order_by_descending="triggered_at",
                limit=limit,
            )
        ]

    def claim_outbox(self, owner: str, now: datetime, limit: int = 20) -> list[OutboxItem]:
        candidates = self.store.query(
            "watch_outbox",
            deliverable=True,
            due_at_lte=_text(now),
            order_by_ascending="available_at",
            limit=limit,
        )
        claimed: list[OutboxItem] = []
        for outbox_id, _ in candidates:

            def operation(
                tx: FirestoreTransaction, outbox_id: str = outbox_id
            ) -> OutboxItem | None:
                current = tx.get("watch_outbox", outbox_id)
                if not current or current["status"] in {"sent", "failed_terminal"}:
                    return None
                if current.get("lease_until") and current["lease_until"] >= _text(now):
                    return None
                current.update(
                    status="leased",
                    attempts=min(current["attempts"] + 1, 20),
                    lease_owner=owner,
                    lease_until=_text(now + timedelta(seconds=30)),
                )
                tx.set("watch_outbox", outbox_id, current)
                return _outbox(current)

            if item := self.store.atomic(operation):
                claimed.append(item)
        return claimed

    def finish_outbox(
        self,
        outbox_id: str,
        owner: str,
        status: OutboxStatus,
        *,
        available_at: datetime,
        provider_message_id: str | None = None,
        error_class: str | None = None,
    ) -> bool:
        def operation(tx: FirestoreTransaction) -> bool:
            current = tx.get("watch_outbox", outbox_id)
            if not current or current.get("lease_owner") != owner:
                return False
            current.update(
                status=status.value,
                deliverable=status == OutboxStatus.RETRY,
                available_at=_text(available_at),
                lease_owner=None,
                lease_until=None,
                provider_message_id=provider_message_id,
                error_class=error_class,
            )
            tx.set("watch_outbox", outbox_id, current)
            return True

        return bool(self.store.atomic(operation))

    def outbox_for_trigger(self, trigger_id: str) -> OutboxItem | None:
        rows = self.store.query("watch_outbox", trigger_id=trigger_id, limit=1)
        return _outbox(rows[0][1]) if rows else None

    def trigger_by_id(self, trigger_id: str) -> WatchTrigger | None:
        rows = self.store.query("watch_triggers", trigger_id=trigger_id, limit=1)
        return WatchTrigger.model_validate_json(rows[0][1]["trigger"]) if rows else None

    def cleanup(self, now: datetime) -> tuple[int, int]:
        old_observations = [
            (document, data)
            for document, data in self.store.query(
                "watch_observations",
                observed_at_lte=_text(now - timedelta(days=7)),
                trigger_evidence=False,
                limit=200,
            )
            if not data.get("trigger_ids") and not data.get("trigger_evidence")
        ]
        old_triggers = self.store.query(
            "watch_triggers",
            triggered_at_lte=_text(now - timedelta(days=30)),
            limit=20,
        )
        old_lifecycle = self.store.query(
            "watch_lifecycle_events",
            occurred_at_lte=_text(now - timedelta(days=30)),
            limit=20,
        )
        expired_events = [*old_triggers, *old_lifecycle]
        old_outboxes = {
            document: [
                outbox_id
                for outbox_id, _ in self.store.query("watch_outbox", trigger_id=document, limit=1)
            ]
            for document, _ in expired_events
        }
        removed_event_ids = {document for document, _ in expired_events}

        def delete_ordinary_observations(tx: FirestoreTransaction) -> None:
            for document, _ in old_observations:
                tx.delete("watch_observations", document)

        self.store.atomic(delete_ordinary_observations)

        def delete_trigger_evidence(tx: FirestoreTransaction) -> None:
            evidence_documents: dict[str, dict[str, Any]] = {}
            for _, event_data in expired_events:
                for observation_id in event_data.get("observation_ids", []):
                    document = f"{event_data['watch_id']}:{observation_id}"
                    if observation := tx.get("watch_observations", document):
                        evidence_documents[document] = observation
            for document, data in evidence_documents.items():
                remaining = sorted(set(data.get("trigger_ids", [])) - removed_event_ids)
                if remaining:
                    data.update(trigger_ids=remaining, trigger_evidence=True)
                    tx.set("watch_observations", document, data)
                elif data["observed_at"] < _text(now - timedelta(days=7)):
                    tx.delete("watch_observations", document)
                else:
                    data.update(trigger_ids=[], trigger_evidence=False)
                    tx.set("watch_observations", document, data)
            for document, data in old_triggers:
                for outbox_id in old_outboxes[document]:
                    tx.delete("watch_outbox", outbox_id)
                tx.delete("trigger_fingerprints", data["fingerprint"])
                tx.delete("watch_triggers", document)
            for document, _ in old_lifecycle:
                for outbox_id in old_outboxes[document]:
                    tx.delete("watch_outbox", outbox_id)
                tx.delete("watch_lifecycle_events", document)

        self.store.atomic(delete_trigger_evidence)
        return len(old_observations), len(old_triggers)
