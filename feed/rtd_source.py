"""Fonte RTD do Profit (Nelogica) — cliente COM local, polling, dedup por alinhamento, detecção de gap.

Fatos medidos nesta máquina (2026-10-27), ver docs/LESSONS_LEARNED.md:
  - ProgID rtdtrading.rtdserver; LocalServer32 = profitchart.exe (COM out-of-proc, o Profit é o servidor).
  - ConnectData funciona SEM ServerStart. ServerStart(None) causa access violation dentro do Profit;
    ServerStart(objeto Python) dá "Tipo não correspondente". Portanto: nunca chamar ServerStart.
  - ConnectData(id, ["T&T0","DAT",i], True) -> (valor, True). RefreshData(n) -> (((ids...),(valores...)), n)
    e devolve SÓ os tópicos alterados desde a última chamada (~2 ms).
  - A janela T&T0 é deslizante (últimos N negócios), não é stream. N_MAX medido = 500 (índice 500 -> AV).
  - Tópico/campo inexistente NÃO devolve "Comando Inválido": provoca access violation dentro do profitchart.exe
    (vem como com_error). Não sondar nomes aleatórios em produção.
  - Campo extra T&T0/AGR/i devolve "Comprador"/"Vendedor" = agressor. Dispensa a inferência via LIV0.
"""
from __future__ import annotations

import re
import time
from collections import deque
from datetime import date, datetime
from typing import Iterator
from zoneinfo import ZoneInfo

import pythoncom
import pywintypes
import win32com.client
from loguru import logger

from schema import Trade

PROGID = "rtdtrading.rtdserver"
TZ = ZoneInfo("America/Sao_Paulo")
JANELA_TT = "T&T0"
FIELDS = ("DAT", "ACP", "PRE", "QUL", "AVD", "AGR")  # 5 da planilha + AGR (agressor), descoberto na Fase 0
AGRESSOR_MAP = {"comprador": "buy", "vendedor": "sell"}  # "RLP" (Retail Liquidity Provider) -> None
_ERR_RE = re.compile(r"inv[aá]lid|erro|error|indispon|n[aã]o dispon|comando", re.I)

INSTRUCAO_FALHA = (
    "Não foi possível assinar T&T0 no RTD do Profit. Verifique, nesta ordem:\n"
    "  1. Feche a planilha Times and Trades.xlsx (só UMA exportação em tempo real por vez).\n"
    "  2. No Profit, abra uma janela Times & Trades do ativo (a primeira vira T&T0).\n"
    "  3. Profit > Arquivo > Exportar em Tempo Real > Configurações: RTD habilitado.\n"
    "  4. Só um Profit aberto; Profit e Python no mesmo nível de permissão (ambos sem 'admin')."
)


def is_error_value(v: object) -> bool:
    return isinstance(v, str) and bool(_ERR_RE.search(v))


def is_empty_value(v: object) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def parse_dat(dat: str, dia: date | None = None) -> int:
    """'HH:MM:SS.mmm' (hora local America/Sao_Paulo) -> epoch ms UTC."""
    dia = dia or datetime.now(TZ).date()
    hh, mm, rest = dat.strip().split(":")
    ss, _, ms = rest.partition(".")
    us = int((ms or "0").ljust(3, "0")[:3]) * 1000
    dt = datetime(dia.year, dia.month, dia.day, int(hh), int(mm), int(ss), us, tzinfo=TZ)
    return int(dt.timestamp() * 1000)


class RtdClient:
    """Camada fina sobre IRtdServer. Mantém o cache de valor por TopicID."""

    def __init__(self) -> None:
        self._srv = None
        self._next_id = 1
        self._topics: dict[int, tuple] = {}
        self._values: dict[int, object] = {}

    def open(self) -> None:
        pythoncom.CoInitialize()
        self._srv = win32com.client.Dispatch(PROGID)

    def connect(self, *topic: object) -> tuple[int, object]:
        tid = self._next_id
        self._next_id += 1
        raw = self._srv.ConnectData(tid, list(topic), True)
        value = raw[0] if isinstance(raw, tuple) else raw
        self._topics[tid] = topic
        self._values[tid] = value
        return tid, value

    def try_connect(self, *topic: object) -> tuple[bool, object, int | None]:
        """(True, valor, tid) se o tópico responde; (False, motivo, None) se dá erro ou valor de erro."""
        try:
            tid, value = self.connect(*topic)
        except pywintypes.com_error as e:
            return False, f"com_error: {e}", None
        if is_error_value(value):
            self.disconnect(tid)
            return False, value, None
        return True, value, tid

    def refresh(self) -> tuple[int, float]:
        """Aplica os tópicos alterados ao cache. Retorna (nº alterados, latência ms)."""
        t0 = time.perf_counter()
        raw = self._srv.RefreshData(len(self._topics))
        lat = (time.perf_counter() - t0) * 1000.0
        data, n = (raw if isinstance(raw, tuple) and len(raw) == 2 else (None, 0))
        if data:
            ids, vals = data
            for tid, v in zip(ids, vals):
                self._values[tid] = v
        return int(n or 0), lat

    def value(self, tid: int) -> object:
        return self._values.get(tid)

    def disconnect(self, tid: int) -> None:
        try:
            self._srv.DisconnectData(tid)
        except pywintypes.com_error:
            pass
        self._topics.pop(tid, None)
        self._values.pop(tid, None)

    def close(self) -> None:
        if self._srv is None:
            return
        for tid in list(self._topics):
            self.disconnect(tid)
        try:
            self._srv.ServerTerminate()
        except pywintypes.com_error:
            pass
        self._srv = None
        pythoncom.CoUninitialize()


