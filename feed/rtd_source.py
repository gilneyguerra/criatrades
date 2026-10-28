"""Fonte RTD do Profit (Nelogica) — cliente COM local, polling adaptativo, dedup por alinhamento, gap.

Fatos medidos nesta máquina (2026-10-27), ver docs/LESSONS_LEARNED.md:
  - ProgID rtdtrading.rtdserver; LocalServer32 = profitchart.exe (COM out-of-proc, o Profit é o servidor).
  - ConnectData funciona SEM ServerStart. ServerStart(None) causa access violation dentro do Profit;
    ServerStart(objeto Python) dá "Tipo não correspondente". Portanto: NUNCA chamar ServerStart.
  - ConnectData(id, ["T&T0","DAT",i], True) -> (valor, True). RefreshData(n) -> (((ids...),(valores...)), n)
    e devolve SÓ os tópicos alterados desde a última chamada (~2 ms).
  - Tópico/campo inexistente NÃO devolve "Comando Inválido": provoca access violation dentro do profitchart.exe.
    Por isso existe a ALLOWLIST abaixo: RtdClient recusa qualquer tópico fora dela, salvo no probe.
  - N_MAX medido = 500 (índice 500 -> AV). Campo T&T0/AGR/i = "Comprador" | "Vendedor" | "RLP".
  - A janela T&T0 é deslizante (últimos N negócios), não é stream; índice 0 = mais novo.
"""
from __future__ import annotations

import gc
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterator
from zoneinfo import ZoneInfo

import pythoncom
import pywintypes
import win32api
import win32com.client
import win32process
from loguru import logger

from schema import Trade

PROGID = "rtdtrading.rtdserver"
TZ = ZoneInfo("America/Sao_Paulo")
JANELA_TT = "T&T0"
FIELDS = ("DAT", "ACP", "PRE", "QUL", "AVD", "AGR")  # 5 da planilha + AGR (agressor), descoberto na Fase 0
AGRESSOR_MAP = {"comprador": "buy", "vendedor": "sell", "rlp": "rlp"}
_ERR_RE = re.compile(r"inv[aá]lid|erro|error|indispon|n[aã]o dispon|comando", re.I)

INSTRUCAO_FALHA = (
    "Não foi possível assinar T&T0 no RTD do Profit. Verifique, nesta ordem:\n"
    "  1. Feche a planilha Times and Trades.xlsx (só UMA exportação em tempo real por vez).\n"
    "  2. No Profit, abra uma janela Times & Trades do ativo (a primeira vira T&T0).\n"
    "  3. Profit > Arquivo > Exportar em Tempo Real > Configurações: RTD habilitado.\n"
    "  4. Só um Profit aberto; Profit e Python no mesmo nível de permissão (ambos sem 'admin')."
)


# -- allowlist de tópicos ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Allowlist:
    """Único conjunto de tópicos validado em 2026-10-27. Mudar aqui exige mudar tests/test_allowlist.py."""
    janela: str = JANELA_TT
    fields: tuple[str, ...] = FIELDS
    linha_min: int = 0
    linha_max: int = 499          # N_MAX - 1
    info: tuple[tuple[str, str, str], ...] = ((JANELA_TT, "INFO", "ATV"),)

    def permite(self, topic: tuple) -> bool:
        if topic in self.info:
            return True
        if len(topic) != 3 or topic[0] != self.janela or topic[1] not in self.fields:
            return False
        idx = topic[2]
        return isinstance(idx, int) and not isinstance(idx, bool) and self.linha_min <= idx <= self.linha_max

    def assinatura(self) -> str:
        return (f"{self.janela}|{','.join(self.fields)}|{self.linha_min}-{self.linha_max}|"
                + ";".join("/".join(t) for t in self.info))


ALLOWLIST = Allowlist()


class TopicoForaDaAllowlist(RuntimeError):
    pass


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


def percentil(valores: list[float], p: float) -> float:
    if not valores:
        return 0.0
    v = sorted(valores)
    k = min(len(v) - 1, max(0, int(round((p / 100.0) * (len(v) - 1)))))
    return v[k]


