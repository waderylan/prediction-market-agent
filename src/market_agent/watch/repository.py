"""Storage-neutral watch repository with complete SQLite and Firestore adapters."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

from market_agent.watch.models import (
    DeliveryChannel,
    OutboxItem,
    OutboxStatus,
    WatchObservation,
    WatchRule,
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
                """
            )

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

    def save_rule(self, rule: WatchRule, *, due_at: datetime) -> None:
        def operation(db: sqlite3.Connection) -> None:
            db.execute(
                "INSERT INTO watches VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)",
                (
                    rule.watch_id,
                    rule.session_id,
                    rule.status.value,
                    rule.schema_version,
                    rule.model_dump_json(),
                    _text(due_at),
                    _text(rule.created_at),
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
                "lease_until = NULL WHERE watch_id = ?",
                (
                    rule.status.value,
                    rule.model_dump_json(),
                    _text(rule.monitoring_starts_at),
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
                "SELECT data FROM watches WHERE watch_id = ? AND session_id = ?",
                (watch_id, session_id),
            ).fetchone()
            if not row:
                return False
            rule = WatchRule.model_validate_json(row["data"]).model_copy(update={"status": status})
            db.execute(
                "UPDATE watches SET status = ?, data = ?, lease_owner = NULL, lease_until = NULL "
                "WHERE watch_id = ?",
                (status.value, rule.model_dump_json(), watch_id),
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
        self._write(
            lambda db: db.execute(
                "UPDATE watches SET due_at = ?, lease_owner = NULL, lease_until = NULL "
                "WHERE watch_id = ? AND lease_owner = ?",
                (_text(due_at), watch_id, owner),
            )
        )

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
        return (bool(row["armed"]), _time(row["fired_at"]) if row and row["fired_at"] else None)

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
            triggers = db.execute(
                "DELETE FROM triggers WHERE triggered_at < ?", (_text(triggers_before),)
            ).rowcount
            observations = db.execute(
                "DELETE FROM observations WHERE observed_at < ? AND NOT EXISTS ("
                "SELECT 1 FROM trigger_evidence WHERE trigger_evidence.watch_id = "
                "observations.watch_id AND trigger_evidence.observation_id = "
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
        }

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
            tx.set("watches", watch_id, current)
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

        for collection in ("watch_runtime", "watch_observations", "watch_outbox"):
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
                current.update(due_at=_text(due_at), lease_owner=None, lease_until=None)
                tx.set("watches", watch_id, current)

        self.store.atomic(operation)

    def add_observation(self, watch_id: str, observation: WatchObservation) -> None:
        self.store.atomic(
            lambda tx: tx.set(
                "watch_observations",
                f"{watch_id}:{observation.observation_id}",
                {
                    "watch_id": watch_id,
                    "observed_at": _text(observation.retrieved_at),
                    "trigger_evidence": False,
                    "trigger_ids": [],
                    "observation": observation.model_dump_json(),
                },
            )
        )

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
        old_outboxes = {
            document: [
                outbox_id
                for outbox_id, _ in self.store.query("watch_outbox", trigger_id=document, limit=1)
            ]
            for document, _ in old_triggers
        }
        removed_trigger_ids = {document for document, _ in old_triggers}

        def delete_ordinary_observations(tx: FirestoreTransaction) -> None:
            for document, _ in old_observations:
                tx.delete("watch_observations", document)

        self.store.atomic(delete_ordinary_observations)

        def delete_trigger_evidence(tx: FirestoreTransaction) -> None:
            evidence_documents: dict[str, dict[str, Any]] = {}
            for _, trigger_data in old_triggers:
                for observation_id in trigger_data.get("observation_ids", []):
                    document = f"{trigger_data['watch_id']}:{observation_id}"
                    if observation := tx.get("watch_observations", document):
                        evidence_documents[document] = observation
            for document, data in evidence_documents.items():
                remaining = sorted(set(data.get("trigger_ids", [])) - removed_trigger_ids)
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

        self.store.atomic(delete_trigger_evidence)
        return len(old_observations), len(old_triggers)
