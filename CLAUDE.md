# CriaTrades — Instruções para o Claude Code (VS Code)
Repositório: https://github.com/gilneyguerra/criatrades — a cada criação ou alteração, manter o repo atualizado
(commit + push origin main) ao final de toda interação.
Leitura de fluxo (Times & Trades) do mini-índice no Profit (Nelogica), em Python. Somente leitura.

> **Medições que atualizam esta spec** (detalhes em `docs/LESSONS_LEARNED.md`, que prevalece sobre o texto abaixo):
> N_MAX = 500 linhas nesta máquina (não 598); tópico inexistente provoca access violation dentro do Profit (não
> "Comando Inválido") — nunca sondar nomes em produção; `ServerStart` nunca deve ser chamado; o campo extra
> `T&T0/AGR/i` devolve o agressor ("Comprador"/"Vendedor"/"RLP"), já incorporado ao schema; `LIV0` não respondeu.

## 0. Regras de ouro (nunca violar)
- Nunca chamar `ServerStart`. `ConnectData` direto.
- Nunca assinar tópico fora da allowlist validada: `T&T0 × {DAT, ACP, PRE, QUL, AVD, AGR} × 0..499` e
  `("T&T0","INFO","ATV")`. A allowlist vive em `feed/rtd_source.py` (`ALLOWLIST`) e `RtdClient` recusa qualquer
  outro tópico; `tests/test_allowlist.py` falha se a assinatura mudar sem atualizar o teste.
- Sondagem de nomes só com `probe.py --testar-campos` (único lugar com `permitir_fora_da_allowlist=True`),
  nunca no collector.
- `aggressor` é `buy | sell | rlp | None` ("Comprador" → buy, "Vendedor" → sell, "RLP" → rlp).
  `buy_agent_id`/`sell_agent_id` ficam `None` (reservados para a DLL).

## 1. Contexto
- Fonte atual: servidor RTD do Profit (Nelogica), ProgID `rtdtrading.rtdserver`. É COM local do Windows,
  não API de rede: o Python instancia o mesmo servidor que o Excel usa (ConnectData por tópico →
  RefreshData em loop). Já incluso no Profit do usuário, sem custo extra. Sem Excel no caminho.
- Tópicos conhecidos da planilha `Times and Trades.xlsx` (assinatura EXATA — parâmetro a mais dá erro):
  ("T&T0","DAT",i) hora com ms · ("T&T0","ACP",i) corretora compradora · ("T&T0","PRE",i) preço ·
  ("T&T0","QUL",i) quantidade · ("T&T0","AVD",i) corretora vendedora · ("T&T0","INFO","ATV") ativo ·
  ("T&T0","AGR",i) agressor (descoberto na Fase 0).
- Restrições documentadas: só UMA exportação em tempo real por vez (coletor e planilha Excel não coexistem;
  fechar a planilha antes de rodar); se a janela T&T0 for fechada no Profit, os tópicos retornam erro;
  RTD pode estar desligado em Exportar em Tempo Real > Configurações.
- A janela é deslizante (últimos N negócios) — NÃO é stream. WINZ26, ~300 negócios/s na média, muito mais
  no pico 10:00–10:45 America/Sao_Paulo. Os campos não trazem ID de agente.
- Fonte futura: ProfitDLL (Nelogica, produto pago "DLL Real Time", contratar via corporativo@nelogica.com.br).
  DLL nativa Windows stdcall, 64 bits, carregar com ctypes.WinDLL, init `DLLInitializeMarketLogin` (só market
  data), dados por callback em thread interna (padrão produtor-consumidor obrigatório, callback só enfileira).
- MetaTrader 5 descartado: tem agressor mas não expõe corretora. Feeds delayed (brapi, yfinance) descartados.
- Saldo líquido por corretora é proxy de fluxo agregado, não inventário de um player — manter isso explícito.
- Read-only: nunca instanciar objeto de ordem/negociação, nunca usar DLLInitializeLogin (roteamento), nunca ler
  credenciais do chat. Dado licenciado para uso pessoal: roda só nesta máquina, sem redistribuição.
- Método organizacional vindo do repo MIT Jakeschincariol/day-trader-skill (trazer o MÉTODO, não o código):
  skills encadeadas por arquivo, backtest com veredito honesto e seed, testes offline com fixtures reais,
  sinais com máquina de estados, regras de linguagem.

