"""Partes puras do RtdSource (sem COM): alinhamento da janela deslizante, parse de DAT, mapeamento AGR."""
from __future__ import annotations

from datetime import date

import pytest

from feed.rtd_source import FIELDS, RtdSource, is_empty_value, is_error_value, parse_dat

A = ("10:00:00.001", "XP", 178000.0, 1.0, "BTG", "Comprador")
B = ("10:00:00.002", "BTG", 178005.0, 2.0, "XP", "Vendedor")
C = ("10:00:00.003", "GEN", 178005.0, 1.0, "XP", "Comprador")
D = ("10:00:00.004", "XP", 178010.0, 3.0, "GEN", "Comprador")
E = ("10:00:00.005", "XP", 178010.0, 1.0, "GEN", "Vendedor")


def test_alinhar_sem_mudanca():
    assert RtdSource.alinhar([A, B, C], [A, B, C]) == ([], False)


def test_alinhar_desloca_k():
    assert RtdSource.alinhar([A, B, C], [B, C, D]) == ([D], False)
    assert RtdSource.alinhar([A, B, C], [C, D, E]) == ([D, E], False)


def test_alinhar_janela_ainda_enchendo():
    assert RtdSource.alinhar([A], [A, B, C]) == ([B, C], False)
    assert RtdSource.alinhar([], [A, B]) == ([A, B], False)


def test_alinhar_gap_quando_janela_girou_inteira():
    novos, gap = RtdSource.alinhar([A, B], [C, D])
    assert gap is True and novos == [C, D]


def test_alinhar_repetidos_identicos_sao_conservadores():
    # dois negócios idênticos consecutivos: o menor k vence (não inventa negócio)
    assert RtdSource.alinhar([A, A], [A, A]) == ([], False)
    assert RtdSource.alinhar([A, A], [A, A, A]) == ([A], False)


def test_parse_dat_ms_em_sao_paulo():
    # 2026-10-27 10:00:00.250 America/Sao_Paulo (UTC-3) = 13:00:00.250Z
    assert parse_dat("10:00:00.250", date(2026, 10, 27)) == 1793106000250
    assert parse_dat("10:00:00", date(2026, 10, 27)) == 1793106000000
    assert parse_dat("10:00:00.5", date(2026, 10, 27)) == 1793106000500


def test_valores_erro_e_vazio():
    assert is_error_value("Comando Inválido")
    assert is_error_value("erro")
    assert not is_error_value("Comprador")
    assert not is_error_value(178000.0)
    assert is_empty_value(None) and is_empty_value("  ") and not is_empty_value(0.0)


def test_to_trade_mapeia_agressor_e_tipos():
    src = RtdSource(ativo="WINZ26", profundidade=3)
    assert src.fields == FIELDS
    t = src._to_trade(A, gap=True)
    assert (t.buyer, t.seller, t.price, t.qty, t.aggressor, t.gap, t.seq, t.source) == \
        ("XP", "BTG", 178000.0, 1, "buy", True, 1, "rtd")
    t2 = src._to_trade(B, gap=False)
    assert t2.aggressor == "sell" and t2.seq == 2
    t3 = src._to_trade(("10:00:00.009", "XP", 1.0, 1.0, "XP", "Direto"), gap=False)
    assert t3.aggressor is None
    assert src.health()["ultimo_negocio"] == "10:00:00.009"


@pytest.mark.parametrize("linhas,esperado", [
    ([A, B, C], False),   # mais antigo primeiro
    ([C, B, A], True),    # mais novo primeiro (comportamento real do T&T0)
    ([A], True),
])
def test_detecta_orientacao(linhas, esperado):
    src = RtdSource(ativo="WINZ26", profundidade=3)
    assert src._detecta_orientacao(linhas) is esperado
