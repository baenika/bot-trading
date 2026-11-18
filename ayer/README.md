# Consejo de Trading: 6 bots en un grupo de Telegram

Dos mesas: **Cripto** (BTC, ETH, SOL; se valida con backtest) y **Memecoins** (Solana; se valida grabando el mercado real).

Cada vez que el mercado hace un movimiento grande, seis bots lo discuten en tu grupo de Telegram, uno detrás de otro:

| Bot | Qué hace |
|---|---|
| 🔭 **Scout** | Vigila las velas. Avisa cuando hay volumen anormal, una vela enorme o una ruptura del máximo o mínimo de 20 velas. |
| 📰 **Noticias** | Busca *por qué* se mueve el precio: lee titulares recientes (CoinDesk, Cointelegraph, Decrypt) y dice si las noticias confirman el movimiento, lo contradicen o si no hay ninguna noticia detrás. |
| 📈 **Chart** | Pone la entrada, el stop loss y el take profit, avisa si hay una resistencia por el camino y propone un precio de entrada mejor si el precio retrocede. |
| 🧊 **Sentimental** | Incluye la **máquina del tiempo** (solo informativa): simula 10.000 futuros con trozos reales del pasado y te dice qué probabilidad hay de tocar el objetivo antes que el stop. El backtest comprueba si esas probabilidades aciertan. Es el antídoto contra la emoción. Detecta el FOMO y el pánico (precio estirado, RSI extremo, rachas de velas, Fear & Greed) y hace un **estudio histórico**: busca casos parecidos en el pasado y comprueba qué pasó después. Tras 3 pérdidas seguidas, pone una pausa para no operar por venganza. |
| 🤨 **Skeptic** | El que más manda. Parte de "no" y discute el trade con los argumentos de los demás bots. Tiene **vetos**: un R:R malo, un stop demasiado lejos, emoción, noticias en contra o un historial sin ventaja matan el trade. Al final decide **COMPRAR, ESPERAR** (pone una orden en el precio de retroceso) o **PASAR**. |
| 🛡️ **Risk** | Arriesga un 1% del capital por trade y no usa apalancamiento. Corta el día si se pierde un 3% y lo para todo si el capital cae un 15% desde su máximo. |

> El dinero real **no está activado**. El camino es: backtest → unas semanas en papel → y solo entonces decidir.

## 1. Instalar

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

## 2. Backtest con dinero ficticio

```bash
python backtest.py --start 2023-01-01 --end 2025-12-31
python backtest.py --symbols BTCUSDT ETHUSDT SOLUSDT --timeframe 4h
```

Descarga las velas de Binance (no hacen falta claves) y crea en `results/`:
- `informe.md`: compara las métricas **con Skeptic y sin Skeptic** y añade "comprar y mantener" como referencia.
- `equity.png`: la curva del capital ficticio.
- `decisiones_skeptic.csv`: todas las alertas, con lo que dijo cada bot y **qué habría pasado** si se hubiera entrado igualmente.
- `trades_con_skeptic.csv` / `trades_sin_skeptic.csv`.

**Antes de pensar en dinero real, comprueba que:**
- tiene al menos 100 trades (con menos, el resultado es suerte);
- el profit factor es mayor de 1,3 y el drawdown máximo menor del 20%;
- gana a "comprar y mantener" o lo iguala con mucho menos drawdown;
- funciona en **varios periodos y activos**, no solo en uno. Ajustar los parámetros hasta que un periodo salga bonito es engañarse a uno mismo (sobreajuste). Prueba 2023, 2024 y 2025 por separado.

Si el informe dice que los trades rechazados habrían dado R positivo, el Skeptic es demasiado estricto: baja `approve_score` o sube `max_ext_atr`.

Las noticias **no** entran en el backtest, porque no hay un histórico gratuito de titulares. En vivo, el filtro de noticias se suma a todo lo demás.

## 3. Telegram: el grupo y tu chat privado

**El grupo** es donde los 6 bots debaten cada alerta. **Tu chat privado** con el bot Risk es donde te llega lo importante y desde donde lo controlas.

1. Abre **@BotFather** y crea 6 bots con `/newbot` (por ejemplo `David Scout`, `David Noticias`…). Copia los 6 tokens en `config.yaml`.
2. Crea un grupo, añade los 6 bots y escribe en el grupo `/start@tu_bot_risk`.
3. Busca tu bot Risk en Telegram, ábrelo en privado y pulsa **Iniciar**.
4. Ejecuta `python get_chat_id.py TOKEN_DEL_RISK`. Te dará dos números: ponlos en `telegram.chat_id` (el del grupo) y en `telegram.private_chat_id` (el tuyo).
5. Comprueba que funciona:
   - `python live.py --test`: cada bot saluda en el grupo.
   - `python panel_telegram.py`: te llega el panel como imagen a tu chat privado.

**Qué te llega al chat privado:** cada vez que el Skeptic dice COMPRAR o ESPERAR, cada entrada y cada cierre, el panel en imagen cada día, y un aviso si el bot deja de poder leer el mercado.

**Comandos** (solo te obedece a ti; si otra persona le escribe, lo ignora):

