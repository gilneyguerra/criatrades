from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

from schema import ARROW_SCHEMA
from verify_aggressor import amostrar, cabecalho, carregar, tabela

TZ = ZoneInfo("America/Sao_Paulo")


def _parquet(tmp_path: Path, n: int = 500) -> Path:
    base = int(datetime(2026, 10, 27, 10, 0, tzinfo=TZ).timestamp() * 1000)
    agr = ["buy", "sell", "rlp", None]
    rows = [{"ts_ms": base + i * 11, "price": 178000.0 + i % 7 * 5, "qty": 1 + i % 3, "buyer": "XP",
             "seller": "BTG", "buy_agent_id": None, "sell_agent_id": None, "aggressor": agr[i % 4],
             "seq": i + 1, "gap": i == 7, "source": "rtd"} for i in range(n)]
    p = tmp_path / "WINZ26_2026-10-27.parquet"
    pl.from_arrow(__import__("pyarrow").Table.from_pylist(rows, schema=ARROW_SCHEMA)).write_parquet(p)
    return p


def test_amostra_deterministica_e_ordenada(tmp_path: Path):
    df = carregar(_parquet(tmp_path))
    a1 = amostrar(df, 200, seed=19)
    a2 = amostrar(df, 200, seed=19)
    assert a1.height == 200 and a1.equals(a2)
    assert a1["ts_ms"].is_sorted()
    assert a1["seq"].n_unique() == 200
    assert amostrar(df, 1000, seed=19).height == 500


def test_cabecalho_e_tabela_respeitam_a_linguagem(tmp_path: Path):
    p = _parquet(tmp_path)
    df = carregar(p)
    cab = cabecalho(df, p)
    for chave in ("source=rtd", "negócios no arquivo=500", "hora do último negócio lido=10:00:05.489",
                  "negócios com gap=1", "janela deslizante"):
        assert chave in cab
    t = tabela(amostrar(df, 8, seed=1))
    assert "Comprador" in t or "Vendedor" in t or "RLP" in t
    for proibida in ("compra", "venda", "oportunidade", "lucro", "P&L"):
        assert proibida not in t.lower().replace("comprador", "").replace("vendedor", "")
