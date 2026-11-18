# bot-trading · el Consejo

Bot de trading multi-agente de David. Seis bots discuten cada oportunidad y el Skeptic puede vetarla. **Todo es simulado: no hay dinero real, ni claves, ni órdenes reales.**

| Bot | Qué hace |
| --- | --- |
| Scout | Detecta movimientos raros: volumen anómalo, velas enormes, rupturas, salidas de compresión |
| News | Busca por qué se mueve (en backtest no opina) |
| Charts | Hace el plan: entrada, stop, objetivo, entrada mejor si retrocede y niveles que estorban |
| Sentimental | Frena el FOMO y la operación por venganza, y mira casos parecidos del pasado |
| Skeptic | Suma pros y contras, aplica vetos y decide COMPRAR, ESPERAR o PASAR |
| Risk | Arriesga un 1 % por operación, para el día al −3 % y para todo al −15 % |

La **máquina del tiempo** (idea de [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill), MIT) repite cada plan miles de veces con rendimientos pasados del activo y cuenta cuántas veces llega antes al objetivo que al stop.

## Cómo usarlo

Necesitas Python 3.10 o superior. No hace falta instalar nada más, salvo `pytest` para los tests.

```bash
python run_backtest.py          # prueba con datos sintéticos
python dashboard_server.py      # abre http://localhost:8000
```

Con datos reales (velas de 1 hora de [data.binance.vision](https://data.binance.vision/?prefix=data/spot/monthly/klines/BTCUSDT/1h/), descargadas a mano):

```bash
python run_backtest.py --csv datos/BTCUSDT-1h.csv --csv datos/ETHUSDT-1h.csv
```

Los ajustes están en `config.example.json`. Cópialo y pásalo con `--config mi_config.json`.

## Cómo leer los resultados

- El panel compara el Consejo **con** Skeptic, **sin** Skeptic y **comprar y mantener**.
- Un buen resultado con datos sintéticos no significa nada: solo prueba que el código funciona.
- Con datos reales, lo que importa es el drawdown, el número de operaciones y el profit factor, no solo la rentabilidad.

## Seguridad

`consejo/seguridad.py` bloquea cualquier modo que no sea `paper` o `backtest`, y cualquier configuración que traiga una clave. Los tests comprueban que no hay código de brokers.
