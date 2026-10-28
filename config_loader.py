"""Carrega config.json (ou o config.example.json) com validação pydantic."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
EXAMPLE = ROOT / ".claude" / "skills" / "flow-config" / "config.example.json"


class Config(BaseModel):
    ativo: str = "WINZ26"
    janela: tuple[str, str] = ("10:00", "10:45")
    tz: str = "America/Sao_Paulo"
    gap_max_pct: float = 2.0
    gap_block_window_s: int = 10          # sinal cuja janela contenha um gap nasce BLOQUEADO
    rlp_separado: bool = True             # negócios rlp fora de burst/absorção/imbalance; métrica própria rlp_net
    poll_ms_base: int = 100
    poll_ms_burst: int = 40
    burst_trades_por_leitura: int = 300
    janela_metricas_s: list[int] = Field(default_factory=lambda: [30, 60, 300])
    burst_min_trades_s: int = 50
    lote_grande_percentil: int = 95
    replay_horizonte_min: list[int] = Field(default_factory=lambda: [1, 3, 5])
    replay_delta_pts: int = 30
    replay_seed: int = 19
    min_sessoes_replay: int = 10
    pontos_por_contrato_brl: float = 0.20


def load_config(path: str | Path | None = None) -> Config:
    """config.json na raiz tem prioridade; senão usa o exemplo versionado."""
    candidates = [Path(path)] if path else [ROOT / "config.json", EXAMPLE]
    for p in candidates:
        if p.exists():
            return Config.model_validate(json.loads(p.read_text(encoding="utf-8")))
    raise FileNotFoundError(f"nenhum config encontrado em {candidates}")
