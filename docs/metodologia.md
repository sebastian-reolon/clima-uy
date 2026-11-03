# Cómo se arma el monitor del río

Esta página explica de dónde salen los números del módulo del río, qué supuestos hay detrás y qué no se puede concluir de ellos. Todo el código está en el repositorio público y cada dato se puede rastrear hasta el archivo que lo trajo.

## De dónde salen los datos

- **ANA (Brasil).** Nivel y caudal cada 15 minutos de las estaciones brasileñas del río y de los afluentes Ibicuí, Ibirapuitã y Quaraí, desde el servicio `DadosHidrometeorologicos`. La historia diaria sale del servicio `HidroSerieHistorica`, que es la serie consolidada por la agencia.
- **INA (Argentina).** Las alturas del río que mide Prefectura Naval Argentina, con las cotas de alerta y evacuación que publica el INA, y los pronósticos oficiales a 13 días con su banda. El INA también tiene la historia larga de cada estación.
- **CTM Salto Grande.** El informe diario de la represa: caudal turbinado y vertido de Salto Grande y volumen de los embalses de Machadinho e Itá.
- **GloFAS, vía Open-Meteo.** Caudal pronosticado a 30 días por un modelo global, en celdas sobre el cauce. GloFAS no dice a qué hora corrió el modelo: el sitio muestra cuándo se bajó el pronóstico. De GloFAS se usa la última bajada de cada día.
- **NOAA CPC.** Los índices de El Niño: RONI, ONI y la anomalía semanal de la región Niño 3.4.

Cada fuente se baja cada 3 horas y el archivo crudo se guarda tal como llegó. Los horarios se guardan en UTC. Los días se cortan en hora de Montevideo.

## Una estación, una sola regla

Cada estación mide desde su propio cero. Por eso:

- nunca se comparan alturas en metros entre estaciones distintas;
- para una misma estación nunca se mezclan series con distinto cero. En Paso de los Libres, por ejemplo, la regla del INA y la de ANA difieren en unos 40 centímetros. Si la estación tiene serie del INA, se usa esa; si no, se usa solo ANA.
- CTM republica las mismas escalas de Prefectura que publica el INA, con su mismo cero; se usa solo para completar días sin dato del INA.

## Cómo se comparan estaciones distintas: el percentil

Para poner todo el río en un mismo gráfico se usa el percentil de cada estación contra su propia historia diaria desde el 1 de enero de 1990. Un percentil de 90 quiere decir que el río está más alto que el 90% de los días desde 1990 en esa misma estación. No depende de dónde está el cero de la regla.

- Se usa el caudal cuando la estación lo tiene con historia suficiente; si no, el nivel.
- Una estación necesita al menos 10 años de días con dato para tener percentil. Si tiene menos, se muestra su altura pero no su percentil.
- Si los datos de una estación empiezan después de 1990, las frases no dicen «desde 1990» sino desde el primer año en que tiene datos (por ejemplo, «el valor más alto desde 2014»).
- El valor de hoy y el de hace 3 días se comparan contra la misma historia, que termina 3 días antes del último dato, para que ningún valor se compare consigo mismo.
- Los percentiles nunca se redondean para arriba: 99,5 se muestra como 99,5 y no como 100.
- El gráfico marca, donde hay cota oficial de alerta, en qué percentil cae esa cota. Solo lo hace cuando la línea de esa estación es su nivel: si la estación se compara por caudal, la marca no se dibuja, porque un percentil de caudal y uno de nivel no se pueden poner en la misma escala.

Para las estaciones sin historia propia se pensó usar como respaldo el reanálisis de GloFAS. No hizo falta: el servicio de series históricas de ANA funciona (verificado el 1 de noviembre de 2026). Las estaciones que aun así no llegan a 10 años no llevan percentil, en vez de tomar prestada la historia de un modelo.

## Datos viejos y lecturas raras

- Un dato con más de 36 horas se muestra como desactualizado, con la fecha del último dato, y no entra en la frase de hoy ni en el perfil. Las estaciones que informan una vez por día (la mayoría de las de Prefectura) tienen 48 horas, para que no queden desactualizadas todas las tardes hasta la lectura siguiente.
- Las lecturas que saltan de golpe y vuelven (por ejemplo, un caudal de 421 m³/s entre lecturas de 13.800) se marcan como sospechosas y no entran en promedios ni gráficos. Esta revisión se aplica solo a series con lecturas más seguidas que cada 6 horas.
- Los valores de relleno de las fuentes (`-999`, `99.99`, celdas vacías) se descartan.

## Cotas oficiales y ciudades uruguayas

Las cotas de alerta y evacuación que se dibujan son las del INA y Prefectura Naval Argentina, en el cero de cada estación argentina. Si una estación no tiene cota oficial, no se dibuja ninguna línea. Una estación «alcanzó o superó» su cota cuando su lectura es igual o mayor que la cota; los días de las crecidas pasadas se cuentan con la misma regla.

Las estaciones se ordenan de aguas arriba a aguas abajo, pero todavía no está la distancia en kilómetros sobre el río de cada una: por eso el perfil las pone a la misma distancia entre sí.