# -- cliente COM ---------------------------------------------------------------------------------------
class RtdClient:
    """Camada fina sobre IRtdServer. Mantém o cache de valor por TopicID e aplica a ALLOWLIST."""

    def __init__(self, permitir_fora_da_allowlist: bool = False) -> None:
        self._srv = None
        self._next_id = 1
        self._topics: dict[int, tuple] = {}
        self._values: dict[int, object] = {}
        self.permitir_fora_da_allowlist = permitir_fora_da_allowlist
        self.thread_id: int | None = None

    def open(self) -> None:
        pythoncom.CoInitialize()           # apartment desta thread; o loop de leitura DEVE rodar nela
        self.thread_id = threading.get_ident()
        self._srv = win32com.client.Dispatch(PROGID)

    def connect(self, *topic: object) -> tuple[int, object]:
        if not self.permitir_fora_da_allowlist and not ALLOWLIST.permite(topic):
            raise TopicoForaDaAllowlist(
                f"tópico {topic!r} fora da allowlist {ALLOWLIST.assinatura()!r}: tópico desconhecido provoca "
                "access violation dentro do Profit. Só probe.py --testar-campos pode sondar.")
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

    def refresh(self) -> tuple[int, float, tuple]:
        """Aplica os tópicos alterados ao cache. Retorna (nº alterados, latência ms, ids alterados)."""
        t0 = time.perf_counter()
        raw = self._srv.RefreshData(len(self._topics))
        lat = (time.perf_counter() - t0) * 1000.0
        data, n = (raw if isinstance(raw, tuple) and len(raw) == 2 else (None, 0))
        ids: tuple = ()
        if data:
            ids, vals = data
            values = self._values
            for tid, v in zip(ids, vals):
                values[tid] = v
        return int(n or 0), lat, ids

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


# -- poll adaptativo -----------------------------------------------------------------------------------
class PollAdaptativo:
    """100 ms base; se uma leitura trouxe > burst_trades_por_leitura negócios novos, cai para poll_ms_burst
    até a taxa ficar abaixo de taxa_fim_s por burst_fim_s segundos."""

    def __init__(self, poll_ms: int = 100, poll_ms_burst: int = 40, burst_trades_por_leitura: int = 300,
                 taxa_fim_s: float = 150.0, burst_fim_s: float = 2.0) -> None:
        self.poll_ms = poll_ms
        self.poll_ms_burst = poll_ms_burst
        self.burst_trades_por_leitura = burst_trades_por_leitura
        self.taxa_fim_s = taxa_fim_s
        self.burst_fim_s = burst_fim_s
        self.modo_burst = False
        self._calmo_desde: float | None = None

    def atualizar(self, n_novos: int, taxa_s: float, agora: float) -> int:
        if n_novos > self.burst_trades_por_leitura:
            self.modo_burst = True
            self._calmo_desde = None
        elif self.modo_burst:
            if taxa_s < self.taxa_fim_s:
                if self._calmo_desde is None:
                    self._calmo_desde = agora
                elif agora - self._calmo_desde >= self.burst_fim_s:
                    self.modo_burst = False
                    self._calmo_desde = None
            else:
                self._calmo_desde = None
        return self.poll_ms_burst if self.modo_burst else self.poll_ms