class RtdSource:
    """FeedSource sobre a janela deslizante T&T0.

    Dedup por ALINHAMENTO: a leitura nova deve conter a leitura anterior deslocada de k linhas;
    as k linhas mais novas são negócios novos. Se nenhum deslocamento alinha, a janela girou
    inteira entre duas leituras -> gap=True no primeiro negócio da leitura.
    """

    source = "rtd"

    def __init__(self, ativo: str, profundidade: int = 598, poll_ms: int = 100,
                 fields: tuple[str, ...] = FIELDS, max_estabiliza: int = 3) -> None:
        self.ativo = ativo
        self.profundidade = profundidade
        self.poll_ms = poll_ms
        self.fields = fields
        self.max_estabiliza = max_estabiliza
        self.client = RtdClient()
        self._row_tids: list[list[int]] = []
        self._prev: list[tuple] = []
        self._running = False
        self._seq = 0
        self._reads = 0
        self._gap_reads = 0
        self._instaveis = 0
        self._lat: deque[float] = deque(maxlen=100)
        self._lat_max = 0.0
        self._poll_max_ms = 0.0
        self._ultimo_poll: float | None = None
        self._ts_recentes: deque[float] = deque()
        self._ultimo_dat: str | None = None
        self.newest_first = True
        self._linhas_invalidas = 0

    # -- ciclo de vida -------------------------------------------------------------------------
    def start(self) -> None:
        self.client.open()
        ok, atv, _ = self.client.try_connect(JANELA_TT, "INFO", "ATV")
        if not ok:
            self.client.close()
            raise RuntimeError(f"{INSTRUCAO_FALHA}\n  Retorno do RTD: {atv!r}")
        if str(atv) != self.ativo:
            logger.warning("T&T0 mostra {} mas o config pede {}; seguindo com o que o Profit exibe",
                           atv, self.ativo)
            self.ativo = str(atv)
        for i in range(self.profundidade):
            tids = []
            for f in self.fields:
                ok, v, tid = self.client.try_connect(JANELA_TT, f, i)
                if not ok:
                    self.client.close()
                    raise RuntimeError(f"T&T0/{f}/{i} recusado ({v!r}); profundidade máxima menor que "
                                       f"{self.profundidade}. Rode probe.py para medir N_MAX.")
                tids.append(tid)
            self._row_tids.append(tids)
        self._estabiliza()
        self.newest_first = self._detecta_orientacao(self._linhas_brutas())
        self._prev = self.snapshot()
        self._running = True
        logger.info("RtdSource ok: ativo={} profundidade={} newest_first={} linhas_validas={}",
                    self.ativo, self.profundidade, self.newest_first, len(self._prev))

    def stop(self) -> None:
        self._running = False
        self.client.close()

    # -- leitura -------------------------------------------------------------------------------
    def _estabiliza(self) -> float:
        """RefreshData até não haver tópico alterado (leitura coerente). Retorna latência da 1ª chamada."""
        n, lat = self.client.refresh()
        tent = 0
        while n > 0 and tent < self.max_estabiliza:
            n, _ = self.client.refresh()
            if n > 0:
                self._instaveis += 1
            tent += 1
        return lat

    def _linhas_brutas(self) -> list[tuple]:
        return [tuple(self.client.value(t) for t in tids) for tids in self._row_tids]

    @staticmethod
    def _valida(row: tuple) -> bool:
        return not any(is_empty_value(v) or is_error_value(v) for v in row)

    def _detecta_orientacao(self, linhas: list[tuple]) -> bool:
        validas = [r for r in linhas if self._valida(r)]
        if len(validas) < 2:
            return True
        i_dat = self.fields.index("DAT")
        try:
            return parse_dat(str(validas[0][i_dat])) >= parse_dat(str(validas[-1][i_dat]))
        except ValueError:
            return True

    def snapshot(self) -> list[tuple]:
        """Linhas válidas da janela, do mais antigo para o mais novo."""
        linhas = self._linhas_brutas()
        validas = [r for r in linhas if self._valida(r)]
        self._linhas_invalidas += len(linhas) - len(validas)
        if self.newest_first:
            validas.reverse()
        return validas

    @staticmethod
    def alinhar(prev: list[tuple], cur: list[tuple]) -> tuple[list[tuple], bool]:
        """Retorna (linhas novas, gap). Procura o menor k tal que cur começa com prev[k:]."""
        if not prev:
            return cur, False
        for k in range(len(prev)):
            m = len(prev) - k
            if m <= len(cur) and cur[0] == prev[k] and cur[:m] == prev[k:]:
                return cur[m:], False
        return cur, True

    def _to_trade(self, row: tuple, gap: bool) -> Trade:
        d = dict(zip(self.fields, row))
        self._seq += 1
        self._ultimo_dat = str(d["DAT"])
        return Trade(
            ts_ms=parse_dat(self._ultimo_dat),
            price=float(d["PRE"]),
            qty=int(float(d["QUL"])),
            buyer=str(d["ACP"]).strip(),
            seller=str(d["AVD"]).strip(),
            aggressor=AGRESSOR_MAP.get(str(d.get("AGR", "")).strip().lower()),
            seq=self._seq,
            gap=gap,
            source=self.source,
        )

    def iter_trades(self) -> Iterator[Trade]:
        periodo = self.poll_ms / 1000.0
        while self._running:
            t0 = time.perf_counter()
            if self._ultimo_poll is not None:
                self._poll_max_ms = max(self._poll_max_ms, (t0 - self._ultimo_poll) * 1000.0)
            self._ultimo_poll = t0
            lat = self._estabiliza()
            self._lat.append(lat)
            self._lat_max = max(self._lat_max, lat)
            cur = self.snapshot()
            novos, gap = self.alinhar(self._prev, cur)
            self._reads += 1
            if gap:
                self._gap_reads += 1
                self._log_gap(cur)
            agora = time.monotonic()
            for i, row in enumerate(novos):
                self._ts_recentes.append(agora)
                yield self._to_trade(row, gap=(gap and i == 0))
            while self._ts_recentes and agora - self._ts_recentes[0] > 1.0:
                self._ts_recentes.popleft()
            self._prev = cur
            resto = periodo - (time.perf_counter() - t0)
            if resto > 0:
                time.sleep(resto)

    def _log_gap(self, cur: list[tuple]) -> None:
        """Janela girou inteira: estima a perda pelo buraco de tempo entre a última linha conhecida e a
        mais antiga da janela nova, vezes a taxa recente de negócios."""
        i_dat = self.fields.index("DAT")
        buraco_ms = None
        if self._prev and cur:
            try:
                buraco_ms = parse_dat(str(cur[0][i_dat])) - parse_dat(str(self._prev[-1][i_dat]))
            except ValueError:
                buraco_ms = None
        taxa = len(self._ts_recentes)  # negócios no último segundo
        perda = int(round(buraco_ms / 1000.0 * taxa)) if (buraco_ms is not None and taxa) else None
        logger.warning("GAP: janela girou inteira entre leituras; {} linhas novas, buraco de {} ms, "
                       "perda estimada ~{} negócios (taxa {} neg/s), intervalo máx entre polls {:.0f} ms",
                       len(cur), buraco_ms, perda if perda is not None else "?", taxa, self._poll_max_ms)

    # -- saúde ---------------------------------------------------------------------------------
    def health(self) -> dict:
        return {
            "source": self.source,
            "ativo": self.ativo,
            "ultimo_negocio": self._ultimo_dat,
            "negocios_s": float(len(self._ts_recentes)),
            "gap_pct": (100.0 * self._gap_reads / self._reads) if self._reads else 0.0,
            "n_janela": len(self._prev),
            "profundidade": self.profundidade,
            "latencia_ms": (sum(self._lat) / len(self._lat)) if self._lat else None,
            "latencia_max_ms": self._lat_max,
            "intervalo_poll_max_ms": self._poll_max_ms,
            "leituras": self._reads,
            "leituras_com_gap": self._gap_reads,
            "leituras_instaveis": self._instaveis,
            "linhas_invalidas": self._linhas_invalidas,
            "seq": self._seq,
        }