Las ciudades uruguayas se muestran desde la estación de enfrente que tiene datos al día, y siempre se dice cuál es:

- Bella Unión: Monte Caseros (INA), enfrente.
- Artigas: Quaraí (ANA), enfrente, sobre el río Cuareim.
- Salto: Concordia (INA), enfrente.
- Paysandú: Colón (INA), enfrente.
- Fray Bentos: no hay estación cercana con datos al día.

Las cotas que se usan en Uruguay (DINAGUA, SINAE, CECOED) pueden estar en otro cero. Mientras no haya una equivalencia oficial documentada, no se traducen a la escala de la ciudad.

## Lo que tarda el agua en bajar

El rezago es cuántos días pasan entre el pico en Paso de los Libres y el pico aguas abajo (Salto Grande arriba, con caudal, y Concordia, con nivel).

1. Se buscan las crecidas: los picos de la estación de abajo por encima del percentil 95 de su historia, separados por al menos 30 días. Una crecida que terminó hace menos de 15 días (o que sigue) todavía no se usa, para que el resultado no cambie mientras pasa.
2. Para cada una se busca el pico de arriba en los 15 días anteriores. Si el máximo cae en el borde de esa ventana, la crecida se descarta y se cuenta como descartada.
3. Se dejan afuera, de a una, tres crecidas enteras (2009, 2015 y 2023). Con las demás se calcula el rezago y se ajusta una relación entre las dos estaciones; después se pronostica la crecida que quedó afuera.
4. El rezago cuenta como validado solo si en las tres crecidas el pico pronosticado cae a 1 día o menos del observado y el pronóstico anda mejor que suponer que el río sigue igual que hace tantos días como el rezago.

Si no pasa, el sitio muestra lo que tardó el agua en las crecidas pasadas, dice que no está validado como pronóstico y explica por qué.

## Cómo se verifican los pronósticos

Cada corrida del INA y de GloFAS se guarda. Cuando llega el dato observado, se compara el pronóstico de cada día con la media observada de ese día, según cuántos días antes se pronosticó. Para GloFAS en Concordia se compara contra el caudal de Salto Grande abajo, porque la serie de caudal de Concordia dejó de publicarse.

Hacen falta al menos 14 corridas, y 10 comparaciones a un día de anticipación, para mostrar resultados. Hasta entonces el sitio dice cuántas corridas lleva acumuladas desde el 1 de noviembre de 2026.

GloFAS es un modelo global sin corregir para este río: sirve para ver la tendencia, no para leer un caudal exacto.

## Las frases automáticas

Las frases que resumen la situación salen de plantillas fijas a partir de los datos. Ningún modelo de lenguaje escribe texto publicado: la misma situación da siempre la misma frase.

## El plano de crecida de cada ciudad

Cada ciudad con plano (por ahora Paysandú) tiene una página que muestra qué terreno queda por debajo del río a una altura elegida, y cuánta gente vive ahí. Es una estimación hecha con el relieve, no una observación.

- **Relieve:** el modelo digital del terreno de 1 m de la IDE Uruguay, llevado a celdas de 2 m. El cero de la escala de la ciudad se pasa al mismo datum con una fuente citada.
- **Agua:** se supone plana frente a toda la ciudad, a la altura elegida sobre ese cero. Se marca el terreno más bajo que esa superficie y conectado con el río; el cauce no cuenta. Los cálculos van cada 25 cm de la escala, más uno exacto en cada marca con fuente (alerta, evacuación, picos pasados).
- **Gente:** la grilla de población de WorldPop 2026, de 100 m, repartida en partes iguales dentro de cada celda. Se lee como un piso y se escribe redondeada («unas 300»); por debajo de 15 personas dice «menos de 15».
- **Zonas:** los segmentos censales del INE de 2011, cada uno con el nombre del barrio de OpenStreetMap más cercano. No son límites oficiales.
- **Altura de hoy:** ninguna de estas ciudades tiene una escala uruguaya con dato en tiempo real. Se toma la escala argentina de enfrente y se le resta la diferencia entre las dos escalas medida en informes de la CARU. Es la única excepción a la regla de no pasar cotas de una escala a otra, y la página lo dice. Si esa estación no tiene dato al día, la página no estima la altura de hoy.
- **Satélite:** la mancha de agua que vio el radar de Sentinel-1 en la última pasada de los últimos 10 días, del servicio GFM de Copernicus. Se consulta en cada actualización; si no responde, se muestra la última pasada guardada o se dice que no se pudo consultar.

El relieve, la población y las zonas se procesan una vez, fuera del servidor, y quedan en el repositorio. En cada actualización solo cambian la altura de hoy y el satélite.

## Límites

- Esto no es una alerta oficial ni reemplaza a SINAE ni a los CECOED.
- Un percentil alto dice que el río está alto para esa estación, no que haya peligro en una ciudad.
- La historia de cada estación tiene huecos; el percentil se calcula con los días que hay.
- DINAGUA está caído desde al menos el 1 de noviembre de 2026; cuando vuelva, se suman las estaciones uruguayas.
