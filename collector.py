"""FASE 1 — coletor. Recebe um FeedSource qualquer e grava Parquet; não sabe qual fonte é.

Uso:  uv run python collector.py [--fonte rtd|dll] [--profundidade 500] [--ate 10:50] [--duracao-s N]

Saída: data/raw/{ativo}_{YYYY-MM-DD}.parquet (nunca sobrescreve: se já existir, cria
       {ativo}_{YYYY-MM-DD}_{HHMMSS}.parquet). Jusante lê o glob {ativo}_{YYYY-MM-DD}*.parquet.
Flush a cada 5 s; health log a cada 10 s (negócios/s, gap_pct, latência, fonte).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from loguru import logger

from config_loader import load_config
from feed.base import FeedSource
from schema import ARROW_SCHEMA, Trade
from scheduler import TZ

ROOT = Path(__file__).resolve().parent


def excel_aberto() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq EXCEL.EXE"], capture_output=True,
                             text=True, timeout=10).stdout
        return "EXCEL.EXE" in out.upper()
    except Exception:  # noqa: BLE001
        return False


def caminho_sem_sobrescrever(saida: Path, ativo: str, agora: datetime) -> Path:
    base = saida / f"{ativo}_{agora:%Y-%m-%d}.parquet"
    if not base.exists():
        return base
    return saida / f"{ativo}_{agora:%Y-%m-%d}_{agora:%H%M%S}.parquet"


class Collector:
    def __init__(self, source: FeedSource, ativo: str, saida: Path,
                 flush_s: float = 5.0, health_s: float = 10.0,
                 checar_excel: bool = True, relogio=None) -> None:
        self.source = source
        self.ativo = ativo
        self.saida = Path(saida)
        self.flush_s = flush_s
        self.health_s = health_s
        self.checar_excel = checar_excel
        self._now = relogio or (lambda: datetime.now(TZ))
        self.caminho: Path | None = None
        self._writer: pq.ParquetWriter | None = None
        self._buf: list[dict] = []
        self.total = 0
        self.total_gaps = 0

    # -- parquet -------------------------------------------------------------------------------
    def _abrir(self) -> None:
        self.saida.mkdir(parents=True, exist_ok=True)
        self.caminho = caminho_sem_sobrescrever(self.saida, self.ativo, self._now())
        self._writer = pq.ParquetWriter(str(self.caminho), ARROW_SCHEMA, compression="zstd")
        logger.info("gravando em {}", self.caminho)

    def _flush(self) -> None:
        if not self._buf or self._writer is None:
            return
        self._writer.write_table(pa.Table.from_pylist(self._buf, schema=ARROW_SCHEMA))
        self._buf.clear()

    def _fechar(self) -> None:
        self._flush()
        if self._writer is not None:
            self._writer.close()
            self._writer = None

    # -- loop ------------------------------------------------------------------------------------
    def run(self, ate: datetime | None = None, max_trades: int | None = None) -> Path | None:
        self.source.start()
        self._abrir()
        prox_flush = time.monotonic() + self.flush_s
        prox_health = time.monotonic() + self.health_s
        excel_antes = excel_aberto() if self.checar_excel else False
        try:
            for tr in self.source.iter_trades():
                self._buf.append(tr.as_row())
                self.total += 1
                self.total_gaps += int(tr.gap)
                agora = time.monotonic()
                if agora >= prox_flush:
                    self._flush()
                    prox_flush = agora + self.flush_s
                if agora >= prox_health:
                    self._health_log()
                    if self.checar_excel:
                        excel_agora = excel_aberto()
                        if excel_agora and not excel_antes:
                            logger.warning("EXCEL.EXE abriu durante a coleta: se for a planilha RTD, "
                                           "a exportação pode ser disputada. Feche a planilha.")
                        excel_antes = excel_agora
                    prox_health = agora + self.health_s
                if ate is not None and self._now() >= ate:
                    logger.info("horário limite {} atingido", ate.strftime("%H:%M"))
                    break
                if max_trades is not None and self.total >= max_trades:
                    break
        except KeyboardInterrupt:
            logger.info("interrompido pelo usuário")
        finally:
            self._fechar()
            self.source.stop()
            self._health_log(final=True)
        return self.caminho

    def _health_log(self, final: bool = False) -> None:
        h = self.source.health()
        lat = h.get("latencia_ms")
        logger.info("{} source={} último={} neg/s={:.0f} gap_pct={:.2f} janela={} lat={} total={} gaps={}",
                    "FIM" if final else "health", h.get("source"), h.get("ultimo_negocio"),
                    h.get("negocios_s") or 0.0, h.get("gap_pct") or 0.0, h.get("n_janela"),
                    f"{lat:.1f}ms" if lat is not None else "-", self.total, self.total_gaps)


def _hora(s: str) -> dtime:
    hh, mm = s.split(":")
    return dtime(int(hh), int(mm))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    ap.add_argument("--fonte", choices=["rtd", "dll"], default="rtd")
    ap.add_argument("--profundidade", type=int, default=500, help="N_MAX medido pela Fase 0 (500 nesta máquina)")
    ap.add_argument("--ate", default=None, help="HH:MM local; padrão: fim da janela do config + 5 min")
    ap.add_argument("--duracao-s", type=int, default=None, help="encerra após N segundos (teste)")
    ap.add_argument("--saida", default=str(ROOT / "data" / "raw"))
    args = ap.parse_args()
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    cfg = load_config(args.config)
    if args.fonte == "rtd":
        from feed.rtd_source import RtdSource
        source: FeedSource = RtdSource(ativo=cfg.ativo, profundidade=args.profundidade, poll_ms=100)
    else:
        from feed.profitdll_source import ProfitDllSource
        source = ProfitDllSource(ativo=cfg.ativo)

    agora = datetime.now(TZ)
    if args.duracao_s is not None:
        ate = datetime.fromtimestamp(agora.timestamp() + args.duracao_s, TZ)
    else:
        fim = _hora(args.ate) if args.ate else _hora(cfg.janela[1])
        ate = agora.replace(hour=fim.hour, minute=fim.minute, second=0, microsecond=0)
        if not args.ate:
            ate = datetime.fromtimestamp(ate.timestamp() + 300, TZ)
    if ate <= agora:
        logger.error("horário limite {} já passou; use --ate ou --duracao-s", ate.strftime("%H:%M"))
        sys.exit(2)

    col = Collector(source, ativo=cfg.ativo, saida=Path(args.saida))
    caminho = col.run(ate=ate)
    print(f"source={args.fonte} negócios={col.total} gaps={col.total_gaps} arquivo={caminho}")


if __name__ == "__main__":
    main()
