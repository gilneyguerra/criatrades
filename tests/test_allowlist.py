"""Sentinela: a allowlist de tópicos RTD só muda junto com este teste (tópico desconhecido = AV no Profit)."""
import pytest

from feed.rtd_source import ALLOWLIST, RtdClient, TopicoForaDaAllowlist

ASSINATURA_VALIDADA_2026_10_27 = "T&T0|DAT,ACP,PRE,QUL,AVD,AGR|0-499|T&T0/INFO/ATV"


def test_assinatura_da_allowlist_nao_mudou():
    # Se este teste falhar, você mudou a allowlist. Só faça isso depois de medir N_MAX / validar o campo com
    # probe.py --testar-campos, registrar em docs/LESSONS_LEARNED.md e atualizar a constante acima.
    assert ALLOWLIST.assinatura() == ASSINATURA_VALIDADA_2026_10_27


@pytest.mark.parametrize("topic", [
    ("T&T0", "DAT", 0), ("T&T0", "AGR", 499), ("T&T0", "QUL", 250), ("T&T0", "INFO", "ATV"),
])
def test_permitidos(topic):
    assert ALLOWLIST.permite(topic)


@pytest.mark.parametrize("topic", [
    ("T&T0", "DAT", 500),          # além do N_MAX medido
    ("T&T0", "DAT", -1),
    ("T&T0", "SEQ", 0),            # campo que provocou AV
    ("T&T0", "DAT", "0"),          # índice como string nunca foi validado
    ("T&T0", "DAT", True),
    ("T&T1", "DAT", 0),            # outra janela
    ("LIV0", "PRE", 0),
    ("T&T0", "INFO", "HOR"),
    ("T&T0", "DAT"),
    ("T&T0", "DAT", 0, 1),
])
def test_recusados(topic):
    assert not ALLOWLIST.permite(topic)


def test_client_recusa_antes_de_tocar_no_com():
    c = RtdClient()  # sem open(): se chegasse ao COM, daria AttributeError, não TopicoForaDaAllowlist
    with pytest.raises(TopicoForaDaAllowlist):
        c.connect("T&T0", "SEQ", 0)
    with pytest.raises(TopicoForaDaAllowlist):
        c.connect("T&T0", "DAT", 500)
    assert c._topics == {}


def test_probe_pode_sondar_explicitamente():
    c = RtdClient(permitir_fora_da_allowlist=True)
    with pytest.raises(AttributeError):  # passou da allowlist e tentou o COM (não aberto neste teste)
        c.connect("T&T0", "DAT", 500)