## 2. Stack
Python 3.12 (Windows), pywin32, polars, pyarrow, duckdb, pydantic, loguru, fastapi + uvicorn (painel).
Gerenciar com uv. Testes com pytest (`uv run pytest`).

## 3. Estrutura
```
feed/base.py            FeedSource(Protocol): start(), stop(), iter_trades() -> Iterator[Trade]; health() -> dict
feed/rtd_source.py      RtdSource: cliente COM, polling 100 ms, dedup por alinhamento, detecção de gap
feed/profitdll_source.py ProfitDllSource: STUB com a assinatura final, NotImplementedError até a licença
schema.py               Trade(ts_ms, price, qty, buyer, seller, buy_agent_id, sell_agent_id, aggressor, seq, gap, source)
scheduler.py            09:55–10:50 America/Sao_Paulo, dias úteis B3
config_loader.py        config.json (raiz) ou .claude/skills/flow-config/config.example.json
probe.py                FASE 0
collector.py            FASE 1 — recebe um FeedSource, escreve Parquet; não sabe qual fonte é
book.py                 FASE 2 — verificação do agressor via LIV0 (só quando source == "rtd")
metrics.py              FASE 3
signal.py               FASE 3/4 — máquina de estados dos sinais
replay.py               FASE 5
dashboard/              FASE 4 — FastAPI + WebSocket + HTML, local, somente leitura
.claude/skills/flow-{collector,metrics,signal,replay,config}/
tests/                  determinísticos, offline; fixtures/winz26_<primeira-manhã>.parquet
```
Regras: cada skill lê o que a anterior escreveu e NUNCA chama a próxima por conta própria.
metrics, signal, replay e dashboard só leem data/raw/*.parquet; trocar RTD por DLL muda um arquivo.

## 4. Config
Ver `.claude/skills/flow-config/config.example.json` (ativo, janela, tz, gap_max_pct, janelas de métricas,
burst, percentil de lote, horizontes/delta/seed do replay, min_sessoes_replay, pontos_por_contrato_brl).

## 5. FASE 0 — probe.py (feita em 2026-10-27 fora do pico; repetir em 10:00–10:45)
1. Exportação ativa / RTD habilitado. 2. N_MAX. 3. Campos extras (só com `--testar-campos`). 4. Gravar N s em
CSV com o schema completo e imprimir total, pico/s, gap_pct, latência, N_MAX. 5. Decisão: gap_pct no pico >
gap_max_pct → recomendar ProfitDLL.

## 6. FASE 1 — collector.py
RtdSource com profundidade = N_MAX, poll 100 ms, dedup por alinhamento, `seq` próprio, gap quando a janela gira
inteira, DAT → ms em America/Sao_Paulo, Parquet a cada 5 s em data/raw/{ativo}_{YYYY-MM-DD}.parquet sem
sobrescrever, health log a cada 10 s. Pronto quando: uma manhã inteira gravada e o mesmo collector rodando sem
alteração contra ProfitDllSource (stub) nos testes.

## 6b. Jitter no pico (feed/rtd_source.py + collector.py)
- Loop quente sem realocação: células pré-alocadas 500×6, só linhas com tópico alterado são reconstruídas.
- `gc.disable()` durante o loop; `gc.collect()` explícito só no flush de 5 s do collector.
- Processo em `HIGH_PRIORITY_CLASS`; `CoInitialize` e `RefreshData` na mesma thread (checado em runtime).
- Poll adaptativo: `poll_ms_base` (100); se uma leitura trouxe > `burst_trades_por_leitura` (300) negócios novos,
  cai para `poll_ms_burst` (40) até a taxa ficar < 150/s por 2 s. Intervalo efetivo vai ao health log.
- Jitter medido (p50/p95/max do intervalo real entre leituras), não só a latência do RefreshData.
- Collector: health log com série de negócios/s, p95 de jitter, nº de gaps e hora do último gap; ao fim,
  `data/raw/{ativo}_{dia}.summary.json` (total, pico/s, gaps com hora + perda estimada, distribuição
  buy/sell/rlp, N_MAX usado, jitter, negócios por segundo).

## 7. FASE 2 — reduzida a verificação (verify_aggressor.py)
O RTD já entrega AGR. `verify_aggressor.py` amostra 200 negócios de data/raw (seed fixo) e imprime a tabela para
conferência manual contra as cores do Profit; registrar a concordância em `docs/LESSONS_LEARNED.md`. Critério:
≥ 95 %. `book.py`/LIV0 sai do caminho crítico.

## 8. FASE 3 — metrics.py (janelas 30 s / 60 s / 300 s / sessão, por corretora) — NÃO implementar ainda
Volume comprado/vendido, saldo líquido ("mão"), VWAP de compra e venda, negócios/s, lote médio, % lotes ≥ p95,
burst_score, absorção, imbalance agressor, ranking "quem domina o fluxo agora", reversão de saldo.
Regras já decididas:
- Negócios `rlp` ficam FORA de burst_score, absorção e imbalance (não consomem liquidez, não movem preço).
- Métrica própria `rlp_net` por corretora (compra − venda via RLP) em janelas móveis (`rlp_separado` no config).
- Um sinal cuja janela de `gap_block_window_s` (10 s) contenha qualquer gap nasce BLOQUEADO.
Pronto quando: replay de um dia gravado reproduz os rankings esperados (teste sobre fixture).

## 9. Máquina de estados de cada sinal (signal.py)
COLETANDO | AGUARDANDO (distância do gatilho) | DISPAROU (hora ms + preço + corretora) | EXPIROU |
BLOQUEADO (gap_pct > gap_max_pct). Um disparo por (regra, corretora, manhã); nunca disparar em janela incompleta;
alertas tocam uma vez (rang_once).

## 10. FASE 4 — dashboard/
FastAPI + WebSocket ~500 ms: ranking por corretora, saldo/VWAP, burst, absorção, alertas com estado. Só leitura.
Pronto quando: rodou ao vivo 10:00–10:45 por 5 pregões.

## 11. FASE 5 — replay.py
Cada regra em cada manhã gravada; Δpreço em 1/3/5 min → a_favor / contra / nada. Bootstrap de blocos de 60 s
10.000× com seed fixo (não OHLC). Veredito por faixa fixa: coin flip (< 5 pts de 50 %), leans (5–15), strongly
(≥ 15). Sempre imprimir seed e nº de sessões; recusar com < min_sessoes_replay. R (R$ 0,20/ponto) só em relatório.

## 12. Regras de linguagem em TODO SKILL.md e em toda saída
- "Disparou uma regra", nunca "compra", "venda" ou "oportunidade". Nunca P&L, lucro ou retorno.
- Toda saída traz: hora do último negócio lido, gap_pct, nº de negócios na janela, source (rtd/dll).
- Nunca arredondar um coin flip para "edge". Se histórico e bootstrap discordam, dizer.
- O dado é janela deslizante do RTD, não stream oficial. "Mão" de corretora é fluxo agregado, não posição.

## 13. Não fazer
openpyxl/xlwings em tempo real; scraping de tela; métricas/sinais antes das Fases 0 e 1 íntegras; copiar
scanner/news/broker/budget/pelosi do repo de referência; MetaTrader 5 ou feeds delayed como fonte.

## 14. Ordem de execução
Fase 0 → Fase 1 → cortar fixture + testes → Fase 2 (verificação) → Fase 3 → Fase 4 → Fase 5.
Upgrade futuro: ProfitDLL — cotar depois que o MVP provar valor; a troca é só implementar ProfitDllSource.

## 15. Primeira manhã de pregão (checklist)
1. Planilha Excel fechada. Profit aberto com T&T0 no WINZ26.
2. 09:55 → `uv run python collector.py` (não rodar probe no horário nobre).
3. 10:50 → ler o `summary.json`: pico/s, gaps no intervalo 10:00–10:45, p95 de jitter.
4. Fora do pico: aumentar linhas da janela T&T no Profit, rodar `probe.py` para novo N_MAX; se > 500, atualizar
   `Allowlist.linha_max` e `tests/test_allowlist.py`.
5. Cortar fixture de 5 min (preferir um trecho com burst) e só então abrir a Fase 3.
Critério para cotar ProfitDLL: qualquer gap dentro de 10:00–10:45 após as mitigações de jitter e janela maior.
