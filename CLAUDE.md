# CriaTrades — Instruções para o Claude Code (VS Code)
Repositório: https://github.com/gilneyguerra/criatrades — a cada criação ou alteração, manter o repo atualizado
(commit + push origin main) ao final de toda interação.
Leitura de fluxo (Times & Trades) do mini-índice no Profit (Nelogica), em Python. Somente leitura.

> **Medições que atualizam esta spec** (detalhes em `docs/LESSONS_LEARNED.md`, que prevalece sobre o texto abaixo):
> N_MAX = 500 linhas nesta máquina (não 598); tópico inexistente provoca access violation dentro do Profit (não
> "Comando Inválido") — nunca sondar nomes em produção; `ServerStart` nunca deve ser chamado; o campo extra
> `T&T0/AGR/i` devolve o agressor ("Comprador"/"Vendedor"), já incorporado ao schema; `LIV0` não respondeu.

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

## 7. FASE 2 — book.py (verificação do agressor)
O RTD já entrega AGR. Se LIV0 vier a responder: preço == ask → comprador; preço == bid → vendedor. Pronto quando
≥ 95 % de concordância com AGR / cores do Profit em 200 negócios.

## 8. FASE 3 — metrics.py (janelas 30 s / 60 s / 300 s / sessão, por corretora)
Volume comprado/vendido, saldo líquido ("mão"), VWAP de compra e venda, negócios/s, lote médio, % lotes ≥ p95,
burst_score, absorção, imbalance agressor, ranking "quem domina o fluxo agora", reversão de saldo.
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
Fase 0 → Fase 1 → cortar fixture + testes → Fase 2 → Fase 3 → Fase 4 → Fase 5.
Upgrade futuro: ProfitDLL — cotar depois que o MVP provar valor; a troca é só implementar ProfitDllSource.
