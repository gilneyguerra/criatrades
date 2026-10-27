# LESSONS LEARNED — CriaTrades

Fatos medidos nesta máquina (Windows 11, Profit Nelogica, Python 3.12 via uv). Atualizar sempre que uma
medição contradisser o que está aqui.

## 2026-10-27 — Fase 0 (probe RTD)

### Interface COM do RTD
- ProgID `rtdtrading.rtdserver`, CLSID `{272D2E65-05FB-4500-BD7B-5905D5B0A1B8}`, TypeLib
  `{EFCFBDCA-78A5-450B-8228-346C4F44D5B8}`. **LocalServer32 = profitchart.exe**: o servidor COM é o próprio
  Profit (out-of-proc). Nada roda "dentro" do Python; cada chamada é um salto de processo (~2–5 ms).
- Métodos (IRtdServer): `ServerStart(CallbackObject)`, `ConnectData(TopicID, Strings, GetNewValues)`,
  `RefreshData(TopicCount)`, `DisconnectData(TopicID)`, `Heartbeat()`, `ServerTerminate()`.
- **`ConnectData` funciona sem `ServerStart`.** `ServerStart(None)` provoca *access violation* dentro do
  profitchart.exe (volta como `com_error`, Profit sobrevive). `ServerStart(objeto Python via wrap)` dá
  "Tipo não correspondente" (exige IRTDUpdateEvent nativo). **Decisão: nunca chamar `ServerStart`.**
- `ConnectData(id, ["T&T0","DAT",0], True)` → `('17:34:45.331', True)`. Índice como `int` funciona.
- `RefreshData(n)` → `(((ids...), (valores...)), n)` e devolve **só os tópicos alterados** desde a última
  chamada (semântica do Excel). Por isso o cliente mantém cache por TopicID e o `RtdSource` chama
  `RefreshData` até voltar 0 alterações antes de ler a janela (leitura coerente). Em 20 s, 6 de 201 leituras
  precisaram de uma 2ª chamada.
- Latência média do `RefreshData`: 2,9 ms. Assinar 2 501 tópicos (500 linhas × 5 campos + ATV): 0,4 s.

### Tópicos
- **Tópico ou campo inexistente NÃO devolve "Comando Inválido"**: provoca access violation dentro do Profit
  (`Read of address 0000000000000010`), capturado como `com_error`. O Profit sobreviveu a ~70 dessas no
  probe, mas é comportamento indefinido. `probe.py` só sonda nomes com `--testar-campos`.
- **N_MAX = 500** linhas (índices 0..499) nesta janela T&T0; índice 500 → AV. A planilha original usa
  0..598: o limite provavelmente segue a configuração da janela Times & Trades no Profit, não o RTD.
  Medir de novo se a janela for reconfigurada.
- Campos extras testados em `T&T0/<campo>/0`: só **`AGR`** respondeu → `'Comprador'` / `'Vendedor'`.
  Isto é o **agressor direto do Profit**, o mesmo que colore o T&T. Já incorporado ao schema
  (`aggressor: buy|sell|None`). A inferência via LIV0 (Fase 2) fica como verificação, não como fonte.
  Falharam: SEQ HOR QTD VOL AGC AGV TIP COM VEN AGRESSOR ID NEG COR CPR VND DATA ULT.
- `T&T0/INFO/ATV` → `'WINZ26'`. Falharam INFO/HOR DAT ULT NEG VOL QTD ABE MAX MIN FEC.
- **`LIV0` não respondeu a nenhum nome** (PRE QUL QTD ACP AVD COR CPR VPR CQT VQT BID ASK PRC QTC PRV QTV
  COMPRA VENDA PCO PVE QCO QVE CCO CVE COC COV, nem INFO/ATV). Hipóteses: não havia janela Livro de Ofertas
  aberta no Profit, ou o prefixo não é `LIV0`. Verificar na planilha-exemplo de book da Nelogica antes de
  sondar de novo (cada erro = AV no Profit).
- A doc pública da Nelogica ("Significados e sintaxe do RTD", "Como exportar dados para o Excel") só cobre
  cotações `=RTD("rtdtrading.rtdserver";;"ATIVO";"ULT")`; não documenta T&T0/LIV0.

### Dados
- Orientação da janela: **índice 0 = negócio mais novo** (`newest_first=True`).
- `QUL` vem como float (1.0); `PRE` float (178390.0); `ACP`/`AVD` nome curto da corretora (`'BTG'`, `'XP'`).
- `DAT` só tem hora com ms, sem data → combinar com a data local America/Sao_Paulo no momento da leitura.
- Excel aberto (sem a planilha RTD) **não** bloqueou a conexão. A restrição "uma exportação por vez" ainda
  não foi reproduzida; o coletor avisa se o EXCEL.EXE abrir no meio da coleta.
- `AGR` tem três valores: `'Comprador'`, `'Vendedor'` e **`'RLP'`** (Retail Liquidity Provider — negócio
  fechado contra o provedor de liquidez da corretora, sem agressor de mercado). Em 15 000 leituras: 51 %
  Vendedor, 45 % Comprador, 4 % RLP. RLP vira `aggressor=None`; na gravação de 300 s foram 741 de 11 797 (6 %).

### Gravação de 300 s (17:48–17:53, after-market, fora do pico)
| métrica | valor |
|---|---|
| negócios | 11 797 em 300 s (média 39/s) |
| pico | **630 negócios/s** (17:50:59) |
| leituras | 2 980; 1 com gap → gap_pct 0,03 % |
| leituras que precisaram de 2º RefreshData | 50 (1,7 %) |
| latência média RefreshData | 1,82 ms |
| ts monotônico no CSV | sim (0 retrocessos) → o alinhamento não inventou negócios |

- **O único gap aconteceu no segundo de pico (630/s)**: última linha conhecida 17:50:58.881, linha mais antiga
  da janela nova 17:50:59.053 → buraco de 172 ms ≈ **~100 negócios perdidos**. Para a janela de 500 girar
  inteira, o poll ficou ≥ ~0,9 s sem ler: travamento do lado do Profit (o servidor RTD roda no processo do
  profitchart.exe, que também estava desenhando o burst) ou do Python. Com 630/s a janela guarda só
  **0,8 s de histórico**; no pico 10:00–10:45 (bem acima disso) qualquer travamento vira gap.
- Decisão provisória do critério da Fase 0: gap_pct 0,03 % ≤ 2 % → **RTD basta fora do pico**. A decisão
  definitiva depende da medição em 10:00–10:45. Mitigações antes de cotar a DLL: (a) aumentar o nº de linhas
  da janela Times & Trades no Profit (a planilha original tinha 599 → o limite parece ser da janela, não do
  RTD) e medir N_MAX de novo; (b) coletor sem outro trabalho na mesma thread (o flush em Parquet já é leve).
- O coletor agora loga, em cada gap, o buraco em ms, a perda estimada e o intervalo máximo entre polls.

## Decisões de projeto decorrentes
- Dedup por **alinhamento** da janela (nova leitura deve conter a anterior deslocada de k linhas), não por
  conjunto de tuplas: negócios idênticos consecutivos são legítimos no WIN.
- `gap=True` só no primeiro negócio da leitura em que nenhum deslocamento alinhou; a perda é desconhecida.
- Nunca sondar nomes de tópicos em produção; `RtdSource.start()` só assina os 6 campos conhecidos.
