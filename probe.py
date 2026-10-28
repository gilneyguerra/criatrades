"""FASE 0 — sonda do RTD do Profit. Decide se o RTD basta ou se vale cotar o ProfitDLL.

Uso:  uv run python probe.py [--segundos 300] [--busca-max 3000] [--saida data/probe]

Passos: (1) exportação ativa / RTD habilitado  (2) N_MAX de linhas do T&T0
        (3) campos extras em T&T0 e campos do LIV0  (4) gravação em CSV com schema completo
        (5) critério de decisão impresso no fim.
Somente leitura. Nunca chama ServerStart (ver feed/rtd_source.py).
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from loguru import logger

from config_loader import load_config
from feed.rtd_source import ALLOWLIST, JANELA_TT, TZ, RtdClient, RtdSource
from schema import CSV_COLUMNS

# Candidatos a campos extras. Os 5 oficiais (DAT ACP PRE QUL AVD) já são conhecidos.
CAMPOS_EXTRA_TT = ["AGR", "SEQ", "HOR", "QTD", "VOL", "AGC", "AGV", "TIP", "COM", "VEN",
                   "AGRESSOR", "ID", "NEG", "COR", "CPR", "VND", "DATA", "ULT"]
CAMPOS_INFO_TT = ["ATV", "HOR", "DAT", "ULT", "NEG", "VOL", "QTD", "ABE", "MAX", "MIN", "FEC"]
JANELA_LIV = "LIV0"
CAMPOS_LIV = ["PRE", "QUL", "QTD", "ACP", "AVD", "COR", "CPR", "VPR", "CQT", "VQT",
              "BID", "ASK", "PRC", "QTC", "PRV", "QTV", "COMPRA", "VENDA", "PCO", "PVE",
              "QCO", "QVE", "CCO", "CVE", "COC", "COV"]
CAMPOS_INFO_LIV = ["ATV", "BID", "ASK", "MCP", "MVD", "ULT"]


def excel_aberto() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq EXCEL.EXE"], capture_output=True,
                             timeout=10).stdout  # bytes: o tasklist escreve na code page OEM, não em UTF-8
        return b"EXCEL.EXE" in out.upper()
    except Exception:  # noqa: BLE001
        return False


def passo1_exportacao(client: RtdClient) -> str:
    print("\n== PASSO 1: exportação ativa / RTD habilitado ==")
    if excel_aberto():
        print("  AVISO: EXCEL.EXE está aberto. Se a planilha Times and Trades.xlsx estiver nela,")
        print("         feche-a: só UMA exportação em tempo real por vez.")
    ok, atv, _ = client.try_connect(JANELA_TT, "INFO", "ATV")
    if not ok:
        from feed.rtd_source import INSTRUCAO_FALHA
        print(INSTRUCAO_FALHA)
        print(f"  Retorno do RTD: {atv!r}")
        sys.exit(2)
    print(f"  T&T0 responde. Ativo exibido: {atv}")
    return str(atv)


def passo2_nmax(client: RtdClient, busca_max: int) -> tuple[int, dict]:
    print("\n== PASSO 2: índice máximo de linha do T&T0 ==")
    respostas: dict[int, object] = {}

    def ok_em(i: int) -> bool:
        ok, v, tid = client.try_connect(JANELA_TT, "DAT", i)
        respostas[i] = v
        if ok and tid is not None:
            client.disconnect(tid)
        return ok

    if not ok_em(0):
        print("  linha 0 falhou — RTD sem dados de T&T0.")
        return 0, respostas
    ultimo_ok, primeiro_falha = 0, None
    i = 100
    while i <= busca_max:
        if ok_em(i):
            ultimo_ok = i
            print(f"  linha {i:5d}: ok  ({respostas[i]!r})")
            i += 100
        else:
            primeiro_falha = i
            print(f"  linha {i:5d}: FALHA ({respostas[i]!r})")
            break
    if primeiro_falha is None:
        print(f"  Até {busca_max} ainda responde; N_MAX >= {busca_max + 1} (aumente --busca-max).")
        return busca_max + 1, respostas
    lo, hi = ultimo_ok, primeiro_falha
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ok_em(mid):
            lo = mid
        else:
            hi = mid
    n_max = lo + 1
    print(f"  N_MAX = {n_max} linhas (índices 0..{lo}); índice {hi} retorna {respostas[hi]!r}")
    return n_max, respostas


def _testa(client: RtdClient, titulo: str, janela: str, campos: list[str], idx: object | None) -> dict:
    print(f"\n  -- {titulo}")
    res = {}
    for c in campos:
        topic = (janela, c) + ((idx,) if idx is not None else ())
        ok, v, tid = client.try_connect(*topic)
        if ok and tid is not None:
            client.disconnect(tid)
        res[c] = {"ok": ok, "valor": None if not ok else (v if isinstance(v, (int, float, str)) else repr(v)),
                  "erro": None if ok else str(v)}
        print(f"     {'/'.join(map(str, topic)):28s} {'OK   ' if ok else 'não  '} {v!r}")
    return res


def passo3_campos(client: RtdClient) -> dict:
    print("\n== PASSO 3: campos extras em T&T0 e campos do LIV0 ==")
    return {
        "tt_extra_linha0": _testa(client, "T&T0 campos extras, linha 0", JANELA_TT, CAMPOS_EXTRA_TT, 0),
        "tt_info": _info(client, JANELA_TT, CAMPOS_INFO_TT),
        "liv_linha0": _testa(client, "LIV0 campos, linha 0", JANELA_LIV, CAMPOS_LIV, 0),
        "liv_info": _info(client, JANELA_LIV, CAMPOS_INFO_LIV),
    }


def _info(client: RtdClient, janela: str, campos: list[str]) -> dict:
    print(f"\n  -- {janela} INFO/<campo>")
    res = {}
    for c in campos:
        ok, v, tid = client.try_connect(janela, "INFO", c)
        if ok and tid is not None:
            client.disconnect(tid)
        res[c] = {"ok": ok, "valor": v if ok else None, "erro": None if ok else str(v)}
        print(f"     {janela}/INFO/{c:8s} {'OK   ' if ok else 'não  '} {v!r}")
    return res


def _passo3_minimo(client: RtdClient) -> dict:
    print("\n== PASSO 3: campos extras (modo mínimo: só confirma AGR; use --testar-campos para sondar) ==")
    return {"tt_extra_linha0": _testa(client, "T&T0 AGR, linha 0", JANELA_TT, ["AGR"], 0)}


def passo4_gravar(ativo: str, n_max: int, segundos: int, saida: Path, cfg) -> dict:
    print(f"\n== PASSO 4: gravando {segundos}s de T&T0 em CSV (profundidade {n_max}) ==")
    src = RtdSource(ativo=ativo, profundidade=min(n_max, ALLOWLIST.linha_max + 1), poll_ms=cfg.poll_ms_base,
                    poll_ms_burst=cfg.poll_ms_burst, burst_trades_por_leitura=cfg.burst_trades_por_leitura)
    if n_max > ALLOWLIST.linha_max + 1:
        print(f"  N_MAX medido {n_max} > allowlist {ALLOWLIST.linha_max + 1}: gravando com a allowlist. Para usar"
              " tudo, atualize Allowlist.linha_max em feed/rtd_source.py e tests/test_allowlist.py.")
    t_start = time.perf_counter()
    src.start()
    t_conn = time.perf_counter() - t_start
    print(f"  {n_max * len(src.fields) + 1} tópicos assinados em {t_conn:.1f}s; newest_first={src.newest_first}")

    saida.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(TZ).strftime("%Y-%m-%d_%H%M%S")
    csv_path = saida / f"probe_{src.ativo}_{stamp}.csv"
    por_segundo: Counter[int] = Counter()
    agressores: Counter[str] = Counter()
    total = 0
    exemplo = None
    fim = time.monotonic() + segundos
    prox_log = time.monotonic() + 10
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        try:
            for tr in src.iter_trades():
                if tr is not None:  # None = heartbeat sem negócio novo
                    w.writerow(tr.as_row())
                    por_segundo[tr.ts_ms // 1000] += 1
                    agressores[str(tr.aggressor)] += 1
                    total += 1
                    exemplo = exemplo or tr
                agora = time.monotonic()
                if agora >= prox_log:
                    h = src.health()
                    print(f"  [{datetime.now(TZ):%H:%M:%S}] negócios={total} último={h['ultimo_negocio']} "
                          f"neg/s={h['negocios_s']:.0f} gap_pct={h['gap_pct']:.2f} "
                          f"janela={h['n_janela']} lat={h['latencia_ms']:.1f}ms src={h['source']}")
                    prox_log = agora + 10
                if agora >= fim:
                    break
        except KeyboardInterrupt:
            print("  interrompido pelo usuário")
        finally:
            h = src.health()
            src.stop()
    if exemplo:
        print(f"  exemplo de linha: {exemplo.as_row()}")
    pico = max(por_segundo.values()) if por_segundo else 0
    h |= {"total_negocios": total, "pico_negocios_s": pico, "csv": str(csv_path),
          "agressores": dict(agressores),
          "segundos": segundos, "tempo_assinatura_s": t_conn}
    return h


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--segundos", type=int, default=300)
    ap.add_argument("--busca-max", type=int, default=3000)
    ap.add_argument("--saida", default="data/probe")
    ap.add_argument("--config", default=None)
    ap.add_argument("--testar-campos", action="store_true",
                    help="passo 3: sonda nomes de campos extras/LIV0. CADA nome inexistente provoca um access "
                         "violation dentro do Profit (capturado, mas evitar). Resultado de 2026-10-27 já está em "
                         "docs/LESSONS_LEARNED.md: só AGR respondeu.")
    args = ap.parse_args()
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    cfg = load_config(args.config)
    agora = datetime.now(TZ)
    print(f"CriaTrades FASE 0 — probe RTD — {agora:%Y-%m-%d %H:%M:%S} {cfg.tz} — source=rtd")
    j0, j1 = cfg.janela
    if not (j0 <= agora.strftime("%H:%M") < j1):
        print(f"  AVISO: fora da janela {j0}-{j1}. O pico de 10:00-10:45 é mais intenso; "
              "o gap_pct medido agora SUBESTIMA o gap do pico. Repetir dentro da janela.")

    # o probe é o ÚNICO lugar autorizado a sair da allowlist (medir N_MAX, sondar campos)
    client = RtdClient(permitir_fora_da_allowlist=True)
    client.open()
    try:
        ativo = passo1_exportacao(client)
        n_max, _ = passo2_nmax(client, args.busca_max)
        campos = passo3_campos(client) if args.testar_campos else _passo3_minimo(client)
    finally:
        client.close()
    if n_max == 0:
        sys.exit(2)

    h = passo4_gravar(ativo, n_max, args.segundos, Path(args.saida), cfg)

    print("\n== RESUMO ==")
    print(f"  ativo={h['ativo']}  source={h['source']}  N_MAX={n_max}")
    print(f"  total de negócios: {h['total_negocios']}  em {h['segundos']}s")
    print(f"  pico de negócios/s: {h['pico_negocios_s']}")
    print(f"  leituras: {h['leituras']}  com gap: {h['leituras_com_gap']}  gap_pct={h['gap_pct']:.2f}%")
    print(f"  leituras instáveis (tópicos ainda mudando no 2º RefreshData): {h['leituras_instaveis']}")
    print(f"  latência média do RefreshData: {h['latencia_ms']:.2f} ms  (máx {h['latencia_max_ms']:.1f} ms)")
    print(f"  jitter do intervalo entre leituras: p50={h['jitter_p50_ms']:.0f} ms p95={h['jitter_p95_ms']:.0f} ms "
          f"máx={h['jitter_max_ms']:.0f} ms; poll efetivo no fim={h['intervalo_ms_efetivo']} ms")
    for g in h['gaps']:
        print(f"  gap em {g['hora']}: buraco {g['buraco_ms']} ms, perda estimada ~{g['perda_estimada']} negócios, "
              f"intervalo da leitura {g['intervalo_leitura_ms']} ms")
    print(f"  segundos de histórico que a janela guarda no pico: {n_max / max(h['pico_negocios_s'], 1):.2f} s "
          "(um travamento maior que isso = gap)")
    print(f"  hora do último negócio lido: {h['ultimo_negocio']}  negócios na janela: {h['n_janela']}")
    print(f"  agressor (campo AGR): {h['agressores']}")
    print(f"  CSV: {h['csv']}")

    print("\n== DECISÃO ==")
    if h["gap_pct"] > cfg.gap_max_pct:
        print(f"  gap_pct {h['gap_pct']:.2f}% > gap_max_pct {cfg.gap_max_pct}% -> RECOMENDAR ProfitDLL "
              "(corporativo@nelogica.com.br).")
    else:
        print(f"  gap_pct {h['gap_pct']:.2f}% <= gap_max_pct {cfg.gap_max_pct}% -> RTD basta para esta amostra.")
    margem = h["pico_negocios_s"] / max(n_max, 1)
    print(f"  pico/N_MAX = {margem:.2f} (fração da janela que gira em 1 s no pico; > 0.5 é zona de risco "
          f"com poll de 100 ms se houver travamento).")
    if not (j0 <= agora.strftime("%H:%M") < j1):
        print("  Esta decisão vale para a amostra fora do pico; confirmar em 10:00-10:45.")

    rel = Path(args.saida) / f"probe_{h['ativo']}_{agora:%Y-%m-%d_%H%M%S}.json"
    rel.write_text(json.dumps({"quando": agora.isoformat(), "n_max": n_max, "campos": campos,
                               "gravacao": h, "config": cfg.model_dump()},
                              ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"  relatório: {rel}")


if __name__ == "__main__":
    main()