| Comando | Qué hace |
|---|---|
| `/panel` | Imagen del panel: capital, curva y último veredicto |
| `/estado` | Resumen en texto |
| `/posiciones` | Posiciones abiertas |
| `/stop` | Interruptor: no abre más operaciones (las abiertas siguen con su stop) |
| `/reanudar` | Vuelve a operar |

## 4. Modo en vivo con dinero ficticio

```bash
python live.py
```

Revisa el mercado cada minuto. Cuando se cierra una vela con un movimiento grande, los bots debaten en el grupo y el Risk publica las entradas, los cierres y un resumen diario. El estado se guarda en `state.json`, así que si lo paras y lo vuelves a lanzar, sigue donde estaba.

Para que funcione 24/7 tiene que estar en un ordenador siempre encendido o en un VPS barato (unos 4–5 €/mes).

**Opcional:** si pones una clave de la API de Anthropic en `llm.anthropic_api_key`, el bot de Noticias usa Claude para leer los titulares y explicar el movimiento con sus propias palabras. Sin clave, funciona con un diccionario de palabras.

## 5. Panel web

```bash
python dashboard_server.py          # se abre http://localhost:8050
python dashboard_server.py --lan    # y también desde el móvil, en la misma wifi
```

Muestra el capital, la rentabilidad, el % de aciertos, el profit factor y el R medio. También el resultado diario, la curva de capital (en backtest, **con y sin Skeptic**), el último veredicto del Skeptic, lo último que ha dicho cada bot, los límites de riesgo, el embudo del Skeptic, las posiciones abiertas y las últimas operaciones.

- Si `live.py` está en marcha, enseña los datos en vivo y se actualiza cada 20 segundos.
- Si no, enseña el último backtest (`results/dashboard.json`).
- Para elegir cuál ver: `http://localhost:8050/?fuente=backtest` o `?fuente=live`.
- El servidor solo enseña el panel y sus datos, nunca `config.yaml` ni tus tokens.

## 6. Mesa Memecoins (Solana)

Es una mesa aparte, con los mismos 6 bots pero con otro trabajo y otras reglas:

| Bot | En la Mesa Memecoins |
|---|---|
| 🔭 Scout | Busca pares nuevos en DexScreener: de 30 min a 72 h de vida y capitalización de 300k a 20M |
| 📰 On-chain | Seguridad del token (RugCheck). Descarta si el creador todavía puede crear tokens o congelar cuentas, si la liquidez no está bloqueada, si los 10 mayores tienen > 25%, si el creador tiene > 3%, si la liquidez es < 40k, el volumen 1h < 50k o ya ha subido > 80% en 1h |
| 📈 Analista | Puntúa de 0 a 100: momentum, compras/ventas, actividad, liquidez/capitalización, aceleración y redes |
| 🧊 Sentimental | Veta si llegas tarde: vela vertical en 5 min, frenesí de compras o +300% en 6 h |
| 🤨 Skeptic | Solo pasa lo que tiene 70+ puntos y ningún veto |
| 🛡️ Risk | Máx. 8% por moneda, stop −6%, trailing desde +12%, máx. 4 posiciones, −6% de límite diario |

**Por qué se prueba distinto:** en memecoins no hay historial fiable, porque las monedas que hacen rug desaparecen y un backtest normal solo vería las que sobrevivieron. La solución es **grabar**:

```bash
python memes_live.py          # opera en papel con datos reales y GRABA todo en data/memes/
python memes_backtest.py      # backtest sobre lo grabado (incluidos los rugs)
python memes_backtest.py --grid   # compara 24 estrategias a la vez
```

Para probarlo ya, sin esperar días, hay una grabación inventada:

```bash
python memes_demo_data.py
python memes_backtest.py --dir data/memes_demo --grid
```

Deja `memes_live.py` grabando **al menos 2–4 semanas** antes de fiarte de un resultado. Y elige estrategias que salgan bien en varias semanas, no la mejor de una sola.

**En Telegram:** la Mesa Memecoins usa los mismos 6 bots. Al grupo solo llegan las compras y las monedas que se quedan cerca de comprarse (para no llenarlo de mensajes). Cada hora llega un resumen de descartes. En tu chat privado, `/memes` te manda el panel de esta mesa. Si quieres un grupo aparte, pon su id en `memes.group_chat_id`.

**En el panel:** botón **🪙 Memes** arriba a la derecha (o `http://localhost:8050/#memes`). Añade la tarjeta "Descartes por regla".

## 7. ¿Y el dinero real?

Solo si el backtest pasa los criterios **y** varias semanas en papel se parecen al backtest. Entonces hay que añadir un ejecutor que mande las órdenes al exchange con claves API **sin permiso de retirada**, y empezar con una cantidad pequeña. No está incluido a propósito.

## Ajustes principales (`config.yaml`)

- `scout.min_vol_z` / `min_strength`: si están más bajos, hay más alertas (y más ruido).
- `chart.rr_target`: el objetivo en múltiplos del riesgo.
- `sentimental.max_ext_atr`: a partir de cuántos ATR de distancia a la media se considera que se está persiguiendo el precio.
- `skeptic.approve_score` / `min_rr`: lo exigente que es el Skeptic.
- `risk.risk_per_trade_pct`: el 1% es lo sensato. No pases del 2%.

*Esto no es asesoramiento financiero. Es una herramienta para probar ideas con datos antes de arriesgar dinero.*
