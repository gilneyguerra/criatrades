"""FASE 2 (reduzida a verificação) — amostra negócios gravados para conferir o campo AGR contra as cores
do Times & Trades do Profit. Não infere nada: imprime a tabela e você marca na mão.

Uso:  uv run python verify_aggressor.py [--arquivo data/raw/WINZ26_2026-10-28.parquet] [--n 200] [--seed 19]
      sem --arquivo: usa o Parquet mais recente de data/raw. Aceita CSV do probe também.

Registrar o resultado (concordância em %) em docs/LESSONS_LEARNED.md. Critério da Fase 2: ≥ 95 %.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo("America/Sao_Paulo")
ROTULO = {"buy": "Comprador", "sell": "Vendedor", "rlp": "RLP", None: "(sem)"}


def carregar(caminho: Path) -> pl.DataFrame:
    if caminho.suffix.lower() == ".csv":
        return pl.read_csv(caminho)
    return pl.read_parquet(caminho)


def mais_recente(pasta: Path) -> Path:
    arquivos = sorted(pasta.glob("*.parquet"), key=lambda p: p.stat().st_mtime)
    if not arquivos:
        raise SystemExit(f"nenhum Parquet em {pasta}; rode collector.py primeiro ou passe --arquivo")
    return arquivos[-1]


def amostrar(df: pl.DataFrame, n: int, seed: int) -> pl.DataFrame:
    n = min(n, df.height)
    return df.sample(n=n, seed=seed, shuffle=True).sort("ts_ms")


def cabecalho(df: pl.DataFrame, caminho: Path) -> str:
    ultimo = datetime.fromtimestamp(df["ts_ms"].max() / 1000.0, TZ).strftime("%H:%M:%S.%f")[:-3]
    gap_pct = 100.0 * df["gap"].sum() / df.height if df.height else 0.0
    fontes = ",".join(df["source"].unique().to_list())
    return (f"arquivo={caminho.name}  source={fontes}  negócios no arquivo={df.height}  "
            f"hora do último negócio lido={ultimo}  negócios com gap={df['gap'].sum()} ({gap_pct:.2f}%)\n"
            "Dado = janela deslizante do RTD, não stream oficial; o campo AGR é o que o Profit exibe.")


def tabela(amostra: pl.DataFrame) -> str:
    linhas = [f"{'#':>3} {'hora':>12} {'preço':>9} {'qtd':>4} {'comprador':<10} {'vendedor':<10} {'AGR (rtd)':<10} Profit"]
    for i, r in enumerate(amostra.iter_rows(named=True), 1):
        hora = datetime.fromtimestamp(r["ts_ms"] / 1000.0, TZ).strftime("%H:%M:%S.%f")[:-3]
        linhas.append(f"{i:>3} {hora:>12} {r['price']:>9.0f} {r['qty']:>4} {r['buyer']:<10} {r['seller']:<10} "
                      f"{ROTULO.get(r['aggressor'], str(r['aggressor'])):<10} [   ]")
    return "\n".join(linhas)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arquivo", default=None)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=19)
    ap.add_argument("--saida", default=None, help="também grava a tabela num .txt")
    args = ap.parse_args()

    caminho = Path(args.arquivo) if args.arquivo else mais_recente(ROOT / "data" / "raw")
    df = carregar(caminho)
    amostra = amostrar(df, args.n, args.seed)
    dist = amostra.group_by("aggressor").len().sort("aggressor")
    texto = "\n".join([
        "CriaTrades — verificação do agressor (AGR) contra as cores do Profit",
        cabecalho(df, caminho),
        f"amostra={amostra.height} seed={args.seed}  distribuição na amostra: "
        + ", ".join(f"{ROTULO.get(a, str(a))}={n}" for a, n in dist.iter_rows()),
        "",
        tabela(amostra),
        "",
        "Como conferir: no Profit, abra o T&T do dia nesses horários e marque [x] quando a cor concordar com AGR.",
        "Concordância = marcados / (amostra − RLP). Registrar em docs/LESSONS_LEARNED.md. Critério: ≥ 95 %.",
    ])
    print(texto)
    if args.saida:
        Path(args.saida).write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
