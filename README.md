# clima-uy

Sitio de referencia público sobre El Niño y el río Uruguay. Este repo tiene el catálogo de estaciones, los parsers, los análisis y el generador del sitio estático.

## Desarrollo

```
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Las rutas se pasan por variables de entorno: `CLIMAUY_ARCHIVE_DIR`, `CLIMAUY_DB` y `CLIMAUY_OUT`.

## Catálogo

`climauy/stations.json` lista las estaciones de río arriba a río abajo. Las cotas de alerta y evacuación son las oficiales del INA / Prefectura Naval Argentina y siempre llevan su fuente. Las estaciones sin cota publicada no tienen ninguna.
