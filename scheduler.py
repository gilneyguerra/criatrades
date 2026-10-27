"""Janela operacional: 09:55–10:50 America/Sao_Paulo em dias úteis da B3 (calendário B3, não NYSE)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Sao_Paulo")
INICIO = time(9, 55)
FIM = time(10, 50)


def _pascoa(ano: int) -> date:
    """Algoritmo de Meeus/Jones/Butcher (calendário gregoriano)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def feriados_b3(ano: int) -> set[date]:
    p = _pascoa(ano)
    fixos = {
        date(ano, 1, 1), date(ano, 4, 21), date(ano, 5, 1), date(ano, 9, 7),
        date(ano, 10, 12), date(ano, 11, 2), date(ano, 11, 15), date(ano, 11, 20),
        date(ano, 12, 24), date(ano, 12, 25), date(ano, 12, 31),
    }
    moveis = {p - timedelta(days=48), p - timedelta(days=47),  # carnaval seg/ter
              p - timedelta(days=2),                           # sexta-feira santa
              p + timedelta(days=60)}                          # corpus christi
    return fixos | moveis


def dia_util_b3(d: date) -> bool:
    return d.weekday() < 5 and d not in feriados_b3(d.year)


def dentro_da_janela(agora: datetime | None = None,
                     inicio: time = INICIO, fim: time = FIM) -> bool:
    agora = agora.astimezone(TZ) if agora else datetime.now(TZ)
    return dia_util_b3(agora.date()) and inicio <= agora.time() < fim


def proxima_abertura(agora: datetime | None = None, inicio: time = INICIO) -> datetime:
    agora = agora.astimezone(TZ) if agora else datetime.now(TZ)
    d = agora.date()
    if agora.time() >= inicio or not dia_util_b3(d):
        d += timedelta(days=1)
    while not dia_util_b3(d):
        d += timedelta(days=1)
    return datetime.combine(d, inicio, tzinfo=TZ)
