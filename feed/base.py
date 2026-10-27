"""Contrato de fonte de dados. collector.py só conhece isto."""
from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable

from schema import Trade


@runtime_checkable
class FeedSource(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def iter_trades(self) -> Iterator[Trade]:
        """Gerador bloqueante: entrega negócios novos em ordem cronológica. Termina em stop()."""
        ...
    def health(self) -> dict:
        """Chaves mínimas: source, ultimo_negocio, negocios_s, gap_pct, n_janela, latencia_ms."""
        ...
