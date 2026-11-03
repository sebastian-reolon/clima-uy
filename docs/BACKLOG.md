# Pendientes

Lo que quedó para después, con el motivo.

## Publicación

- **Crecimiento de las fotos diarias.** Cada foto (`snapshots/<día>/`) pesa unos 380 KB, así que la rama `gh-pages` suma unos 140 MB por año solo de fotos, más su historia en git. Antes de que pese, decidir entre podar las fotos viejas (por ejemplo, dejar una por semana pasado un año) o moverlas a archivos mensuales en GitHub Releases y dejar en el sitio solo el índice con los links.
- **Historia de las medias diarias.** Cada exportación completa reescribe las medias diarias (unos 5 MB). Git guarda solo las líneas que cambian, pero la historia de `gh-pages` crece todos los días. Revisar el tamaño del repositorio a los tres meses.
- **Costo del control de datos de infraestructura.** Antes de cada commit se revisa todo el árbol publicado, fotos incluidas, así que el tiempo crece con las fotos. Si se vuelve lento, revisar solo los archivos que cambiaron en la corrida (los que `git add -A` deja listos) más un control completo una vez por semana.

- **Las fotos viejas comparten `/static`.** Cada foto usa el CSS y los scripts del sitio en vivo, así que un cambio de estilo también cambia cómo se ven las fotos viejas (los datos y el texto no cambian). Si importa, copiar `static/` dentro de cada foto o versionarlo por fecha.
- **Peso de la página del río.** Con los gráficos en línea, `rio/index.html` pesa unos 335 KB. No es «poco» para un celular con datos; revisar qué gráficos se pueden simplificar o cargar más abajo.

## Datos

- **DINAGUA vuelve:** sus archivos del archivo crudo están marcados como `ignored` en el registro de ingesta (no había parser). Cuando se escriba el parser, borrar esas filas del registro (`file_ledger` con `path` que empieza por `dinagua/`) para que se lean de nuevo, igual que hace `python -m climauy reingest` con GloFAS.
- **Kilómetro de río (`river_km`)** está vacío en todas las estaciones. El perfil las pone a la misma distancia; con los kilómetros se podría dibujar a escala.

## Licencias

- **INA, Prefectura y CTM** no publican condiciones de reutilización. Sus datos se republican con la cita por decisión del proyecto (1 de noviembre de 2026). Si alguna pide retirarlos, sacar su clave de `FULL_SERIES_SOURCES` / `FULL_FORECAST_ISSUERS` en `climauy/publish.py` y borrar a mano los meses cerrados que la incluyen. Conviene igual escribirles para avisar y, si responden, anotar su respuesta en `docs/licencias.md`.
- **Archivo mensual en GitHub Releases** que preveía el plan: no está hecho; las lecturas por mes ya están en `data/rio/raw/`.

## Cotas uruguayas

- **Artigas:** la cota de seguridad publicada es de 2023 (CECOED de Artigas, en un informe del SINAE). Buscar un valor más nuevo de DINAGUA o del CECOED, y una cota de alerta o de evacuación si la hay.
- **Ceros de las escalas** de Bella Unión y Artigas: no se encontraron publicados. Los de Salto (1,3175 m IGN) y Fray Bentos (-0,1425 m IGN) están en el informe INA-CARU 2019 de relevamiento topográfico (<https://www.ina.gob.ar/delta/pdf/INA-CARU_2019_Informe2_RelevamientoTopografico.pdf>), como el de Paysandú (0,04 m IGN); falta llevarlos al datum del relieve de la IDE para sus planos. El de Paysandú es de la escala que lee la CARU y no está confirmado que sea la misma de DINAGUA.
- **Revisar las cotas** cuando DINAGUA publique un informe nuevo (el último encontrado es el N°13, del 7 de agosto de 2026) y actualizar `content/referencias_uy.json`.

## Plano de crecida por ciudad

- **Fotos diarias de los planos.** Las páginas `/rio/mapa/<ciudad>/` no entran en las fotos diarias, porque cada una carga unos 2 MB de alturas (`niveles.json`); desde una foto, el link «ver el mapa» lleva a la página en vivo. Si se quiere guardar cómo se veía el plano un día, copiar solo el HTML (lleva dibujada la altura de ese día) más el `niveles.json` de ese día.
- **Diferencia entre escalas leída a mano.** La resta de Colón a Paysandú (1,18 m, rango 1,14 a 1,20) sale de informes de la CARU de octubre de 2026 y se revisa a mano. Escribir un lector de los informes de crecida de la CARU que la actualice.
- **Más ciudades:** Bella Unión, Constitución, Nuevo Berlín y San Javier quedan para después, y Salto y Fray Bentos mientras no tengan su cero en el datum del relieve.
- **Barrios con límites oficiales.** Las zonas se nombran por el punto de barrio de OpenStreetMap más cercano. Si las Intendencias publican polígonos de barrio, usarlos en lugar de esa aproximación.
- **Notas fijas de Paysandú.** Las notas del borde (unos 40 cm por la pendiente) y del cero (relevamiento INA-CARU 2019, unos 35 cm) están escritas para Paysandú. Pasarlas a la configuración de cada ciudad antes de publicar otra.
