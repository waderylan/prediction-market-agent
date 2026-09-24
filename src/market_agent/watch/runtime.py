"""Runtime composition for local SQLite and Cloud Run durable boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

from market_agent.config import Settings
from market_agent.watch.cloud import GoogleFirestoreStore, GoogleOIDCVerifier, GoogleSecretLoader
from market_agent.watch.coordinator import MCPWatchEvidenceProvider, WatchCoordinator
from market_agent.watch.delivery import DeliveryWorker, TelegramDelivery
from market_agent.watch.repository import (
    FirestoreWatchRepository,
    SQLiteWatchRepository,
    WatchRepository,
)
from market_agent.watch.service import WatchService


@dataclass(frozen=True)
class WatchRuntime:
    repository: WatchRepository
    service: WatchService
    coordinator: WatchCoordinator
    delivery: DeliveryWorker
    oidc: GoogleOIDCVerifier | None


def build_watch_runtime(settings: Settings) -> WatchRuntime:
    if settings.watch_storage == "firestore":
        if not settings.gcp_project_id:
            raise ValueError("GCP_PROJECT_ID is required for Firestore watch storage")
        repository: WatchRepository = FirestoreWatchRepository(
            GoogleFirestoreStore(settings.gcp_project_id)
        )
    else:
        path = Path(settings.watch_sqlite_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        repository = SQLiteWatchRepository(path)

    token = settings.telegram_bot_token
    if token is None and settings.telegram_bot_token_secret:
        if not settings.gcp_project_id:
            raise ValueError("GCP_PROJECT_ID is required for Secret Manager")
        token = SecretStr(
            GoogleSecretLoader(settings.gcp_project_id).access(settings.telegram_bot_token_secret)
        )
    telegram = None
    if token is not None and settings.telegram_chat_id is not None:
        telegram = TelegramDelivery(token, settings.telegram_chat_id)

    oidc = None
    if settings.scheduler_oidc_audience and settings.scheduler_service_account:
        oidc = GoogleOIDCVerifier(
            settings.scheduler_oidc_audience, settings.scheduler_service_account
        )
    service = WatchService(repository, telegram_configured=telegram is not None)
    return WatchRuntime(
        repository=repository,
        service=service,
        coordinator=WatchCoordinator(repository, MCPWatchEvidenceProvider()),
        delivery=DeliveryWorker(repository, telegram),
        oidc=oidc,
    )
