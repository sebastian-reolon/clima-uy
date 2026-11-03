# clima-uy

Sitio de referencia público sobre El Niño y el río Uruguay. Este repo tiene el catálogo de estaciones, los parsers, los análisis y el generador del sitio estático.

Hecho por [Sebastián Reolon](https://github.com/sebastian-reolon).

## Desarrollo

```
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Las rutas se pasan por variables de entorno: `CLIMAUY_ARCHIVE_DIR`, `CLIMAUY_DB` y `CLIMAUY_OUT`.

## Mapas de ciudades

Cada mapa de inundación sale de un paquete por ciudad en `content/mapa/<slug>/` (`config.json` a mano, `pack.json.gz` generado), que se commitea. Hay dos extras opcionales: `mapa` (`pip install -e '.[mapa]'`) para la capa satelital del build del sitio, y `mapa-pack` (`pip install -e '.[mapa-pack]'`) para armar los paquetes. Sin `mapa` el sitio igual se construye y las páginas avisan que no se pudo consultar el satélite. Para generar el paquete de una ciudad: `python -m climauy mapa-pack <slug>`. Las descargas en bruto van a la carpeta que indica `CLIMAUY_MAPA_CACHE` (por defecto `~/.cache/clima-uy/mapa`), nunca al repo.

## Catálogo

`climauy/stations.json` lista las estaciones de río arriba a río abajo. Las cotas de alerta y evacuación son las oficiales del INA / Prefectura Naval Argentina y siempre llevan su fuente. Las estaciones sin cota publicada no tienen ninguna.
