from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass
class AzureTableReadStore:
    """Azure Table Storage backend for read/unread de-duplication.

    - PartitionKey is constant by default (fast point lookup by RowKey).
    - RowKey is entry_id.
    """

    table_name: str
    partition_key: str
    account_url: str | None
    connection_string: str | None

    def __post_init__(self) -> None:
        if not self.table_name:
            raise ValueError("table_name is required")
        if not self.partition_key:
            raise ValueError("partition_key is required")
        if not (self.connection_string or self.account_url):
            raise ValueError("Either connection_string or account_url must be provided")

        # Lazy imports so local mode doesn't require Azure packages.
        from azure.core.exceptions import ResourceExistsError  # noqa: WPS433
        from azure.data.tables import TableServiceClient  # noqa: WPS433

        if self.connection_string:
            service = TableServiceClient.from_connection_string(self.connection_string)
        else:
            from azure.identity import DefaultAzureCredential  # noqa: WPS433

            service = TableServiceClient(endpoint=self.account_url, credential=DefaultAzureCredential())

        self._table = service.get_table_client(self.table_name)
        try:
            self._table.create_table()
        except ResourceExistsError:
            pass

        self._cache: set[str] = set()

    def has(self, entry_id: str) -> bool:
        if entry_id in self._cache:
            return True

        from azure.core.exceptions import ResourceNotFoundError  # noqa: WPS433

        try:
            self._table.get_entity(partition_key=self.partition_key, row_key=entry_id)
            self._cache.add(entry_id)
            return True
        except ResourceNotFoundError:
            return False

    def mark_many(self, entry_ids: Iterable[str]) -> None:
        from azure.data.tables import UpdateMode  # noqa: WPS433

        now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        ops = []
        for eid in entry_ids:
            if not eid:
                continue
            self._cache.add(eid)
            entity = {
                "PartitionKey": self.partition_key,
                "RowKey": str(eid),
                "marked": True,
                "marked_at": now,
            }
            ops.append(("upsert", entity, {"mode": UpdateMode.MERGE}))

        # Azure Tables: max 100 ops per transaction and same PartitionKey.
        for i in range(0, len(ops), 100):
            batch = ops[i : i + 100]
            if not batch:
                continue
            self._table.submit_transaction(batch)

    def save(self) -> None:
        # No-op: writes are immediate.
        return
