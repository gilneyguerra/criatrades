"""Collector é agnóstico de fonte: roda igual com uma fonte falsa, com o stub da DLL e com RtdSource."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from collector import Collector, TimerFino, caminho_sem_sobrescrever, medir_resolucao_sleep
from feed.base import FeedSource
from feed.profitdll_source import ProfitDllSource
from schema import CSV_COLUMNS, Trade

TZ = ZoneInfo("America/Sao_Paulo")
T0 = datetime(2026, 10, 27, 10, 0, 0, tzinfo=TZ)


class FakeSource:
    source = "rtd"

    def __init__(self, trades: list[Trade], gaps: list[dict] | None = None) -> None:
        self._trades = trades
        self._gaps = gaps or []
        self.started = self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def iter_trades(self):
        yield from self._trades

    def health(self) -> dict:
        return {"source": "rtd", "ultimo_negocio": "10:00:00.000", "negocios_s": 1.0, "gap_pct": 0.5,
                "n_janela": len(self._trades), "profundidade": 500, "latencia_ms": 2.0, "latencia_max_ms": 9.0,
                "jitter_p50_ms": 100.0, "jitter_p95_ms": 130.0, "jitter_max_ms": 900.0,
                "intervalo_ms_efetivo": 40, "modo_burst": True, "gaps": self._gaps,
                "leituras": 10, "leituras_instaveis": 1}


def _trades(n: int, gap_em: int | None = None) -> list[Trade]:
    base = int(T0.timestamp() * 1000)
    agr = ["buy", "sell", "rlp", "buy"]
    return [Trade(ts_ms=base + i * 7, price=178000.0 + 5 * (i % 3), qty=1 + i % 4,
                  buyer="XP", seller="BTG", aggressor=agr[i % 4],
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
    assert set(df["aggressor"].unique().to_list()) == {"buy", "sell", "rlp"}


def test_summary_json(tmp_path: Path):
    gaps = [{"hora": "10:00:00.070", "seq": 11, "buraco_ms": 172, "perda_estimada": 100, "taxa_s": 630}]
    src = FakeSource(_trades(50, gap_em=10), gaps=gaps)
    col = Collector(src, ativo="WINZ26", saida=tmp_path, checar_excel=False, relogio=lambda: T0)
    col.run()
    s = json.loads((tmp_path / "WINZ26_2026-10-27.summary.json").read_text(encoding="utf-8"))
    assert s["total"] == 50 and s["source"] == "rtd" and s["n_max_usado"] == 500
    assert s["aggressor"] == {"buy": 25, "sell": 13, "rlp": 12, "None": 0}
    assert s["gaps"] == gaps and s["gaps_total"] == 1
    assert s["pico_negocios_s"] == 50 and s["pico_em"] == "10:00:00"
    assert s["jitter_ms"] == {"p50": 100.0, "p95": 130.0, "max": 900.0}
    assert s["hora_ultimo_negocio"] == "10:00:00.000"
    assert s["negocios_por_segundo"] == {"10:00:00": 50}
    assert s["arquivo"].endswith("WINZ26_2026-10-27.parquet")
    assert col.summary()["gaps"] == gaps


def test_nunca_sobrescreve(tmp_path: Path):
    Collector(FakeSource(_trades(3)), "WINZ26", tmp_path, checar_excel=False, relogio=lambda: T0).run()
    t1 = T0 + timedelta(minutes=7)
    c2 = Collector(FakeSource(_trades(2)), "WINZ26", tmp_path, checar_excel=False, relogio=lambda: t1)
    p2 = c2.run()
    assert p2 == tmp_path / "WINZ26_2026-10-27_100700.parquet"
    assert (tmp_path / "WINZ26_2026-10-27_100700.summary.json").exists()
    assert pl.read_parquet(tmp_path / "WINZ26_2026-10-27.parquet").height == 3
    assert pl.read_parquet(p2).height == 2
    assert caminho_sem_sobrescrever(tmp_path, "WINZ26", T0).name.endswith("_100000.parquet")


def test_horario_limite_vale_mesmo_sem_negocios(tmp_path: Path):
    """Mercado parado: a fonte só manda heartbeats (None); o collector ainda precisa encerrar na hora."""
    class Parada(FakeSource):
        def __init__(self):
            super().__init__([])
            self.heartbeats = 0

        def iter_trades(self):
            while True:
                self.heartbeats += 1
                if self.heartbeats > 1000:
                    raise AssertionError("collector não encerrou no horário limite")
                yield None

    relogio = [T0 - timedelta(seconds=1)]
    ticks = {"n": 0}

    def agora():
        ticks["n"] += 1
        return relogio[0] if ticks["n"] > 3 else T0 - timedelta(minutes=1)

    src = Parada()
    col = Collector(src, "WINZ26", tmp_path, checar_excel=False, relogio=agora)
    col.run(ate=T0 - timedelta(seconds=1))
    assert col.total == 0 and src.heartbeats < 1000 and src.stopped
    assert (tmp_path / "WINZ26_2026-10-27.summary.json").exists()


def test_max_trades_e_horario_limite(tmp_path: Path):
    col = Collector(FakeSource(_trades(100)), "WINZ26", tmp_path, checar_excel=False, relogio=lambda: T0)
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


def test_timer_fino_mede_e_desfaz():
    with TimerFino() as t:
        assert t.ativo is True                      # Windows: winmm sempre disponível
        assert 0.5 < t.resolucao_antes_ms < 60      # sem timeBeginPeriod costuma dar ~15,6 ms
        assert 0.5 < t.resolucao_depois_ms < 6      # com timeBeginPeriod(1) deve ficar perto de 1-2 ms
        info = t.info()
        assert info["timeBeginPeriod_ativo"] and info["periodo_ms"] == 1
    assert t.ativo is False
    assert medir_resolucao_sleep(n=3) > 0


def test_summary_tem_timer(tmp_path: Path):
    col = Collector(FakeSource(_trades(5)), "WINZ26", tmp_path, checar_excel=False, relogio=lambda: T0)
    col.run()
    s = json.loads((tmp_path / "WINZ26_2026-10-27.summary.json").read_text(encoding="utf-8"))
    assert s["timer"]["timeBeginPeriod_ativo"] is True
    assert s["timer"]["resolucao_sleep_efetiva_ms"] < 6
    assert col.timer.ativo is False  # desfeito ao sair do run()