# -- fonte ---------------------------------------------------------------------------------------------
class RtdSource:
    """FeedSource sobre a janela deslizante T&T0.

    Dedup por ALINHAMENTO: a leitura nova deve conter a leitura anterior deslocada de k linhas;
    as k linhas mais novas são negócios novos. Se nenhum deslocamento alinha, a janela girou
    inteira entre duas leituras -> gap=True no primeiro negócio da leitura.

    Loop quente: células pré-alocadas (profundidade × campos); só as linhas cujos tópicos mudaram no
    RefreshData são reconstruídas; GC desligado durante o loop (quem grava chama gc.collect() no flush);
    processo em HIGH_PRIORITY_CLASS; CoInitialize e RefreshData na MESMA thread.
    """

    source = "rtd"

    def __init__(self, ativo: str, profundidade: int = 500, poll_ms: int = 100, poll_ms_burst: int = 40,
                 burst_trades_por_leitura: int = 300, fields: tuple[str, ...] = FIELDS,
                 max_estabiliza: int = 3, alta_prioridade: bool = True) -> None:
        self.ativo = ativo
        self.profundidade = profundidade
        self.fields = fields
        self.max_estabiliza = max_estabiliza
        self.alta_prioridade = alta_prioridade
        self.poll = PollAdaptativo(poll_ms, poll_ms_burst, burst_trades_por_leitura)
        self.client = RtdClient()
        self.newest_first = True
        self.gaps: list[dict] = []
        # buffers do loop quente
        self._cells: list[list[object]] = []
        self._rows: list[tuple | None] = []
        self._tid_pos: dict[int, int] = {}
        self._dirty: set[int] = set()
        self._prev: list[tuple] = []
        # estado / métricas
        self._running = False
        self._seq = 0
        self._reads = 0
        self._gap_reads = 0
        self._instaveis = 0
        self._linhas_invalidas = 0
        self._lat: deque[float] = deque(maxlen=100)
        self._lat_max = 0.0
        self._jitter: deque[float] = deque(maxlen=600)   # intervalo real entre leituras, ms
        self._ultimo_poll: float | None = None
        self._intervalo_ms = poll_ms
        self._ts_recentes: deque[float] = deque()
        self._ultimo_dat: str | None = None
        self._thread_loop: int | None = None

    # -- ciclo de vida -------------------------------------------------------------------------
    def start(self) -> None:
        self.client.open()
        if self.alta_prioridade:
            try:
                win32process.SetPriorityClass(win32api.GetCurrentProcess(), win32process.HIGH_PRIORITY_CLASS)
            except pywintypes.error as e:  # pragma: no cover
                logger.warning("não consegui HIGH_PRIORITY_CLASS: {}", e)
        ok, atv, _ = self.client.try_connect(JANELA_TT, "INFO", "ATV")
        if not ok:
            self.client.close()
            raise RuntimeError(f"{INSTRUCAO_FALHA}\n  Retorno do RTD: {atv!r}")
        if str(atv) != self.ativo:
            logger.warning("T&T0 mostra {} mas o config pede {}; seguindo com o que o Profit exibe",
                           atv, self.ativo)
            self.ativo = str(atv)
        nf = len(self.fields)
        self._cells = [[None] * nf for _ in range(self.profundidade)]
        self._rows = [None] * self.profundidade
        for i in range(self.profundidade):
            for j, f in enumerate(self.fields):
                ok, v, tid = self.client.try_connect(JANELA_TT, f, i)
                if not ok:
                    self.client.close()
                    raise RuntimeError(f"T&T0/{f}/{i} recusado ({v!r}); profundidade máxima menor que "
                                       f"{self.profundidade}. Rode probe.py para medir N_MAX.")
                self._tid_pos[tid] = i * nf + j
                self._cells[i][j] = v
        self._dirty = set(range(self.profundidade))
        self._estabiliza()
        self._reconstruir_sujas()
        self.newest_first = self._detecta_orientacao()
        self._prev = self.snapshot()
        self._running = True
        logger.info("RtdSource ok: ativo={} profundidade={} newest_first={} linhas_validas={} poll={}ms/{}ms",
                    self.ativo, self.profundidade, self.newest_first, len(self._prev),
                    self.poll.poll_ms, self.poll.poll_ms_burst)

    def stop(self) -> None:
        self._running = False
        self.client.close()

    # -- leitura -------------------------------------------------------------------------------
    def _aplicar(self, ids: tuple) -> None:
        cells, nf, pos, values, dirty = self._cells, len(self.fields), self._tid_pos, self.client._values, self._dirty
        for tid in ids:
            p = pos.get(tid)
            if p is None:
                continue
            i, j = divmod(p, nf)
            cells[i][j] = values[tid]
            dirty.add(i)

    def _estabiliza(self) -> float:
        """RefreshData até não haver tópico alterado (leitura coerente). Retorna latência da 1ª chamada."""
        n, lat, ids = self.client.refresh()
        self._aplicar(ids)
        tent = 0
        while n > 0 and tent < self.max_estabiliza:
            n, _, ids = self.client.refresh()
            self._aplicar(ids)
            if n > 0:
                self._instaveis += 1
            tent += 1
        return lat

    @staticmethod
    def _valida(row: tuple) -> bool:
        for v in row:
            if v is None or (isinstance(v, str) and (v.strip() == "" or _ERR_RE.search(v))):
                return False
        return True

    def _reconstruir_sujas(self) -> None:
        rows, cells = self._rows, self._cells
        for i in self._dirty:
            row = tuple(cells[i])
            if self._valida(row):
                rows[i] = row
            else:
                rows[i] = None
                self._linhas_invalidas += 1
        self._dirty.clear()

    def _detecta_orientacao(self) -> bool:
        validas = [r for r in self._rows if r is not None]
        if len(validas) < 2:
            return True
        i_dat = self.fields.index("DAT")
        try:
            return parse_dat(str(validas[0][i_dat])) >= parse_dat(str(validas[-1][i_dat]))
        except ValueError:
            return True

    def snapshot(self) -> list[tuple]:
        """Linhas válidas da janela, do mais antigo para o mais novo (uma lista nova por leitura)."""
        self._reconstruir_sujas()
        rows = self._rows
        if self.newest_first:
            return [r for r in reversed(rows) if r is not None]
        return [r for r in rows if r is not None]

    @staticmethod
    def alinhar(prev: list[tuple], cur: list[tuple]) -> tuple[list[tuple], bool]:
        """Retorna (linhas novas, gap). Procura o menor k tal que cur começa com prev[k:]."""
        if not prev:
            return cur, False
        if not cur:
            return [], False
        lp, lc = len(prev), len(cur)
        c0 = cur[0]
        for k in range(lp):
            if prev[k] != c0:
                continue
            m = lp - k
            if m > lc:
                continue
            j = 1
            while j < m and cur[j] == prev[k + j]:
                j += 1
            if j == m:
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

    def _registrar_gap(self, cur: list[tuple]) -> None:
        """Janela girou inteira: estima a perda pelo buraco de tempo entre a última linha conhecida e a
        mais antiga da janela nova, vezes a taxa recente de negócios."""
        i_dat = self.fields.index("DAT")
        buraco_ms = None
        if self._prev and cur:
            try:
                buraco_ms = parse_dat(str(cur[0][i_dat])) - parse_dat(str(self._prev[-1][i_dat]))
            except ValueError:
                buraco_ms = None
        taxa = len(self._ts_recentes)
        perda = int(round(buraco_ms / 1000.0 * taxa)) if (buraco_ms is not None and taxa) else None
        info = {"hora": str(cur[0][i_dat]) if cur else None, "seq": self._seq + 1, "buraco_ms": buraco_ms,
                "perda_estimada": perda, "taxa_s": taxa, "linhas_novas": len(cur),
                "intervalo_leitura_ms": round(self._jitter[-1], 1) if self._jitter else None}
        self.gaps.append(info)
        logger.warning("GAP: janela girou inteira entre leituras; {} linhas novas, buraco de {} ms, perda "
                       "estimada ~{} negócios (taxa {} neg/s), intervalo desta leitura {} ms",
                       len(cur), buraco_ms, perda if perda is not None else "?", taxa,
                       info["intervalo_leitura_ms"])

    def iter_trades(self) -> Iterator[Trade | None]:
        self._thread_loop = threading.get_ident()
        if self._thread_loop != self.client.thread_id:
            raise RuntimeError("iter_trades() deve rodar na mesma thread que start() (apartment COM)")
        gc_estava_ligado = gc.isenabled()
        gc.disable()
        try:
            while self._running:
                t0 = time.perf_counter()
                if self._ultimo_poll is not None:
                    self._jitter.append((t0 - self._ultimo_poll) * 1000.0)
                self._ultimo_poll = t0
                lat = self._estabiliza()
                self._lat.append(lat)
                if lat > self._lat_max:
                    self._lat_max = lat
                cur = self.snapshot()
                novos, gap = self.alinhar(self._prev, cur)
                self._reads += 1
                if gap:
                    self._gap_reads += 1
                    self._registrar_gap(cur)
                agora = time.monotonic()
                for i, row in enumerate(novos):
                    self._ts_recentes.append(agora)
                    yield self._to_trade(row, gap=(gap and i == 0))
                if not novos:
                    yield None  # heartbeat: leitura sem negócio novo; quem consome checa horário/flush/health
                while self._ts_recentes and agora - self._ts_recentes[0] > 1.0:
                    self._ts_recentes.popleft()
                self._prev = cur
                self._intervalo_ms = self.poll.atualizar(len(novos), float(len(self._ts_recentes)), agora)
                resto = self._intervalo_ms / 1000.0 - (time.perf_counter() - t0)
                if resto > 0:
                    time.sleep(resto)
        finally:
            if gc_estava_ligado:
                gc.enable()

    # -- saúde ---------------------------------------------------------------------------------
    def health(self) -> dict:
        jit = list(self._jitter)
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
            "intervalo_ms_efetivo": self._intervalo_ms,
            "modo_burst": self.poll.modo_burst,
            "jitter_p50_ms": percentil(jit, 50),
            "jitter_p95_ms": percentil(jit, 95),
            "jitter_max_ms": max(jit) if jit else 0.0,
            "leituras": self._reads,
            "leituras_com_gap": self._gap_reads,
            "gaps": list(self.gaps),
            "leituras_instaveis": self._instaveis,
            "linhas_invalidas": self._linhas_invalidas,
            "seq": self._seq,
            "gc_ligado": gc.isenabled(),
        }
