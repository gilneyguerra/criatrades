---
name: flow-collector
description: Grava o Times & Trades do mini-índice (janela deslizante T&T0 do RTD do Profit) em data/raw/{ativo}_{YYYY-MM-DD}.parquet. Fase 1. Somente leitura. Não chama a próxima skill.
---

# flow-collector

Lê um `FeedSource` (hoje `RtdSource`; amanhã `ProfitDllSource`, sem mudar este arquivo) e escreve
Parquet com o schema de `schema.py`. Nunca sobrescreve um arquivo existente.

## Pré-condições (Fase 0 já medida em 2026-10-27)
- Profit aberto com uma janela Times & Trades do ativo (vira `T&T0`); RTD habilitado em
  Arquivo > Exportar em Tempo Real > Configurações.
- Planilha `Times and Trades.xlsx` FECHADA (uma exportação por vez).
- Profundidade = N_MAX da Fase 0: **500** nesta máquina. Índice 500 provoca access violation no Profit.

## Rodar
```
uv run python collector.py                       # até o fim da janela do config + 5 min
uv run python collector.py --duracao-s 300       # teste de 5 min
uv run python collector.py --fonte dll           # falha com NotImplementedError até a licença
```

## Saída
- `data/raw/WINZ26_2026-10-27.parquet` (ou `_HHMMSS` se já existir).
- Log a cada 10 s: `hora do último negócio`, `neg/s`, `gap_pct`, `negócios na janela`, `latência`, `source`.

## Linguagem obrigatória (seção 12)
- O dado é **janela deslizante do RTD**, não stream oficial. Se a janela girar inteira entre duas leituras
  de 100 ms, o primeiro negócio da leitura sai com `gap=True` e a perda é desconhecida.
- "Mão" de corretora é fluxo agregado, não posição de um player.
- Nunca "compra"/"venda"/"oportunidade"; nunca P&L.

## Pronto quando
Uma manhã inteira 10:00–10:45 gravada com `gap_pct` reportado, e `pytest tests/test_collector.py` verde
contra a fonte falsa e contra o stub da DLL.
