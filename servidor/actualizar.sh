#!/bin/bash
# Baja la última versión de la rama del bot. Si hay cambios, pasa los tests y reinicia los servicios
# (al arrancar, el bot manda al móvil la lista de novedades). Si los tests fallan, se queda la versión vieja.
set -u
DIR=/home/consejo/bot-trading
cd "$DIR" || exit 1
como() { runuser -u consejo -- "$@"; }
antes=$(como git rev-parse HEAD)
como git fetch -q origin || exit 0
rama=$(como git rev-parse --abbrev-ref HEAD)
como git merge -q --ff-only "origin/$rama" || exit 0
despues=$(como git rev-parse HEAD)
[ "$antes" = "$despues" ] && exit 0
if ! como env PYTHONUTF8=1 python3 -m pytest -q -x >/tmp/consejo-tests.log 2>&1; then
    como git reset -q --hard "$antes"
    como env PYTHONUTF8=1 python3 -c "
from consejo.avisos import Avisador
from consejo.telegram import Grupo
Avisador(Grupo('telegram.json'), 'servidor').avisar('error', 'tests', 'La versión nueva del repo no pasa los tests: sigo con la anterior. Revísalo.')"
    exit 1
fi
for s in consejo-cripto consejo-mercados consejo-memes consejo-ballenas; do
    systemctl try-restart "$s.service"
done
