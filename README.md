# Argos

**Vigilancia conductual de leaderboards de copy trading.**
Detección de manipulación y reducción de falsos positivos aplicando metodología SOC a telemetría de plataformas financieras.

> Cerbero guarda la puerta. Argos vigila.

---

## Qué es esto

Argos no es un bot de trading. Es un aparato de medición.

La pregunta que responde no es *"¿cuánto puedo ganar copiando a este trader?"* sino tres preguntas más chicas y contestables:

1. ¿Qué fracción de los líderes de un leaderboard muestra evidencia estadística de manipulación de volumen?
2. ¿Cuánto se degrada el desempeño de un líder entre su fill y el de quien lo copia?
3. ¿Se puede reducir la tasa de falsos positivos de un motor de detección conductual con meta-labeling?

La tercera es la que importa, y es un problema de SOC, no de finanzas.

## Por qué un proyecto de trading es un proyecto de seguridad

La equivalencia estructural es casi uno a uno:

| Argos | SOC |
|---|---|
| Ingesta de trades del leaderboard | Pipeline de telemetría / log ingestion |
| Baseline conductual por líder | UEBA — baselining de entidad |
| Reglas de detección (`ARG-001`…) | Reglas de detección tipo Sigma |
| `surveillance_alert.triage_state` | Cola de triage del analista N1 |
| Meta-modelo de filtrado | Reducción de fatiga de alertas |
| `decision_log` | Audit trail / case management |
| Falsification audit | Validación de detection engineering |

La fatiga de alertas por falsos positivos es uno de los problemas medibles y no resueltos de la operación SOC. Meta-labeling —una técnica de finanzas cuantitativas para filtrar señales falsas sin perder las verdaderas— no se ha transferido mucho a ese dominio. Ahí está la tesis.

## Arquitectura

```
                    ┌──────────────┐
   leaderboard ────▶│   ingest/    │──┐
   CCXT / WS   ────▶│   (Python)   │  │
                    └──────────────┘  │
                                      ▼
                            ┌───────────────────┐
                            │  TimescaleDB      │
                            │  append-only en   │
                            │  tablas de        │
                            │  evidencia        │
                            └─────────┬─────────┘
                                      │
        ┌──────────────┬──────────────┼──────────────┐
        ▼              ▼              ▼              ▼
  ┌───────────┐  ┌───────────┐  ┌──────────┐  ┌───────────┐
  │surveillance│ │falsification│ │analytics │  │  paper/   │
  │  (detección)│ │  (control  │ │(métricas)│  │(ejecución │
  │            │ │  negativo) │ │          │  │ simulada) │
  └─────┬──────┘  └─────┬─────┘  └────┬─────┘  └─────┬─────┘
        └───────────────┴─────────────┴──────────────┘
                                │
                       ┌────────▼────────┐
                       │  api/ + dash/   │
                       └─────────────────┘
```

### Servicios

| Directorio | Rol |
|---|---|
| `ingest/` | Scraper del leaderboard, CCXT, WebSocket de mercado |
| `db/` | Esquema TimescaleDB, migraciones |
| `falsification/` | Control negativo. Entornos sin señal y placebos de microestructura |
| `surveillance/` | Benford, roundedness, ciclos en grafo, clasificador de boosting |
| `analytics/` | Sharpe, Sortino, drawdown, trades efectivamente independientes |
| `paper/` | Ejecución simulada con fricción `G ≈ σ·(Q/V)^0.5` + ajuste sigmoide |
| `metalabeling/` | Triple-barrier + meta-modelo de triage |
| `api/` | FastAPI |
| `dashboard/` | React + Recharts |

## Arranque rápido

```bash
cp .env.example .env        # editar credenciales
docker compose up -d        # Postgres 16 + TimescaleDB, aplica db/schema.sql
cd ingest
pip install -e .
python -m argos_ingest.freeze_cohort --platform bybit --top 50   # UNA sola vez
python -m argos_ingest.trades --loop                             # ingesta continua
```

## Reglas metodológicas

No negociables, aplican a todo el código de modelado:

- **Nunca k-fold aleatorio** en series de tiempo. Solo walk-forward o purged k-fold con embargo.
- **Purging**: fuera del train las observaciones cuyo label se traslapa con el test.
- **Sin normalización global.** Cualquier estadístico calculado sobre todo el dataset es leakage.
- **Pesos por unicidad**: labels traslapados no son muestras independientes.
- Toda métrica se reporta junto a su versión ajustada por multiplicidad (deflated Sharpe).
- **El falsification audit corre antes que cualquier modelo.** Si el pipeline encuentra señal en ruido, se bloquea el reporte de resultados reales.

## Estado

| Fase | Módulo | Estado |
|---|---|---|
| 01 | Ingesta + cohorte congelado | En curso |
| 02 | Falsification audit | Pendiente |
| 03 | Analytics + surveillance | Pendiente |
| 04 | Paper con fricción | Pendiente |
| 05 | Meta-labeling | Pendiente |
| 06 | API + dashboard | Pendiente |

## Alcance y límites

Este repositorio **no ejecuta órdenes con capital real**. La única excepción es un experimento de calibración acotado y documentado (ver `docs/calibracion.md`), cuyo objetivo explícito es medir la brecha entre el fill del líder y el propio, no evaluar rentabilidad.

Nada aquí constituye asesoría financiera ni fiscal.

## Referencias

- Nikolopoulos, S. — *Spurious Predictability in Financial Machine Learning*, arXiv:2604.15531
- López de Prado, M. — *Advances in Financial Machine Learning*, caps. 3 y 7
- Falk, Tsoukalas & Zhang — *Can AI Detect Wash Trading?*, arXiv:2311.18717
- Cong et al. — *Crypto Wash Trading*
- Bouchaud, J.P. — Latent liquidity y la ley de raíz cuadrada de impacto

## Licencia

MIT
