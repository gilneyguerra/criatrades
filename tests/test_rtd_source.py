"""Partes puras do RtdSource (sem COM): alinhamento da janela deslizante, parse de DAT, AGR, poll adaptativo."""
from __future__ import annotations

from datetime import date

import pytest

from feed.rtd_source import (FIELDS, PollAdaptativo, RtdSource, is_empty_value, is_error_value, parse_dat,
                             percentil)

A = ("10:00:00.001", "XP", 178000.0, 1.0, "BTG", "Comprador")
B = ("10:00:00.002", "BTG", 178005.0, 2.0, "XP", "Vendedor")
C = ("10:00:00.003", "GEN", 178005.0, 1.0, "XP", "Comprador")
D = ("10:00:00.004", "XP", 178010.0, 3.0, "GEN", "RLP")
E = ("10:00:00.005", "XP", 178010.0, 1.0, "GEN", "Vendedor")


def test_alinhar_sem_mudanca():
    assert RtdSource.alinhar([A, B, C], [A, B, C]) == ([], False)


def test_alinhar_desloca_k():
    assert RtdSource.alinhar([A, B, C], [B, C, D]) == ([D], False)
    assert RtdSource.alinhar([A, B, C], [C, D, E]) == ([D, E], False)


def test_alinhar_janela_ainda_enchendo():
    assert RtdSource.alinhar([A], [A, B, C]) == ([B, C], False)
    assert RtdSource.alinhar([], [A, B]) == ([A, B], False)
    assert RtdSource.alinhar([A, B], []) == ([], False)


def test_alinhar_gap_quando_janela_girou_inteira():
    novos, gap = RtdSource.alinhar([A, B], [C, D])
    assert gap is True and novos == [C, D]


def test_alinhar_repetidos_identicos_sao_conservadores():
    assert RtdSource.alinhar([A, A], [A, A]) == ([], False)
    assert RtdSource.alinhar([A, A], [A, A, A]) == ([A], False)
    # prefixo coincide mas o resto não: continua procurando k maior
    assert RtdSource.alinhar([A, B, A, C], [A, C, D, E]) == ([D, E], False)


def test_alinhar_janela_de_500_com_burst():
    prev = [(f"10:00:{i // 1000:02d}.{i % 1000:03d}", "XP", 1.0, 1.0, "BTG", "Comprador") for i in range(500)]
    novos = [(f"10:00:{i // 1000:02d}.{i % 1000:03d}", "XP", 1.0, 1.0, "BTG", "Comprador") for i in range(500, 620)]
    cur = prev[120:] + novos
    assert RtdSource.alinhar(prev, cur) == (novos, False)


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
    assert RtdSource._valida(A) and not RtdSource._valida(("", "XP", 1.0, 1.0, "BTG", "RLP"))


def test_to_trade_mapeia_agressor_e_tipos():
    src = RtdSource(ativo="WINZ26", profundidade=3)
    assert src.fields == FIELDS
    t = src._to_trade(A, gap=True)
    assert (t.buyer, t.seller, t.price, t.qty, t.aggressor, t.gap, t.seq, t.source) == \
        ("XP", "BTG", 178000.0, 1, "buy", True, 1, "rtd")
    assert src._to_trade(B, gap=False).aggressor == "sell"
    assert src._to_trade(D, gap=False).aggressor == "rlp"
    t4 = src._to_trade(("10:00:00.009", "XP", 1.0, 1.0, "XP", "Leilão"), gap=False)
    assert t4.aggressor is None and t4.seq == 4
    assert src.health()["ultimo_negocio"] == "10:00:00.009"


def test_detecta_orientacao():
    src = RtdSource(ativo="WINZ26", profundidade=3)
    src._rows = [A, B, C]
    assert src._detecta_orientacao() is False
    src._rows = [C, None, A]
    assert src._detecta_orientacao() is True
    src._rows = [A, None, None]
    assert src._detecta_orientacao() is True


def test_snapshot_reconstroi_so_linhas_sujas_e_inverte():
    src = RtdSource(ativo="WINZ26", profundidade=3)
    src.fields = FIELDS
    src._cells = [list(C), list(B), list(A)]      # índice 0 = mais novo, como no Profit
    src._rows = [None, None, None]
    src._dirty = {0, 1, 2}
    src.newest_first = True
    assert src.snapshot() == [A, B, C]
    src._cells[0] = list(D)                       # mudou só a linha 0, mas sem marcar suja: não reconstrói
    assert src.snapshot() == [A, B, C]
    src._dirty.add(0)
    assert src.snapshot() == [A, B, D]
    src._cells[1][0] = ""                         # linha inválida some do snapshot
    src._dirty.add(1)
    assert src.snapshot() == [A, D]
    assert src._linhas_invalidas == 1


def test_poll_adaptativo():
    p = PollAdaptativo(poll_ms=100, poll_ms_burst=40, burst_trades_por_leitura=300, taxa_fim_s=150, burst_fim_s=2)
    assert p.atualizar(10, 50, 0.0) == 100
    assert p.atualizar(301, 900, 0.1) == 40           # burst
    assert p.atualizar(20, 400, 0.2) == 40            # taxa ainda alta
    assert p.atualizar(5, 100, 1.0) == 40             # calmo há 0 s
    assert p.atualizar(5, 100, 2.5) == 40             # calmo há 1,5 s
    assert p.atualizar(5, 200, 2.8) == 40             # voltou a subir: zera a contagem
    assert p.atualizar(5, 100, 3.0) == 40
    assert p.atualizar(5, 100, 5.1) == 100            # 2,1 s calmo: volta ao base
    assert p.modo_burst is False


def test_percentil():
    assert percentil([], 95) == 0.0
    assert percentil([5.0], 95) == 5.0
    v = [float(i) for i in range(1, 101)]
    assert percentil(v, 50) == 50.0 or percentil(v, 50) == 51.0
    assert percentil(v, 95) == 95.0
    assert percentil(v, 100) == 100.0
