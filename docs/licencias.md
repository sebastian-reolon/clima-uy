# Licencias de las fuentes

Qué permite cada fuente, dónde lo dice y qué publica el sitio de cada una. Todo se consultó el 1 de noviembre de 2026.

El criterio es conservador. Si una fuente dice por escrito que se puede republicar citándola, se publican sus series completas con la cita. Si no dice nada o no queda claro, se publica solo lo derivado (estados, percentiles, tendencia y la frase del día) con el link a la fuente, hasta tener una confirmación por escrito.

Mostrar en una página el último valor de una estación con su fuente y su link es citar un dato, y eso se hace con todas. Lo que cambia según la licencia es si se ofrecen para descargar las series completas: las medias diarias, las lecturas crudas por mes y los pronósticos por mes.

## Resumen

| Fuente | Qué dice | Qué publica el sitio |
|---|---|---|
| ANA (Brasil) | Permite reproducir los datos citando la fuente | Series completas, con la cita |
| INA y Prefectura Naval Argentina | El sistema de datos no publica términos | Recomendado: solo lo derivado y el link |
| CTM Salto Grande | No publica términos de reutilización | Recomendado: solo lo derivado y el link |
| GloFAS vía Open-Meteo | CC BY 4.0 (Open-Meteo) y licencia CEMS-FLOODS (Copernicus), con aviso obligatorio | Series completas, con los dos avisos |
| NOAA CPC | Dominio público | Series completas, con la cita |

La decisión final se toma antes de publicar el repositorio. En el código son dos listas en `climauy/publish.py`: `FULL_SERIES_SOURCES` (fuentes de lecturas) y `FULL_FORECAST_ISSUERS` (fuentes de pronósticos). Hoy las dos incluyen todo. Para aplicar la recomendación de este documento quedan así: `FULL_SERIES_SOURCES = {"ana", "ana_hist"}` y `FULL_FORECAST_ISSUERS = {"glofas"}`. Con eso la página de datos lista solo las series que se descargan y dice qué fuentes van solo como derivado.

Como no todas las fuentes permiten republicar, no se arma el archivo mensual comprimido en GitHub Releases que preveía el plan. Las lecturas por mes de las fuentes permitidas ya quedan en `data/rio/raw/`.

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
- En el portal nacional de datos abiertos (<https://datos.gob.ar/>) no aparece ningún conjunto de datos hidrométricos del INA ni de Prefectura: se buscó por «Instituto Nacional del Agua», «hidrométrica», «alturas ríos» y «prefectura».

No queda claro. Es probable que el INA no tenga problema en que se republiquen sus series citándolo, porque las ofrece en una API pública, pero no lo dice por escrito.

Recomendación: publicar solo lo derivado y el link al sistema del INA, y pedirle al INA por escrito permiso para republicar las series con atribución. Con esa respuesta se agrega `"ina"` a `FULL_SERIES_SOURCES` y a `FULL_FORECAST_ISSUERS`.

Qué implica la recomendación: no se ofrecen para descargar las medias diarias de las estaciones del INA, las lecturas crudas del INA ni los pronósticos del INA. Las páginas siguen mostrando el estado de cada estación, su último valor con la fuente, el percentil y la banda del pronóstico.

## CTM Salto Grande (Comisión Técnica Mixta de Salto Grande)

Qué se usa: el parte diario en PDF de caudales y niveles (<https://www.saltogrande.org/docs/hidrologia/CaudalesNiveles.pdf>), con los caudales de la represa, los volúmenes de Machadinho e Itá y lecturas de niveles que completan días sin dato del INA.

Qué dice: el sitio (<https://www.saltogrande.org/>) solo muestra «© 2013 Comisión Técnica Mixta de Salto Grande». No publica términos de reutilización. El directorio `/docs/hidrologia/` devuelve 403.

No queda claro.

Recomendación: solo lo derivado y el link al parte diario. Qué implica: las lecturas de la CTM no van en los archivos de lecturas crudas, y los días que la CTM completaba en las series del INA no van en las medias diarias. La página del río sigue mostrando los datos de la represa del día, con el link al parte.

## GloFAS vía Open-Meteo

Qué se usa: la API de crecidas de Open-Meteo (<https://open-meteo.com/en/docs/flood-api>), que sirve los pronósticos de caudal del Global Flood Awareness System (GloFAS), del Servicio de Gestión de Emergencias de Copernicus (CEMS).

Qué dice Open-Meteo (<https://open-meteo.com/en/licence>): «API data are offered under Attribution 4.0 International (CC BY 4.0)». Pide un link al lado de donde se muestran los datos, por ejemplo «Weather data by Open-Meteo.com» con link a <https://open-meteo.com/>. Los términos de uso (<https://open-meteo.com/en/terms>) dicen que la API gratuita es solo para uso no comercial; cuentan como no comerciales los sitios sin suscripciones ni publicidad. Este sitio no tiene publicidad ni suscripciones y tiene que seguir así para usar la API gratuita.

Qué dice Copernicus: Open-Meteo enlaza para GloFAS a la licencia CEMS-FLOODS (<https://ewds.climate.copernicus.eu/licences/cems-floods>). Permite reproducir, distribuir, comunicar al público y adaptar los datos. Pide que quien los distribuya o los muestre ponga el aviso «Generated using Copernicus Emergency Management Service information [año]» y, si los datos se modificaron, «Contains modified Copernicus Emergency Management Service information [año]». El sitio calcula medianas y bandas, así que corresponde el segundo. La licencia también dice que los datos de GloFAS son solo informativos y no son una alerta de crecida, porque eso lo emiten solo las instituciones nacionales o regionales; el pie de todas las páginas ya dice que el sitio no es una alerta oficial.

Qué publica el sitio: los pronósticos completos por mes, con los dos avisos.

Pendiente antes de publicar: el pie de foto del pronóstico de GloFAS en la página del río y la sección de pronósticos de la página de datos tienen que llevar el link a Open-Meteo y el aviso «Contains modified Copernicus Emergency Management Service information 2026». Hoy la fuente figura solo en el pie de la página.

## NOAA CPC (Climate Prediction Center)

Qué se usa: los índices ONI, RONI y Niño 3.4 semanal (<https://www.cpc.ncep.noaa.gov/data/indices/>).

Qué dice: el CPC es parte del Servicio Meteorológico Nacional de Estados Unidos, y su portada enlaza al aviso legal del servicio (<https://www.weather.gov/disclaimer>). La dirección `https://www.cpc.ncep.noaa.gov/disclaimer.php` devuelve 404. El aviso dice que la información «is in the public domain, unless specifically noted otherwise, and may be used without charge for any lawful purpose», siempre que no se la presente como propia, no se insinúe que la NOAA respalda el uso, y no se la modifique y se la presente como información oficial. Como obra del gobierno federal, no tiene derechos de autor en Estados Unidos (17 U.S.C. § 105).

Qué publica el sitio: los índices completos, con la cita «Fuente: NOAA Climate Prediction Center». El sitio no se presenta como fuente oficial.

## INUMET

No se descargan datos del INUMET. El panorama trimestral de la portada (`content/enso_outlook.json`) transcribe a mano las probabilidades que publicó el INUMET, con su fuente y su link. No se ofrece como descarga y no se investigó su licencia en esta tarea.
