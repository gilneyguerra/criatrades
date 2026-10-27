from datetime import date, datetime

from scheduler import TZ, dentro_da_janela, dia_util_b3, feriados_b3, proxima_abertura


def test_feriados_moveis_2026():
    f = feriados_b3(2026)
    assert date(2026, 2, 16) in f and date(2026, 2, 17) in f   # carnaval
    assert date(2026, 4, 3) in f                                # sexta-feira santa
    assert date(2026, 6, 4) in f                                # corpus christi
    assert date(2026, 11, 20) in f                              # consciência negra (B3 desde 2024)


def test_dia_util():
    assert dia_util_b3(date(2026, 10, 27))          # terça
    assert not dia_util_b3(date(2026, 10, 25))      # domingo
    assert not dia_util_b3(date(2026, 11, 2))       # finados


def test_janela():
    assert dentro_da_janela(datetime(2026, 10, 27, 10, 0, tzinfo=TZ))
    assert not dentro_da_janela(datetime(2026, 10, 27, 10, 50, tzinfo=TZ))
    assert not dentro_da_janela(datetime(2026, 10, 25, 10, 0, tzinfo=TZ))


def test_proxima_abertura_pula_fim_de_semana_e_feriado():
    sexta_tarde = datetime(2026, 10, 30, 15, 0, tzinfo=TZ)
    assert proxima_abertura(sexta_tarde) == datetime(2026, 11, 3, 9, 55, tzinfo=TZ)  # 2/11 é feriado
