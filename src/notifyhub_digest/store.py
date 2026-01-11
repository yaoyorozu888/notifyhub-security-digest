from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class ReadStoreLike:
    def has(self, entry_id: str) -> bool:  # pragma: no cover
        raise NotImplementedError

    def mark_many(self, entry_ids: Iterable[str]) -> None:  # pragma: no cover
        raise NotImplementedError

    def save(self) -> None:  # pragma: no cover
        raise NotImplementedError


@dataclass
class ReadStore:
    path: Path
    _data: dict[str, dict[str, str]]

    @classmethod
    def load(cls, state_dir: Path, filename: str = "read_store.json") -> "ReadStore":
        state_dir.mkdir(parents=True, exist_ok=True)
        path = state_dir / filename
        if not path.exists():
            return cls(path=path, _data={})
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return cls(path=path, _data=raw)
        except Exception:
            # 壊れている場合は空扱い（安全側）
            pass
        return cls(path=path, _data={})

    def has(self, entry_id: str) -> bool:
        return entry_id in self._data

    def mark_many(self, entry_ids: Iterable[str]) -> None:
        for eid in entry_ids:
            self._data[str(eid)] = {"marked": "true"}

    def save(self) -> None:
        # atomic write
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = json.dumps(self._data, ensure_ascii=False, sort_keys=True, indent=2)
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self.path)


def get_read_store(state_dir: Path) -> ReadStoreLike:
    """既読管理ストアを取得。

    - 既定: ローカルJSON（state/read_store.json）
    - `READ_STORE_BACKEND=azure` で Azure Table Storage に切替

    Azure設定（いずれか）:
    - `AZURE_STORAGE_CONNECTION_STRING`
    - `AZURE_TABLE_ACCOUNT_URL` + Managed Identity/Entra ID (DefaultAzureCredential)
    """

    backend = os.getenv("READ_STORE_BACKEND", "local").strip().lower()
    if backend != "azure":
        return ReadStore.load(state_dir)

    table_name = os.getenv("AZURE_TABLE_NAME", "notifyhubRead").strip()
    partition_key = os.getenv("AZURE_TABLE_PARTITION_KEY", "read").strip()
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    account_url = os.getenv("AZURE_TABLE_ACCOUNT_URL")

    try:
        from notifyhub_digest.azure_table_store import AzureTableReadStore

        return AzureTableReadStore(
            table_name=table_name,
            partition_key=partition_key,
            account_url=account_url,
            connection_string=conn_str,
        )
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "Azure backend requires extras: pip install '.[azure]'"
        ) from e
