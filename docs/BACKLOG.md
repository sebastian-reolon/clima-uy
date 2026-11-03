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
- **Ceros de las escalas** de Bella Unión y Artigas: no se encontraron publicados. El de Paysandú (0,04 m IGN) se midió con GNSS en el relevamiento INA-CARU de 2019 (<https://www.ina.gob.ar/delta/pdf/INA-CARU_2019_Informe2_RelevamientoTopografico.pdf>, Tabla 3.7); es el de la escala que lee la CARU y no está confirmado que sea la misma de DINAGUA. Los de Salto y Fray Bentos no se midieron: ver «Salto y Fray Bentos» en la sección del plano.
- **Revisar las cotas** cuando DINAGUA publique un informe nuevo (el último encontrado es el N°13, del 7 de agosto de 2026) y actualizar `content/referencias_uy.json`.

## Plano de crecida por ciudad

- **Fotos diarias de los planos.** Las páginas `/rio/mapa/<ciudad>/` no entran en las fotos diarias, porque cada una carga unos 2 MB de alturas (`niveles.json`); desde una foto, el link «ver el mapa» lleva a la página en vivo. Si se quiere guardar cómo se veía el plano un día, copiar solo el HTML (lleva dibujada la altura de ese día) o guardar `niveles.json` una vez por ciudad, que no cambia entre corridas.
- **Diferencia entre escalas leída a mano.** La resta de Colón a Paysandú (1,18 m, rango 1,14 a 1,20) sale de informes de la CARU de octubre de 2026 y se revisa a mano. Escribir un lector de los informes de crecida de la CARU que la actualice.
- **Más ciudades:** Bella Unión, Constitución, Nuevo Berlín y San Javier quedan para después.
- **Salto y Fray Bentos** no tienen plano porque falta el vínculo entre el cero de su escala y el relieve de la IDE (revisado el 3 de noviembre de 2026). En Paysandú ese vínculo son puntos del puerto medidos con GNSS en 2019, sobre los que se leyó el relieve (+0,39 m). Para estas dos ciudades no hay puntos así:
  - **Salto:** el único cero publicado es 1,3175 m IGN (2,010 m Wharton) en la Tabla 1 del informe 3 INA-CARU de 2019 (<https://www.ina.gov.ar/delta/pdf/INA-CARU_2019_Informe3_ModeloHidrodinamico.pdf>). Es un valor anterior al relevamiento: la campaña de septiembre de 2019 no pasó por Salto, y en las cinco escalas medidas que tenían un valor anterior, ese valor estaba corrido entre 0,25 y 0,32 m (Tabla 3.7 del informe 2). El informe 3 anuncia «una campaña más de relevamiento de ceros de las estaciones faltantes»; no se encontró publicada.
  - **Fray Bentos:** el SOHMA da el cero de la escala del puerto respecto del Plano Hidrométrico Provisorio (ex Wharton): 25,5 cm por debajo del plano de reducción de sondas, que está 80 cm por encima del ex Wharton, o sea 0,545 m sobre el ex Wharton (Publicación 4 E, 2023, <https://sohma.armada.mil.uy/attachments/article/305/Pub4E_Ed_2023.pdf>). El informe 3 INA-CARU lo da como −0,1425 m IGN, también valor anterior al relevamiento. Ninguno de los dos está atado a puntos medidos en tierra dentro del relieve de Fray Bentos; Nuevo Berlín (−0,36 m IGN, medido) queda a 28 km.
  - **Qué haría falta:** para cada ciudad, el cero medido en un datum (IGN o el oficial uruguayo) y al menos tres puntos en tierra, dentro del relieve de la ciudad, con su altura en ese mismo datum (por ejemplo, puntos de nivelación del Instituto Geográfico Militar con monografía, o un relevamiento GNSS como el de Paysandú). No se puede usar el +0,39 de Paysandú: la diferencia entre el relieve y el datum se midió solo ahí.
- **Barrios con límites oficiales.** Las zonas se nombran por el punto de barrio de OpenStreetMap más cercano. Si las Intendencias publican polígonos de barrio, usarlos en lugar de esa aproximación.
- **Peso de la página del plano.** `rio/mapa/paysandu/index.html` pesa unos 780 KB (270 KB comprimida) porque lleva dibujados dos planos, el ancho y el del celular, con las mismas manzanas y calles. Si pesa, dibujar las capas fijas una sola vez y recortarlas con `viewBox`.
