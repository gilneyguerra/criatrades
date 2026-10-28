"""FASE 1 — coletor. Recebe um FeedSource qualquer e grava Parquet; não sabe qual fonte é.

Uso:  uv run python collector.py [--fonte rtd|dll] [--profundidade 500] [--ate 10:50] [--duracao-s N]

Saída: data/raw/{ativo}_{YYYY-MM-DD}.parquet (nunca sobrescreve: se já existir, cria
       {ativo}_{YYYY-MM-DD}_{HHMMSS}.parquet) + {mesmo nome}.summary.json ao fim da sessão.
       Jusante lê o glob {ativo}_{YYYY-MM-DD}*.parquet.
Flush a cada 5 s (com gc.collect() explícito: a fonte RTD roda com GC desligado); health log a cada 10 s.
"""
from __future__ import annotations

import argparse
import gc
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, time as dtime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from loguru import logger

from config_loader import load_config
from feed.base import FeedSource
from schema import ARROW_SCHEMA
from scheduler import TZ

ROOT = Path(__file__).resolve().parent


def excel_aberto() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq EXCEL.EXE"], capture_output=True,
                             timeout=10).stdout  # bytes: o tasklist escreve na code page OEM, não em UTF-8
        return b"EXCEL.EXE" in out.upper()
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
        self.caminho_summary: Path | None = None
        self._writer: pq.ParquetWriter | None = None
        self._buf: list[dict] = []
        self.total = 0
        self.total_gaps = 0
        self.inicio: datetime | None = None
        self.por_segundo: Counter[int] = Counter()      # epoch s -> negócios
        self.agressores: Counter[str] = Counter()       # buy / sell / rlp / None
        self.gaps_vistos: list[dict] = []               # (hora, seq) dos negócios com gap=True

    # -- parquet -------------------------------------------------------------------------------
    def _abrir(self) -> None:
        self.saida.mkdir(parents=True, exist_ok=True)
        self.inicio = self._now()
        self.caminho = caminho_sem_sobrescrever(self.saida, self.ativo, self.inicio)
        self.caminho_summary = self.caminho.with_name(self.caminho.stem + ".summary.json")
        self._writer = pq.ParquetWriter(str(self.caminho), ARROW_SCHEMA, compression="zstd")
        logger.info("gravando em {}", self.caminho)

    def _flush(self) -> None:
        if self._buf and self._writer is not None:
            self._writer.write_table(pa.Table.from_pylist(self._buf, schema=ARROW_SCHEMA))
            self._buf.clear()
        gc.collect()  # único ponto de coleta enquanto a fonte roda com gc.disable()

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
                if tr is not None:  # None = heartbeat sem negócio novo
                    self._buf.append(tr.as_row())
                    self.total += 1
                    self.por_segundo[tr.ts_ms // 1000] += 1
                    self.agressores[str(tr.aggressor)] += 1
                    if tr.gap:
                        self.total_gaps += 1
                        self.gaps_vistos.append({"ts_ms": tr.ts_ms, "seq": tr.seq})
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
            self._escrever_summary()
        return self.caminho

    # -- relatórios ------------------------------------------------------------------------------
    def _serie_ultimos_s(self, n: int = 10) -> list[int]:
        if not self.por_segundo:
            return []
        ultimo = max(self.por_segundo)
        return [self.por_segundo.get(s, 0) for s in range(ultimo - n + 1, ultimo + 1)]

    def _health_log(self, final: bool = False) -> None:
        h = self.source.health()
        lat = h.get("latencia_ms")
        gaps = h.get("gaps") or []
        ultimo_gap = gaps[-1]["hora"] if gaps else "-"
        logger.info("{} source={} último={} neg/s={:.0f} série10s={} gap_pct={:.2f} gaps={} último_gap={} "
                    "janela={} lat={} jitter_p95={:.0f}ms jitter_max={:.0f}ms poll={}ms{} total={}",
                    "FIM" if final else "health", h.get("source"), h.get("ultimo_negocio"),
                    h.get("negocios_s") or 0.0, self._serie_ultimos_s(), h.get("gap_pct") or 0.0,
                    self.total_gaps, ultimo_gap, h.get("n_janela"),
                    f"{lat:.1f}ms" if lat is not None else "-",
                    h.get("jitter_p95_ms") or 0.0, h.get("jitter_max_ms") or 0.0,
                    h.get("intervalo_ms_efetivo", "-"), " BURST" if h.get("modo_burst") else "", self.total)

    def summary(self) -> dict:
        h = self.source.health()
        pico_s, pico_n = (max(self.por_segundo.items(), key=lambda kv: kv[1]) if self.por_segundo else (None, 0))
        gaps_fonte = h.get("gaps") or []
        return {
            "ativo": self.ativo,
            "source": h.get("source"),
            "arquivo": str(self.caminho),
            "inicio": self.inicio.isoformat() if self.inicio else None,
            "fim": self._now().isoformat(),
            "total": self.total,
            "pico_negocios_s": pico_n,
            "pico_em": datetime.fromtimestamp(pico_s, TZ).strftime("%H:%M:%S") if pico_s else None,
            "hora_ultimo_negocio": h.get("ultimo_negocio"),
            "n_janela": h.get("n_janela"),
            "n_max_usado": h.get("profundidade"),
            "gap_pct_leituras": h.get("gap_pct"),
            "gaps_total": self.total_gaps,
            "gaps": gaps_fonte if gaps_fonte else self.gaps_vistos,
            "aggressor": {k: self.agressores.get(k, 0) for k in ("buy", "sell", "rlp", "None")},
            "jitter_ms": {"p50": h.get("jitter_p50_ms"), "p95": h.get("jitter_p95_ms"), "max": h.get("jitter_max_ms")},
            "latencia_ms": {"media": h.get("latencia_ms"), "max": h.get("latencia_max_ms")},
            "leituras": h.get("leituras"),
            "leituras_instaveis": h.get("leituras_instaveis"),
            "negocios_por_segundo": {datetime.fromtimestamp(s, TZ).strftime("%H:%M:%S"): n
                                     for s, n in sorted(self.por_segundo.items())},
        }

    def _escrever_summary(self) -> None:
        if self.caminho_summary is None:
            return
        self.caminho_summary.write_text(json.dumps(self.summary(), ensure_ascii=False, indent=2, default=str),
                                        encoding="utf-8")
        logger.info("summary em {}", self.caminho_summary)


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
        source: FeedSource = RtdSource(ativo=cfg.ativo, profundidade=args.profundidade,
                                       poll_ms=cfg.poll_ms_base, poll_ms_burst=cfg.poll_ms_burst,
                                       burst_trades_por_leitura=cfg.burst_trades_por_leitura)
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
    print(f"source={args.fonte} negócios={col.total} gaps={col.total_gaps} arquivo={caminho} "
          f"summary={col.caminho_summary}")


if __name__ == "__main__":
    main()
