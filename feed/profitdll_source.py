"""STUB da fonte ProfitDLL (Nelogica, produto pago "DLL Real Time").

Assinatura final já fixada para que collector.py não mude quando a licença chegar.
Regras quando implementar:
  - ctypes.WinDLL (stdcall, 64 bits), init SOMENTE com DLLInitializeMarketLogin (market data).
  - NUNCA DLLInitializeLogin (roteamento) nem qualquer objeto de ordem.
  - Callback de negócio roda em thread da DLL: só enfileirar (queue.Queue); o consumidor é iter_trades().
  - Callback esperado: agente comprador, agente vendedor, tipo (agressor) — confirmar no manual.
"""
from __future__ import annotations

import queue
from typing import Iterator

from schema import Trade


class ProfitDllSource:
    source = "dll"

    def __init__(self, ativo: str, dll_path: str | None = None) -> None:
        self.ativo = ativo
        self.dll_path = dll_path
        self._q: queue.Queue[Trade] = queue.Queue()
        self._running = False

    def start(self) -> None:
        raise NotImplementedError(
            "ProfitDLL ainda não licenciada. Contratar via corporativo@nelogica.com.br; "
            "depois implementar DLLInitializeMarketLogin + callbacks produtor-consumidor."
        )

    def stop(self) -> None:
        self._running = False

    def iter_trades(self) -> Iterator[Trade | None]:
        while self._running:
            try:
                yield self._q.get(timeout=0.5)
            except queue.Empty:
                yield None  # heartbeat (mesmo contrato do RtdSource)

    def health(self) -> dict:
        return {"source": self.source, "ultimo_negocio": None, "negocios_s": 0.0,
                "gap_pct": 0.0, "n_janela": 0, "latencia_ms": None, "status": "stub"}
