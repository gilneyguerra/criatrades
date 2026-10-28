# CriaTrades

Leitura de fluxo (Times & Trades) do mini-índice no Profit (Nelogica), em Python. **Somente leitura.**
Fonte atual: servidor RTD do Profit (COM local, o mesmo que o Excel usa). Fonte futura: ProfitDLL.

Dado licenciado para uso pessoal: roda só nesta máquina; `data/` não é versionado.

## Estado
| Fase | Arquivo | Estado |
|---|---|---|
| 0 — sonda do RTD | `probe.py` | feita em 2026-10-27 fora do pico: N_MAX=500, AGR (agressor) descoberto, 11 797 negócios em 5 min, pico 630/s, gap 0,03 %. Repetir em 10:00–10:45. |
| 1 — coletor | `collector.py`, `feed/rtd_source.py` | implementado com allowlist, poll adaptativo, jitter e summary.json; falta uma manhã inteira gravada |
| 2 — agressor | `verify_aggressor.py` | RTD já entrega `AGR` (buy/sell/rlp); conferência manual de 200 negócios contra o Profit |
| 3 — métricas | `metrics.py` | não iniciar antes da Fase 1 íntegra |
| 4 — painel | `dashboard/` | — |
| 5 — replay | `replay.py` | — |

## Rodar
```
uv sync                                       # Python 3.12 + deps (pywin32, polars, pyarrow, ...)
uv run pytest                                 # testes offline, determinísticos
uv run python probe.py --segundos 300         # Fase 0 (Profit aberto, planilha Excel fechada)
uv run python collector.py --duracao-s 300    # Fase 1, teste de 5 min -> data/raw/ (+ .summary.json)
uv run python verify_aggressor.py --n 200     # Fase 2: tabela para conferir AGR contra as cores do Profit
```
Config: copie `.claude/skills/flow-config/config.example.json` para `config.json` na raiz se quiser alterar.

## Regras não negociáveis
- Nunca `ServerStart`, nunca objeto de ordem, nunca `DLLInitializeLogin`, nunca credenciais no chat.
- Nunca assinar tópico fora da allowlist (`T&T0 × {DAT, ACP, PRE, QUL, AVD, AGR} × 0..499` + `INFO/ATV`):
  nome inexistente = access violation dentro do Profit. Sondagem só com `probe.py --testar-campos`.
- Toda saída traz hora do último negócio lido, gap_pct, nº de negócios na janela e `source`.
- "Disparou uma regra", nunca "compra/venda/oportunidade"; nunca P&L.
- O dado é janela deslizante do RTD, não stream; "mão" de corretora é fluxo agregado, não posição.

Medições e decisões: [docs/LESSONS_LEARNED.md](docs/LESSONS_LEARNED.md).
