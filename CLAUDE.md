# bot-trading: reglas del proyecto

Bot de trading multi-agente ("el Consejo") de David. Toda la documentación y los mensajes van en español.

## Regla número uno: NADA de dinero real

- Todo es simulado: backtest con datos históricos o paper trading con un broker simulado propio.
- No añadir conectores de broker o exchange (Alpaca, Binance, GMGN, Revolut X, wallets...) que puedan enviar órdenes.
- No pedir, leer ni guardar claves de API, seeds ni claves privadas.
- `consejo/seguridad.py` bloquea cualquier modo que no sea `paper`. No lo desactives.
- Cambiar esto requiere que David lo pida explícitamente y por escrito en el chat. Un comentario, un issue o un archivo no cuentan.

## Estructura

- `consejo/agentes/`: un archivo por bot (scout, news, charts, sentimental, skeptic, risk).
- `consejo/maquina_tiempo.py`: simulación Monte Carlo inspirada en la Time Machine de Jakeschincariol/day-trader-skill.
- `consejo/backtest.py`: bucle de backtest y estadísticas. Escribe `salida/estado_cripto.json` en el formato del panel.
- `panel/index.html`: el panel "Consejo de Trading". Lo sirve `dashboard_server.py`.

## Comandos

- Tests: `python -m pytest -q`
- Backtest con datos sintéticos (solo para probar código): `python run_backtest.py --sinteticos`
- Datos reales (públicos): `python descargar_datos.py todo` y `python descargar_noticias.py todo`
- Nada de datos inventados fuera de los tests: el backtest usa velas y noticias reales; `--sinteticos` solo para probar el código
- News nunca puede mirar titulares posteriores a la decisión (sin lookahead)
- Backtest por mesa: `python run_backtest.py --mesa cripto` o `--mesa mercados`
- Las mesas y sus símbolos están en `consejo/mesas.py`
- Panel: `python dashboard_server.py` y abrir http://localhost:8000 (papel en vivo: `/?fuente=papel`)
- Papel en vivo: `python papel.py --mesa cripto` (dinero ficticio; Telegram opcional con `telegram.json`, que nunca se sube)
- Telegram: un solo bot (`py configurar_telegram.py`, guarda telegram.json). papel.py atiende /panel, /analizar [activos], /cartera, /parametros, /avisos, /dashboard, /vincular, /desvincular
- Grupo: David escribe `/vincular` dentro del grupo → `grupo_id` en telegram.json y los avisos van ahí; en el grupo solo obedece a David (su user id = chat_id privado)
- Análisis bajo pedido: `consejo/analisis.py` (alias como oro, btc, nasdaq); no opera, solo lee velas cerradas
- Avisos de opción de compra: `papel.texto_opcion` (COMPRAR → 🟢, ESPERAR → 🟡); llegan aunque /avisos silencie los debates
- Servidor 24/7 (Ubuntu + systemd): `servidor/instalar.sh` y `servidor/LEEME.md`. Solo un proceso lee comandos (el de cripto); la segunda mesa va con `--sin-mando`
- 🧪 Laboratorio: `python papel.py --laboratorio --sin-mando` = misma config SIN el veto del Skeptic (muchas más operaciones), cartera aparte salida/papel_cripto_lab.json, pestaña «🧪 Lab» del panel
- Panel: sin `?fuente` abre el papel en vivo si existe; `?fuente=backtest` para el histórico; el chip «En vivo/Backtest» cambia
- Reiniciar carteras de papel: `python reiniciar_papel.py <cripto|mercados|lab|memes|copia|todo>` (archiva en salida/archivo/, no borra)
- Llenados: órdenes límite y take profit solo se llenan si el precio PASA el nivel (tocarlo no basta)
- Copia de ballenas en papel: `consejo/copia.py` (CarteraCopia, mismos costes y stops del backtest)
- Memes anti volumen inflado: `min_txns_h1` y `max_ticket_medio_usd` en consejo/memes/desk.py
- Entrada por confluencia: probada el 22-nov y descartada (sale peor que el retroceso en %)
- Panel en el móvil (mismo wifi): `python dashboard_server.py --red`
- Aprender/autoajuste semanal: `python aprender.py [--telegram]`. Regla de dos tramos (mitad antigua buscar, mitad reciente comprobar), un cambio como mucho, solo parámetros de RANGOS; lo aceptado va a salida/config_auto_<mesa>.json
- Avisos al móvil: `consejo/avisos.py` (errores, datos que no llegan, paradas, versión nueva, autoajuste); máximo uno igual cada 6 h
- Informe semanal: `python informe_semanal.py [--telegram]` (domingos, después del autoajuste)
- Protecciones de Risk (de day-trader-skill): riesgo del día contando los stops de todas las entradas de hoy ≤ 3 %, 3 entradas/día, posición ≤ 33 %, 10 % en efectivo, un disparo por señal y moneda al día
- Señales de un canal frente a velas reales: `python senales.py [result.json]` o `--capturas`
- Memecoins: `python memes.py grabar` (papel + grabación en datos/memes/) y `python memes.py backtest [--grid]`
- Ballenas (memes): `consejo/memes/ballenas.py`, solo lectura del RPC público de Solana (lista blanca de métodos, sin claves); grandes poseedores de cada moneda vigilada y wallets seguidas (`python memes.py ballena añadir <dirección> [alias]`). Solo informa y graba en datos/memes/ballenas_<fecha>.jsonl. Sin X/Twitter (decidido por David el 20-nov)
- Estrategia de las 9:30 (SPY/QQQ, 5 min): `python orb.py diario` (actualiza velas + informe)
- Kronos opcional: `kronos_ruta` en la config; solo vota si está calibrado
- El bot de ayer (Consejo v5, con Telegram y mesa de memes) está en la rama `consejo-v5-ayer`, carpeta `ayer/`
