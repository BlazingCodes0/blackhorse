# ARCHITECTURE (discussion draft)

## Streams
1. YouTube automation -> motar/youtube
2. Digital products -> motar/products
3. Crypto trading -> motar/trading (uses ~/qubitFlowBeta)

## Flow
motar (agents do work) -> heart (safety filter) -> brain (decides, monitors) -> back to motar

## Trading integration
- qubitFlowBeta (Node.js, Express, pg) = execution + signal bot + billing. It stays the money-mover.
- QuantConnect LEAN = backtesting/research engine for strategies BEFORE qubitFlow deploys them.
- Phoenix (Arize) = observability/tracing for every agent decision.
- Market info: motar/trading scrapes exchanges, news, sentiment -> heart checks -> brain decides strategy params -> LEAN backtest -> if ok -> qubitFlow executes.

## AI suggestions (per layer)
- brain/decision: Claude or GPT-4 class model via API for orchestration + CrewAI/LangGraph for multi-agent flows
- motar: Ollama local model (Llama 3.x) for cheap high-volume tasks; GPT-Researcher, Crawl4AI for research
- heart: smaller fast model (e.g. Llama via Ollama) + hard-coded rules engine in Python (keyword/PII/financial-risk checks) — do NOT rely on LLM alone for safety
- trading signals: LEAN natively quant; LLM summarizes market structure per qubitFlow's MARKET_STRUCTURE_RESEARCH.md
- observability: Phoenix traces every agent call

## Next steps
1. Decide primary LLM provider (API key budget)
2. Pick one stream to prototype first
3. Define the heart policy rules file
