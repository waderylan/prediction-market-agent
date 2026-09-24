"""Injectable Google Cloud boundaries for Firestore, OIDC, and Secret Manager."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from google.auth.transport.requests import Request as GoogleRequest
from google.cloud import firestore, secretmanager
from google.cloud.firestore_v1.base_query import FieldFilter

from market_agent.watch.repository import FirestoreStore, FirestoreTransaction


class GoogleFirestoreTransaction:
    def __init__(self, client: firestore.Client, transaction: Any) -> None:
        self.client = client
        self.transaction = transaction

    def get(self, collection: str, document: str) -> dict[str, Any] | None:
        snapshot = (
            self.client.collection(collection).document(document).get(transaction=self.transaction)
        )
        return snapshot.to_dict() if snapshot.exists else None

    def set(self, collection: str, document: str, data: dict[str, Any]) -> None:
        self.transaction.set(self.client.collection(collection).document(document), data)

    def delete(self, collection: str, document: str) -> None:
        self.transaction.delete(self.client.collection(collection).document(document))

    def query(self, collection: str, **filters: Any) -> list[tuple[str, dict[str, Any]]]:
        raise RuntimeError("transactional collection scans are intentionally unsupported")


class GoogleFirestoreStore(FirestoreStore):
    def __init__(self, project_id: str) -> None:
        self.client = firestore.Client(project=project_id)

    def atomic(self, operation: Callable[[FirestoreTransaction], Any]) -> Any:
        transaction = self.client.transaction()

        @firestore.transactional
        def execute(current: Any) -> Any:
            return operation(GoogleFirestoreTransaction(self.client, current))

        return execute(transaction)

    def query(self, collection: str, **filters: Any) -> list[tuple[str, dict[str, Any]]]:
        limit = int(filters.pop("limit", 100))
        order_ascending = filters.pop("order_by_ascending", None)
        order_descending = filters.pop("order_by_descending", None)
        if order_ascending and order_descending:
            raise ValueError("query can specify only one ordering")
        query: Any = self.client.collection(collection)
        for key, value in filters.items():
            operator = "=="
            field = key
            if key.endswith("_lte"):
                field, operator = key[:-4], "<="
            elif key.endswith("_gte"):
                field, operator = key[:-4], ">="
            if field == "due_at" and collection == "watch_outbox":
                field = "available_at"
            query = query.where(filter=FieldFilter(field, operator, value))
        if order_ascending:
            query = query.order_by(order_ascending, direction=firestore.Query.ASCENDING)
        elif order_descending:
            query = query.order_by(order_descending, direction=firestore.Query.DESCENDING)
        return [
            (snapshot.id, snapshot.to_dict())
            for snapshot in query.limit(limit).stream()
            if snapshot.exists
        ]


class GoogleSecretLoader:
    def __init__(self, project_id: str, client: Any | None = None) -> None:
        self.project_id = project_id
        self.client = client or secretmanager.SecretManagerServiceClient()

    def access(self, secret_id: str) -> str:
        if "/" in secret_id:
            raise ValueError("secret ID must not be a resource path")
        name = f"projects/{self.project_id}/secrets/{secret_id}/versions/latest"
        response = self.client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8")


class GoogleOIDCVerifier:
    """Verifies audience and the dedicated Scheduler service-account identity."""

    def __init__(
        self,
        audience: str,
        service_account: str,
        verify: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.audience = audience
        self.service_account = service_account
        self._verify = verify

    def verify(self, authorization: str | None) -> bool:
        if not authorization or not authorization.startswith("Bearer "):
            return False
        token = authorization[7:]
        try:
            if self._verify:
                claims = self._verify(token, self.audience)
            else:
                from google.oauth2 import id_token

                claims = id_token.verify_oauth2_token(
                    token, GoogleRequest(), audience=self.audience
                )  # type: ignore[no-untyped-call]
        except Exception:
            return False
        return (
            claims.get("aud") == self.audience
            and claims.get("email") == self.service_account
            and claims.get("email_verified") is True
            and claims.get("iss") in {"https://accounts.google.com", "accounts.google.com"}
        )
