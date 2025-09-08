# hf_eolus_geo_tools

This repository contains tools to work with STAC catalogs and related geospatial data.

## STAC Geoparquet Browser

The directory `scripts/stac_browser` provides a Docker image that serves a web browser for a STAC catalog containing GeoParquet assets. The browser allows:

- Navigating the catalog hierarchy (catalogs → subcatalogs → collections).
- Rendering GeoParquet assets as interactive maps.
- Visualizing wind vectors, scalar values, or point density.
- Filtering features over time when a timestamp column is available.

### Build the image

```bash
docker build -t stac-browser scripts/stac_browser
```

### Run the container

Mount the catalog directory to `/data` and specify column names through environment variables:

```bash
docker run -p 8501:8501 \
    -v /path/to/catalog:/data \
    -e WIND_DIRECTION_COLUMN=direction \
    -e WIND_SPEED_COLUMN=speed \
    -e SCALAR_COLUMN=value \
    -e TIME_COLUMN=timestamp \
    stac-browser
```

The application will be available at `http://localhost:8501`.

Notes:
- `STAC_PATH` (optional): Path to the STAC root. It may be a file or a directory. If a directory is provided, the app looks for `catalog.json` first and then `collection.json`. If you point it to a directory that is not a STAC root, the app will show a simple filesystem browser so you can drill down to a folder that contains `catalog.json` or `collection.json`. Default inside the Docker image is `/data`.
- `TIME_COLUMN` (optional): Nombre de la columna temporal (date/datetime). La app convierte strings a fechas automáticamente.
- `TIME_MAX_INSTANTS` (optional): Umbral máximo de instantes únicos que se mostrarán directamente en un selector (por defecto `200`). Si se supera, se usa selector de Fecha -> Hora. Este umbral también puede ajustarse desde la barra lateral en tiempo de ejecución.
- `COLORMAP` (optional): Colormap por defecto para mapas escalares y de viento. Valor por defecto `YlOrRd` (brillante sobre fondo oscuro). En la barra lateral puedes elegir entre: `YlOrRd`, `YlGnBu`, `Viridis`, `Plasma`, `Magma`, `Inferno`.
 - `ITEMS_PAGE_SIZE` (optional): Tamaño de página para la lista de Items (por defecto `100`). También ajustable desde la barra lateral.

Visualización:
- Fondo: se usa `CartoDB dark_matter` para alto contraste (océano negro). Las flechas de viento se dibujan en blanco semi-transparente para no tapar el color.
- Leyenda: cada mapa escalar/viento añade una barra de color con la leyenda del campo.

Rendimiento:
- Navegación paginada de Items: el selector de Items carga por páginas para evitar leer miles de Items al iniciar. Configura el tamaño y la página desde la barra lateral.

Ejemplos:
- Si quieres empezar desde un directorio padre (que aún no es STAC) y navegar hasta una colección concreta (p. ej. `.../hf_radial_metrics_aws_ingestion/hf_ingestion/VILA/HF-Radar-VILA1`), monta ese directorio padre en `/data` y deja `STAC_PATH=/data` o la ruta relativa dentro. La app abrirá un explorador de carpetas para que navegues hasta el `catalog.json`/`collection.json` correspondiente.
