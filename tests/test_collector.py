"""Collector é agnóstico de fonte: roda igual com uma fonte falsa, com o stub da DLL e com RtdSource."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from collector import Collector, caminho_sem_sobrescrever
from feed.base import FeedSource
from feed.profitdll_source import ProfitDllSource
from schema import CSV_COLUMNS, Trade

TZ = ZoneInfo("America/Sao_Paulo")
T0 = datetime(2026, 10, 27, 10, 0, 0, tzinfo=TZ)


class FakeSource:
    source = "rtd"

    def __init__(self, trades: list[Trade]) -> None:
        self._trades = trades
        self.started = self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def iter_trades(self):
        yield from self._trades

    def health(self) -> dict:
        return {"source": "rtd", "ultimo_negocio": "10:00:00.000", "negocios_s": 1.0,
                "gap_pct": 0.0, "n_janela": len(self._trades), "latencia_ms": 2.0}


def _trades(n: int, gap_em: int | None = None) -> list[Trade]:
    base = int(T0.timestamp() * 1000)
    return [Trade(ts_ms=base + i * 7, price=178000.0 + 5 * (i % 3), qty=1 + i % 4,
                  buyer="XP", seller="BTG", aggressor="buy" if i % 2 else "sell",
                  seq=i + 1, gap=(i == gap_em), source="rtd") for i in range(n)]


def test_protocolos():
    assert isinstance(FakeSource([]), FeedSource)
    assert isinstance(ProfitDllSource("WINZ26"), FeedSource)


def test_grava_parquet_com_schema_completo(tmp_path: Path):
    src = FakeSource(_trades(50, gap_em=10))
    col = Collector(src, ativo="WINZ26", saida=tmp_path, checar_excel=False, relogio=lambda: T0)
    caminho = col.run()
    assert src.started and src.stopped
    assert caminho == tmp_path / "WINZ26_2026-10-27.parquet"
    df = pl.read_parquet(caminho)
    assert df.columns == CSV_COLUMNS
    assert df.height == 50
    assert df["seq"].is_sorted()
    assert df["gap"].sum() == 1
    assert df["source"].unique().to_list() == ["rtd"]
    assert df["buy_agent_id"].null_count() == 50
    assert set(df["aggressor"].unique().to_list()) == {"buy", "sell"}


def test_nunca_sobrescreve(tmp_path: Path):
    Collector(FakeSource(_trades(3)), "WINZ26", tmp_path, checar_excel=False, relogio=lambda: T0).run()
    t1 = T0 + timedelta(minutes=7)
    c2 = Collector(FakeSource(_trades(2)), "WINZ26", tmp_path, checar_excel=False, relogio=lambda: t1)
    p2 = c2.run()
    assert p2 == tmp_path / "WINZ26_2026-10-27_100700.parquet"
    assert pl.read_parquet(tmp_path / "WINZ26_2026-10-27.parquet").height == 3
    assert pl.read_parquet(p2).height == 2
    assert caminho_sem_sobrescrever(tmp_path, "WINZ26", T0).name.endswith("_100000.parquet")


def test_max_trades_e_horario_limite(tmp_path: Path):
    relogio = iter([T0] * 100)
    col = Collector(FakeSource(_trades(100)), "WINZ26", tmp_path, checar_excel=False,
                    relogio=lambda: next(relogio))
    col.run(max_trades=20)
    assert col.total == 20
    ate = T0 - timedelta(seconds=1)
    col2 = Collector(FakeSource(_trades(100)), "WINZ26", tmp_path, checar_excel=False, relogio=lambda: T0)
    col2.run(ate=ate)
    assert col2.total == 1  # lê um negócio, vê que passou da hora, encerra


def test_stub_dll_falha_claro(tmp_path: Path):
    col = Collector(ProfitDllSource("WINZ26"), "WINZ26", tmp_path, checar_excel=False)
    with pytest.raises(NotImplementedError, match="nelogica"):
        col.run()
