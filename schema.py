"""Schema único de negócio. Tudo a jusante (collector, metrics, signal, replay, dashboard)
lê e escreve este formato — trocar RTD por ProfitDLL não muda nada aqui."""
from __future__ import annotations

from typing import Literal

import pyarrow as pa
from pydantic import BaseModel, Field

Source = Literal["rtd", "dll"]
Aggressor = Literal["buy", "sell"]


class Trade(BaseModel):
    ts_ms: int = Field(description="epoch ms (UTC) do negócio; origem: DAT com ms em America/Sao_Paulo")
    price: float
    qty: int
    buyer: str = Field(description="corretora compradora (ACP)")
    seller: str = Field(description="corretora vendedora (AVD)")
    buy_agent_id: int | None = None   # só a DLL traz
    sell_agent_id: int | None = None  # só a DLL traz
    aggressor: Aggressor | None = None  # DLL traz; RTD infere via LIV0 (Fase 2)
    seq: int = Field(description="sequência monotônica própria do coletor")
    gap: bool = Field(default=False, description="True se a janela girou inteira entre duas leituras")
    source: Source

    def as_row(self) -> dict:
        return self.model_dump()


CSV_COLUMNS = [
    "ts_ms", "price", "qty", "buyer", "seller",
    "buy_agent_id", "sell_agent_id", "aggressor", "seq", "gap", "source",
]

ARROW_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("price", pa.float64()),
    ("qty", pa.int32()),
    ("buyer", pa.string()),
    ("seller", pa.string()),
    ("buy_agent_id", pa.int32()),
    ("sell_agent_id", pa.int32()),
    ("aggressor", pa.string()),
    ("seq", pa.int64()),
    ("gap", pa.bool_()),
    ("source", pa.string()),
])
