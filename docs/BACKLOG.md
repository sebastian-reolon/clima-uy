# Pendientes

Lo que quedó para después, con el motivo.

## Publicación

- **Crecimiento de las fotos diarias.** Cada foto (`snapshots/<día>/`) pesa unos 380 KB, así que la rama `gh-pages` suma unos 140 MB por año solo de fotos, más su historia en git. Antes de que pese, decidir entre podar las fotos viejas (por ejemplo, dejar una por semana pasado un año) o moverlas a archivos mensuales en GitHub Releases y dejar en el sitio solo el índice con los links.
- **Historia de las medias diarias.** Cada exportación completa reescribe las medias diarias (unos 5 MB). Git guarda solo las líneas que cambian, pero la historia de `gh-pages` crece todos los días. Revisar el tamaño del repositorio a los tres meses.

## Licencias

- **Pedirle permiso al INA** para republicar sus series y pronósticos con atribución. Con la respuesta por escrito se agrega `"ina"` a `FULL_SERIES_SOURCES` y a `FULL_FORECAST_ISSUERS` en `climauy/publish.py`. Los meses ya cerrados no se completan hacia atrás.
- **Pedirle permiso a la CTM Salto Grande** para republicar las lecturas del parte diario.

## Cotas uruguayas

- **Artigas:** la cota de seguridad publicada es de 2023 (CECOED de Artigas, en un informe del SINAE). Buscar un valor más nuevo de DINAGUA o del CECOED, y una cota de alerta o de evacuación si la hay.
- **Ceros de las escalas** de Bella Unión, Salto, Fray Bentos y Artigas: no se encontraron publicados. El de Paysandú es de la escala que lee la CARU y no está confirmado que sea la misma de DINAGUA.
- **Revisar las cotas** cuando DINAGUA publique un informe nuevo (el último encontrado es el N°13, del 7 de agosto de 2026) y actualizar `content/referencias_uy.json`.
