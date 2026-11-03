# Licencias de las fuentes

Qué permite cada fuente, dónde lo dice y qué publica el sitio de cada una. Todo se consultó el 1 de noviembre de 2026.

El sitio ofrece para descargar los datos de todas sus fuentes, siempre con la cita y el link a la fuente. Las páginas también los muestran en estados, tablas y gráficos hechos con las series diarias.

ANA, GloFAS (vía Open-Meteo y Copernicus) y NOAA dicen por escrito que se pueden republicar. El INA, Prefectura Naval Argentina y la CTM Salto Grande no publican condiciones de reutilización: no lo permiten ni lo prohíben por escrito. Para esas tres, republicar sus datos públicos citándolas es un criterio del proyecto, no un permiso legal. Si alguna de esas fuentes pide que se retiren sus datos, se retiran: el pedido se puede hacer en los issues del repositorio (<https://github.com/sebastian-reolon/clima-uy/issues>).

## Resumen

| Fuente | Qué dice | Qué publica el sitio |
|---|---|---|
| ANA (Brasil) | Permite reproducir los datos citando la fuente | Series completas, con la cita |
| INA y Prefectura Naval Argentina | El sistema de datos no publica términos | Series completas y pronósticos, con la cita, por criterio del proyecto; se retiran si lo piden |
| CTM Salto Grande | No publica términos de reutilización | Lecturas completas, con la cita, por criterio del proyecto; se retiran si lo piden |
| GloFAS vía Open-Meteo | CC BY 4.0 (Open-Meteo) y licencia CEMS-FLOODS (Copernicus), con aviso obligatorio | Series completas, con los dos avisos |
| NOAA CPC | Dominio público | Series completas, con la cita |

En el código son dos listas en `climauy/publish.py`: `FULL_SERIES_SOURCES = {"ana", "ana_hist", "ina", "ctm"}` (fuentes de lecturas) y `FULL_FORECAST_ISSUERS = {"ina", "glofas"}` (fuentes de pronósticos). Los índices del CPC se publican siempre completos. Para retirar una fuente se la saca de la lista que corresponda: deja de tener archivos de descarga y la página de datos lo dice. La página de datos explica, fuente por fuente, qué se publica y con qué criterio.

Todavía no se arma el archivo mensual comprimido en GitHub Releases que preveía el plan. Las lecturas por mes ya quedan en `data/rio/raw/`.

Los meses cerrados no se completan hacia atrás. Un archivo mensual de lecturas o pronósticos se actualiza mientras su mes está en curso, se escribe una última vez en la primera corrida después de que el mes termina y desde ahí queda fijo. Si más adelante se agrega una fuente a las listas, los meses ya cerrados no la incluyen; si se saca una, los meses cerrados que la incluían hay que borrarlos a mano. Las medias diarias, en cambio, se rehacen enteras en cada exportación completa, y los archivos de series que ya no se exportan se borran.

## ANA, Agência Nacional de Águas e Saneamento Básico (Brasil)

Qué se usa: la telemetría del servicio `ServiceANA.asmx` (lecturas cada 15 minutos) y las series históricas consolidadas `HidroSerieHistorica`, de las estaciones brasileñas del río Uruguay y sus afluentes.

Qué dice: el registro de metadatos de la Rede Hidrometeorológica Nacional en el SNIRH, que es la red de estaciones de donde salen estos datos, dice: «É permitida a reprodução de dados e de informações, desde que citada a fonte.» La misma frase está en los conjuntos de datos de estaciones del portal de datos abiertos de la ANA.

- Registro de la red: <https://metadados.snirh.gov.br/geonetwork/srv/api/records/f85dbf06-a869-414c-afc5-bb01869e9156>
- Portal de datos abiertos: <https://dadosabertos.ana.gov.br/>, por ejemplo <https://dadosabertos.ana.gov.br/datasets/ca92f3f4be234361b3b0b0e7b82d8012_2/about>
- Servicio de telemetría: <https://telemetriaws1.ana.gov.br/ServiceANA.asmx>

El servicio de telemetría en sí no publica términos. La autorización se toma de la declaración general de la ANA sobre los datos de su red. De contexto, Brasil tiene una política de datos abiertos para el gobierno federal (Decreto 8.777/2016), pero no se usa como permiso: el permiso es la frase de la ANA.

Ojo: el sitio institucional `gov.br/ana` dice que su contenido está bajo CC BY-ND 3.0 (sin obras derivadas). Eso aplica a los textos del sitio, no a los datos de la red, que tienen la frase de arriba. No se reutiliza ningún texto del sitio de la ANA.

Qué publica el sitio: las medias diarias y las lecturas crudas, con la cita «Fuente: ANA, Agência Nacional de Águas e Saneamento Básico (Brasil)».

## INA (Instituto Nacional del Agua) y Prefectura Naval Argentina

Qué se usa: el Sistema de Información Hidrológica de la Cuenca del Plata del INA (API a5), que publica las alturas y caudales de las escalas del río Uruguay, muchas de ellas de la red de Prefectura, junto con las cotas de alerta y evacuación y los pronósticos del INA.

Qué dice:

- El portal del sistema (<https://alerta.ina.gob.ar/pub/gui>) y la interfaz de la API (<https://alerta.ina.gob.ar/a5>) no publican términos de uso ni licencia.
- El catálogo de redes de la API (<https://alerta.ina.gob.ar/a5/obs/puntual/fuentes>) marca la red «escalas Prefectura Nacional» con `"public": true`. Eso indica que los datos son de acceso público en el sistema, no es una licencia.
- La página de Prefectura con las alturas de los ríos (<https://contenidosweb.prefecturanaval.gob.ar/alturas/>) no publica términos.
- La página institucional del INA en argentina.gob.ar (<https://www.argentina.gob.ar/ina>) dice que «los contenidos de Argentina.gob.ar están licenciados bajo Creative Commons Atribución 4.0 Internacional». Eso cubre los contenidos de ese sitio, no el sistema de datos de `alerta.ina.gob.ar`.
- En el portal nacional de datos abiertos (<https://datos.gob.ar/>) no aparece ningún conjunto de datos hidrométricos del INA ni de Prefectura: se buscó por «Instituto Nacional del Agua», «hidrométrica», «alturas ríos» y «prefectura», y ninguna de las 45 organizaciones que publican en el portal es el INA ni Prefectura.

No queda claro: el INA ofrece estos datos en una API pública, pero no dice por escrito si se pueden republicar.

Qué publica el sitio: las series completas (medias diarias y lecturas crudas por mes) y los pronósticos del INA, con la cita «Fuente: INA, Sistema de Información Hidrológica de la Cuenca del Plata» y el link. Las páginas además los muestran en estados, tablas y gráficos: el recorrido de los últimos 30 días, las temporadas de crecida desde 1990, los picos de las crecidas pasadas, la línea de lo observado y la banda de los pronósticos. Es un criterio del proyecto, no un permiso del INA ni de Prefectura. Si el INA o Prefectura piden retirarlos, se saca `"ina"` de `FULL_SERIES_SOURCES` y de `FULL_FORECAST_ISSUERS`, y los meses cerrados que los incluyen se borran a mano. Pedido: <https://github.com/sebastian-reolon/clima-uy/issues>.

## CTM Salto Grande (Comisión Técnica Mixta de Salto Grande)

Qué se usa: el parte diario en PDF de caudales y niveles (<https://www.saltogrande.org/docs/hidrologia/CaudalesNiveles.pdf>), con los caudales de la represa, los volúmenes de Machadinho e Itá y lecturas de niveles que completan días sin dato del INA.

Qué dice: el sitio (<https://www.saltogrande.org/>) solo muestra «© 2013 Comisión Técnica Mixta de Salto Grande». No publica términos de reutilización. El directorio `/docs/hidrologia/` devuelve 403.

No queda claro.

Qué publica el sitio: sus lecturas de niveles en los archivos de lecturas crudas y en las medias diarias, donde completan días sin dato del INA, con la cita «Fuente: CTM Salto Grande, parte diario de caudales y niveles» y el link. La página del río muestra los datos del día de la represa. Es un criterio del proyecto, no un permiso de la CTM. Si la CTM pide retirarlos, se saca `"ctm"` de `FULL_SERIES_SOURCES` y los meses cerrados que los incluyen se borran a mano. Pedido: <https://github.com/sebastian-reolon/clima-uy/issues>.

## GloFAS vía Open-Meteo

Qué se usa: la API de crecidas de Open-Meteo (<https://open-meteo.com/en/docs/flood-api>), que sirve los pronósticos de caudal del Global Flood Awareness System (GloFAS), del Servicio de Gestión de Emergencias de Copernicus (CEMS).

Qué dice Open-Meteo (<https://open-meteo.com/en/licence>): «API data are offered under Attribution 4.0 International (CC BY 4.0)». Pide un link al lado de donde se muestran los datos, por ejemplo «Weather data by Open-Meteo.com» con link a <https://open-meteo.com/>. Los términos de uso (<https://open-meteo.com/en/terms>) dicen que la API gratuita es solo para uso no comercial; cuentan como no comerciales los sitios sin suscripciones ni publicidad. Este sitio no tiene publicidad ni suscripciones y tiene que seguir así para usar la API gratuita.

Qué dice Copernicus: Open-Meteo enlaza para GloFAS a la licencia CEMS-FLOODS (<https://ewds.climate.copernicus.eu/licences/cems-floods>). Permite reproducir, distribuir, comunicar al público y adaptar los datos. Pide que quien los distribuya o los muestre ponga el aviso «Generated using Copernicus Emergency Management Service information [año]» y, si los datos se modificaron, «Contains modified Copernicus Emergency Management Service information [año]». El sitio calcula medianas y bandas, así que corresponde el segundo. La licencia también dice que los datos de GloFAS son solo informativos y no son una alerta de crecida, porque eso lo emiten solo las instituciones nacionales o regionales; el pie de todas las páginas ya dice que el sitio no es una alerta oficial.

Qué publica el sitio: los pronósticos completos por mes, con los dos avisos.

Cada pie de foto de los pronósticos de GloFAS en la página del río y la sección de licencias de la página de datos llevan el link «Weather data by Open-Meteo.com» a <https://open-meteo.com/> y el aviso «Contains modified Copernicus Emergency Management Service information» con el año.

## NOAA CPC (Climate Prediction Center)

Qué se usa: los índices ONI, RONI y Niño 3.4 semanal (<https://www.cpc.ncep.noaa.gov/data/indices/>).

Qué dice: el CPC es parte del Servicio Meteorológico Nacional de Estados Unidos, y su portada enlaza al aviso legal del servicio (<https://www.weather.gov/disclaimer>). La dirección `https://www.cpc.ncep.noaa.gov/disclaimer.php` devuelve 404. El aviso dice que la información «is in the public domain, unless specifically noted otherwise, and may be used without charge for any lawful purpose», siempre que no se la presente como propia, no se insinúe que la NOAA respalda el uso, y no se la modifique y se la presente como información oficial. Como obra del gobierno federal, no tiene derechos de autor en Estados Unidos (17 U.S.C. § 105).

Qué publica el sitio: los índices completos, con la cita «Fuente: NOAA Climate Prediction Center». El sitio no se presenta como fuente oficial.

## INUMET

No se descargan datos del INUMET. El panorama trimestral de la portada (`content/enso_outlook.json`) transcribe a mano las probabilidades que publicó el INUMET, con su fuente y su link. No se ofrece como descarga y no se investigó su licencia en esta tarea.

## Mapas de ciudad: terreno, población, calles y segmentos censales

Los mapas de ciudad se arman una vez, fuera del sitio, con `python -m climauy mapa-pack <ciudad>`. Lo que queda en el repositorio es `content/mapa/<ciudad>/pack.json.gz`: polígonos del agua cada 25 cm, cuántas personas viven debajo, y las capas del plano. Las descargas crudas no se publican.

### IDE Uruguay, Modelo Digital del Terreno

Qué se usa: el MDT de 1 m por ciudad de la Infraestructura de Datos Espaciales, por ejemplo el de Paysandú (<https://visualizador.ide.uy/descargas/datos/CU_Remesa_09/01_MDT_MDS/08_Ciudad_PSD/04_MDT_GeoTIFF_Resolucion_100cm/>, 87 hojas).

Qué dice: los datos se publican bajo la Licencia de Datos Abiertos de Uruguay, versión 0.1 (<https://visualizador.ide.uy/descargas/datos/00_Licencia_de_datos_abiertos.pdf>). Todo uso tiene que citar al proveedor, la licencia y el conjunto de datos, y decir qué se modificó.

Qué publica el sitio: no publica el terreno, solo lo que sale de él (el agua a cada altura y las curvas de nivel), con la cita «Fuente: IDE Uruguay, MDT ciudad de Paysandú 1 m (CU_Remesa_09), Licencia de Datos Abiertos de Uruguay; remuestreado a 2 m y procesado».

### GFM, Global Flood Monitoring (Copernicus)

Qué se usa: la extensión de agua que ve el radar de Sentinel-1, del servicio GFM del Servicio de Gestión de Emergencias de Copernicus (CEMS), consultada en <https://stac.eodc.eu/api/v1>.

Qué dice: los términos del GFM (<https://extwiki.eodc.eu/GFM/Terms_and_conditions>) dicen que los datos siguen los términos de servicio del CEMS, son de acceso libre y completo y se pueden usar incluso con fines comerciales «as long as the source of the data (CEMS) is acknowledged». La licencia CC BY 4.0 que aparece en el manual del GFM cubre el documento, no los datos.

Qué publica el sitio: los polígonos de agua de la última pasada sobre la ciudad, con la cita «Contiene información modificada del Copernicus Emergency Management Service (GFM)» y la fecha de la pasada.

### WorldPop

Qué se usa: la población 2026 de Uruguay en celdas de 100 m, versión restringida a zonas construidas, release R2025A (<https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/2026/URY/v1/100m/constrained/ury_pop_2026_CN_100m_R2025A_v1.tif>).

Qué dice: CC BY 4.0 (<https://hub.worldpop.org/data/licence.txt>).

Qué publica el sitio: no publica la grilla, solo cuántas personas quedan debajo de cada altura, con la cita «Población: WorldPop (CC BY 4.0)».

### OpenStreetMap

Qué se usa: calles, cuerpos de agua y nombres de barrios del extracto de Uruguay de Geofabrik (<https://download.geofabrik.de/south-america/uruguay-latest.osm.pbf>).

Qué dice: ODbL (<https://www.openstreetmap.org/copyright>). Pide la mención «© OpenStreetMap contributors» y que una base de datos derivada que se publique quede bajo la misma licencia.

Qué publica el sitio: manzanas, calles, el río y los nombres de barrio dentro del plano, con la mención «© colaboradores de OpenStreetMap» y el link. Las capas del plano en `pack.json.gz` son una base derivada y quedan bajo ODbL.

### INE, segmentos censales 2011

Qué se usa: los segmentos censales del Censo 2011 (<https://www5.ine.gub.uy/documents/Cartograf%C3%ADa/Vectoriales/2011/mapas%20vectoriales%202011.zip>, capa `ine_seg_11`), para repartir a las personas por zona.

Qué dice: los metadatos de la capa dicen «Restricciones de uso: libre, citando como fuente al INE».

Qué publica el sitio: no publica los segmentos, solo la tabla de personas por zona, con la cita «Fuente: INE, segmentos censales 2011». Cada segmento se nombra por el barrio de OpenStreetMap más cercano, que no es un límite oficial.
