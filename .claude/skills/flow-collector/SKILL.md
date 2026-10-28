---
name: flow-collector
description: Grava o Times & Trades do mini-índice (janela deslizante T&T0 do RTD do Profit) em data/raw/{ativo}_{YYYY-MM-DD}.parquet + .summary.json. Fase 1. Somente leitura. Não chama a próxima skill.
---

# flow-collector

Lê um `FeedSource` (hoje `RtdSource`; amanhã `ProfitDllSource`, sem mudar este arquivo) e escreve
Parquet com o schema de `schema.py`. Nunca sobrescreve um arquivo existente.

## Pré-condições (Fase 0 já medida em 2026-10-27)
- Profit aberto com uma janela Times & Trades do ativo (vira `T&T0`); RTD habilitado em
  Arquivo > Exportar em Tempo Real > Configurações.
- Planilha `Times and Trades.xlsx` FECHADA (uma exportação por vez).
- Profundidade = N_MAX da Fase 0: **500** nesta máquina. A allowlist em `feed/rtd_source.py` só deixa assinar
  `T&T0 × {DAT, ACP, PRE, QUL, AVD, AGR} × 0..499` + `INFO/ATV`; qualquer outro tópico = access violation no Profit.

## Rodar
```
uv run python collector.py                       # até o fim da janela do config + 5 min
uv run python collector.py --duracao-s 300       # teste de 5 min
uv run python collector.py --fonte dll           # falha com NotImplementedError até a licença
```

## Como lida com o pico
- Poll adaptativo: 100 ms base; se uma leitura trouxe > 300 negócios novos, cai para 40 ms até a taxa ficar
  abaixo de 150/s por 2 s (`poll_ms_base`, `poll_ms_burst`, `burst_trades_por_leitura` no config).
- Loop quente sem realocação, GC desligado durante a leitura (coleta explícita só no flush de 5 s),
  processo em prioridade alta, COM na mesma thread do loop.
- Jitter medido (p50/p95/máx do intervalo real entre leituras) e logado; cada gap registra hora, buraco em ms,
  perda estimada e o intervalo da leitura em que ocorreu.

## Saída
- `data/raw/WINZ26_2026-10-28.parquet` (ou `_HHMMSS` se já existir).
- `data/raw/WINZ26_2026-10-28.summary.json`: total, pico/s e hora, gaps (hora + perda estimada), distribuição
  buy/sell/rlp, N_MAX usado, jitter p50/p95/máx, latência, negócios por segundo.
- Log a cada 10 s: `hora do último negócio`, `neg/s`, série dos últimos 10 s, `gap_pct`, nº de gaps e hora do
  último, `negócios na janela`, latência, jitter p95/máx, poll efetivo (e `BURST` quando ativo), `source`.

## Linguagem obrigatória (seção 12 do CLAUDE.md)
- O dado é **janela deslizante do RTD**, não stream oficial. Se a janela girar inteira entre duas leituras,
  o primeiro negócio da leitura sai com `gap=True` e a perda é só estimada.
- "Mão" de corretora é fluxo agregado, não posição de um player.
- `rlp` é negócio contra o provedor de liquidez da corretora: não consome liquidez nem move preço.
- Nunca "compra"/"venda"/"oportunidade"; nunca P&L.

## Pronto quando
Uma manhã inteira 10:00–10:45 gravada com `summary.json` lido (gaps, p95 de jitter), e `pytest` verde contra a
fonte falsa e contra o stub da DLL.
