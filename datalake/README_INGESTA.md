# README_INGESTA — Ingesta del Data Lake (Medallón)

Este documento describe **dónde dejar los archivos reales**, **qué formatos se
aceptan**, **qué modos de ejecución existen** y **qué escribe** el script
[`ingesta_datalake.py`](./ingesta_datalake.py) en Bronze, Silver, Gold,
PostgreSQL y MongoDB.

> El proyecto es **Data-Driven**: la oferta y la malla entran por el Data Lake y
> desde allí se alimentan las bases operativas. No se usan datos ficticios.

---

## 1. Archivos de entrada reales

Deje los archivos en `datalake/entrada/` (esa carpeta está montada en Docker):

| Archivo | Contenido | Uso |
|---|---|---|
| `datalake/entrada/chorarios_26082026.xlsx` | Oferta académica: **4.869 filas de datos × 43 columnas** | `--excel`, modo oferta |
| `datalake/entrada/ruta_nueva_isum_v2.pdf` | Malla de Ingeniería de Sistemas: **1 página escaneada sin capa de texto** | `--pdf`, modo malla |
| `datalake/malla_curricular.json` | Transcripción asistida de la malla (fallback del PDF escaneado) | fallback del modo malla |

Rutas por defecto:

```text
datalake/entrada/chorarios_26082026.xlsx
datalake/entrada/ruta_nueva_isum_v2.pdf
datalake/malla_curricular.json
```

Si no se pasan `--excel`/`--pdf`, el script busca esos nombres y, si no están,
el primer `*.xlsx` / `*.pdf` de `datalake/entrada/`. Los nombres de los archivos
reales pueden cambiar: pase la ruta explícita.

---

## 2. Arquitectura Medallón

```text
datalake/
├── entrada/                 <- archivos que deja el Admin
├── bronze/                  <- copia 1:1 con timestamp
├── silver/                  <- Parquet limpio y tipado
├── gold/                    <- agregados analíticos
├── malla_curricular.json    <- fallback de la malla escaneada
├── ingesta_datalake.py
└── README_INGESTA.md
```

| Capa | Qué hace | Salidas |
|---|---|---|
| **Bronze** | Copia cruda de cada archivo procesado, sin transformarlo | `bronze/oferta_<timestamp>.xlsx`, `bronze/malla_<timestamp>.pdf`, `bronze/malla_curricular_<timestamp>.json` |
| **Silver** | Limpieza, normalización y tipado | `oferta.parquet`, `horario.parquet`, `malla.parquet`, `docentes.parquet`, `sedes.parquet` |
| **Gold** | Agregados por curso/sede/jornada y por semestre | `oferta_resumen.parquet`, `malla_resumen.parquet` |
| **PostgreSQL** | Upsert idempotente de la oferta | `curso`, `docente`, `sede`, `nrc`, `horario` |
| **MongoDB** | Malla y reglas de prerrequisitos | `inscripcion.malla_curricular`, `inscripcion.reglas_negocio` |

Cada corrida **sobrescribe** los Parquet de las capas que procesa. Los archivos
de la capa que no se procesa (por ejemplo, `malla.parquet` en
`--solo-oferta`) **no se tocan**.

---

## 3. Requisitos e instalación

Se requiere **Python 3.8 o superior**.

```bash
pip install pandas openpyxl pyarrow pdfplumber psycopg2-binary pymongo
```

Opcional para OCR del PDF escaneado:

```bash
pip install pytesseract pillow pypdfium2
# Binario del sistema (Fedora):
sudo dnf install tesseract tesseract-langpack-spa
```

| Dependencia | ¿Obligatoria? | Para qué |
|---|---|---|
| `pandas` + `pyarrow` | Sí, para procesar | Leer/limpiar y escribir Parquet |
| `openpyxl` | Sí, para Excel | Motor de `pandas.read_excel` |
| `pdfplumber` | Recomendada | Intentar extraer texto del PDF |
| `psycopg2-binary` | Solo modos con PostgreSQL | Carga de oferta |
| `pymongo` | Solo modos con MongoDB | Malla/reglas |
| `pytesseract` + `pillow` + binario `tesseract` | Opcional | OCR si el PDF no tiene texto |

`--sin-bd` no se conecta ni usa `psycopg2` ni `pymongo`. Si falta `pdfplumber`, el
modo malla sigue funcionando con el fallback JSON.

> El contenedor del backend ya instala `pandas`, `pyarrow`, `psycopg2-binary` y
> `pymongo`. Para ejecutar el modo Excel dentro del contenedor agregue
> `openpyxl`; para el modo malla agregue `pdfplumber` (o ejecute la ingesta en
> un entorno virtual con todas las dependencias).

---

## 4. Modos de ejecución (nombres exactos)

### 4.1 Solo oferta

```bash
python3 datalake/ingesta_datalake.py \
  --excel datalake/entrada/chorarios_26082026.xlsx \
  --solo-oferta
```

- Procesa **solo** el Excel.
- Escribe Bronze/Silver/Gold de oferta y carga PostgreSQL.
- **No lee ni toca** `malla.parquet`, MongoDB ni la malla.
- Es el modo que debe usar el backend para `POST /api/admin/upload-oferta`.

### 4.2 Solo malla

```bash
python3 datalake/ingesta_datalake.py \
  --pdf datalake/entrada/ruta_nueva_isum_v2.pdf \
  --solo-malla
```

- Procesa **solo** el PDF (con fallback JSON).
- Escribe Bronze/Silver malla y carga MongoDB (`malla_curricular` y
  `reglas_negocio`). También escribe `gold/malla_resumen.parquet`.
- **No toca** la oferta ni las tablas operativas de PostgreSQL.

### 4.3 Sin bases de datos

```bash
python3 datalake/ingesta_datalake.py --sin-bd
```

- Procesa lo que exista en `datalake/entrada/`.
- Escribe Bronze/Silver/Gold.
- No se conecta a PostgreSQL ni a MongoDB.

Se puede combinar con un modo:

```bash
python3 datalake/ingesta_datalake.py --excel <ruta> --solo-oferta --sin-bd
python3 datalake/ingesta_datalake.py --pdf <ruta> --solo-malla --sin-bd
```

### 4.4 Sin flags (ambos)

```bash
python3 datalake/ingesta_datalake.py
```

- Busca el Excel y el PDF por defecto en `datalake/entrada/`.
- Si existen ambos, procesa oferta + malla.
- Si solo existe uno, procesa ese y avisa del faltante.
- Si no existe ninguno, termina con error claro y código de salida `2`.

### 4.5 Flags auxiliares

| Flag | Efecto |
|---|---|
| `--malla-json <ruta>` | Cambia el JSON de fallback (default `datalake/malla_curricular.json`) |
| `--periodo <texto>` | Sobrescribe el periodo leído del Excel/CSV (ej. `202660`) |
| `--debug` | Muestra la traza completa ante un error inesperado |

`--solo-oferta` y `--solo-malla` son mutuamente excluyentes.

---

## 5. Formato del Excel de oferta

El archivo real tiene **43 columnas**:

```text
PERIODO, NRC, MODO_CALIFICAR, SECUENCIA, LISTA_CRUZADA, ALFA, NUM,
ESTADO_NRC, TITULO, HORAS_MINIMAS, HORAS_MAXIMAS, CONECTOR,
HORAS_CREDITOS_SEDE, INTENSIDAD_HORARIA, RECTORIA, DESC_RECTORIA, SEDE,
DESC_SEDE, FACULTAD_RESPONSABLE, DEPARTAMENTO_RESPONSABLE, CUPO, INSCRITOS,
SALDO, ID_DOCENTE, IDENTIFICACION, NOMBRE_DOCENTE, PARTE_PERIODO, EDIFICIO,
SALON, FECHA_INICIAL_1, FECHA_FINAL_1, HI, HF, L, M, I, J, V, S, D,
TIPO_HORARIO, METODO_EDUCATIVO, RESTRICCION_SEDE
```

Los nombres se normalizan (mayúsculas/minúsculas, sin BOM, espacios y guiones a
`_`). Las columnas que no se usan se ignoran.

### 5.1 Reglas de limpieza

| Dato | Regla |
|---|---|
| `ESTADO_NRC` | Se conservan **solo** las filas `Activo` (se comparan sin acentos ni mayúsculas). Las demás se cuentan como inactivas. |
| `codigo_curso` | `ALFA + NUM`, sin espacios y en MAYÚSCULAS. Ej.: `ALFA='ISUM'`, `NUM='BG042'` → `ISUMBG042`. |
| `NRC` | Entero. Un NRC con más de un curso se descarta con aviso. |
| `nombre_curso` | `TITULO`. Si viene vacío se usa el código como marcador y se advierte. |
| `creditos` | `HORAS_CREDITOS_SEDE` → `HORAS_MINIMAS` → `HORAS_MAXIMAS` → `INTENSIDAD_HORARIA` → `0`. El fallback está documentado; no se inventan créditos. |
| `horas` | `HORAS_MINIMAS` → `HORAS_MAXIMAS` → `INTENSIDAD_HORARIA` → nulo. |
| `jornada` | Se deriva de la **sesión más temprana** del NRC: `HI < 12:00` → `Diurna`; `12:00–17:59` → `Mixta`; `>= 18:00` → `Nocturna`; sin `HI` → `Virtual`. |
| `modalidad` | `METODO_EDUCATIVO`: `TRAD` → `Presencial`, `DIST` → `Distancia`, `VIRT` → `Virtual`. Si no hay `HI`, `Virtual`. |
| `cupo_max`, `inscritos`, `saldo` | Enteros; si faltan se usa `0`. |
| `sede_id` / `sede_nombre` | `SEDE` y `DESC_SEDE`, con el mapa canónico de la sección 5.3. |
| `docente_id` / `docente_nombre` | `ID_DOCENTE` / `NOMBRE_DOCENTE`. El ID se guarda como texto. |
| `programa` / `semestre` | Se completan desde `malla.parquet` cuando el código coincide (modo sin flags o cuando se procesan ambos). En `--solo-oferta` quedan nulos. |

### 5.2 Días y horas

Las columnas de día son `L`, `M`, `I`, `J`, `V`, `S`, `D` (en el Excel real
están físicamente después de `HF`). **Si la celda no está vacía, hay sesión ese
día**; el valor (`M`, `T`, `W`, `R`, `F`, `S`, `U`) es solo un marcador y no se
interpreta.

| Columna | Día |
|---|---|
| `L` | 1 — Lunes |
| `M` | 2 — Martes |
| `I` | 3 — Miércoles |
| `J` | 4 — Jueves |
| `V` | 5 — Viernes |
| `S` | 6 — Sábado |
| `D` | 7 — Domingo |

- `HI`/`HF` vienen como HHMM (`915` → `09:15:00`, `2030` → `20:30:00`).
  También se aceptan `HH:MM` y `HH:MM:SS`.
- Si `HI`/`HF` están **vacíos**, la sesión es virtual/asincrónica: **no se crea
  fila de horario**. El NRC sí queda en `oferta.parquet` con `jornada=Virtual`.
- Si hay día pero falta una hora válida, la sesión se omite con aviso.
- `numero_sesion` se numera por NRC (1, 2, 3…) ordenando por día y hora de
  inicio. Las filas exactamente duplicadas se eliminan.
- Un NRC puede tener hasta 4 sesiones en el archivo real.

### 5.3 Sedes canónicas

| `SEDE` | `DESC_SEDE` canónica |
|---|---|
| `UMD` | `ENGATIVA PRESENCIAL` |
| `CBP` | `CD BOLIVAR PRESENCIAL` |
| `VGA` | `VICERECTORIA GENERAL ACADEMICA` |
| `RUP` | `SANTAFE PRESENCIAL` |

Si aparece un código desconocido se conserva el `DESC_SEDE` del archivo (o el
código si también falta la descripción).

---

## 6. Malla desde el PDF escaneado

El PDF real es un **escaneo Xerox sin capa de texto** (verificado con
`pdftotext`: solo el carácter de salto de página). El script aplica esta
cascada:

1. **pdfplumber**: intenta `extract_text()` en todas las páginas.
2. **Parser de texto** (best-effort): si hay texto, busca líneas con
   semestre/código/nombre/créditos/horas. Si no logra al menos 5 cursos,
   descarta el resultado.
3. **OCR opcional**: si existen `pytesseract`, `Pillow`, `pypdfium2` y el
   binario `tesseract`, rasteriza a 300 dpi y aplica OCR (español; si falta el
   idioma, usa el idioma por defecto). Si no está disponible, lo informa.
4. **Fallback JSON**: `datalake/malla_curricular.json`.

El fallback JSON tiene esta forma:

```json
{
  "programa": "INGENIERÍA DE SISTEMAS",
  "sede": "BOGOTÁ",
  "fuente": "Ruta nueva ISUM V2 (2).pdf",
  "nota": "Transcripción asistida...",
  "cursos": [
    {
      "semestre": 2,
      "codigo": "ISUMBG023",
      "nombre": "Programación Orientada a Objetos",
      "creditos": 3,
      "horas": 4,
      "prerrequisitos": ["ISUMBG013"]
    }
  ]
}
```

- `prerrequisitos` puede ser lista o texto separado por comas.
- Los códigos se normalizan a MAYÚSCULAS y se deduplican por `codigo`.
- Si el PDF no tiene texto y el JSON no existe, el script **falla con error
  claro**; no inventa cursos.
- El JSON de fallback se copia a Bronze con timestamp para dejar trazabilidad.

Con los archivos reales, el fallback produce **59 cursos** repartidos en **10
semestres**.

---

## 7. Contrato de los Parquet (Silver)

### `silver/oferta.parquet` — una fila por NRC

| Columna | Tipo | Descripción |
|---|---|---|
| `periodo` | texto | `PERIODO` (ej. `202660`) |
| `id_nrc` | Int64 | `NRC` |
| `codigo_curso` | texto | `ALFA + NUM` |
| `nombre_curso` | texto | `TITULO` |
| `creditos` | Int64 | Créditos (con fallback documentado) |
| `jornada` | texto | `Diurna` / `Mixta` / `Nocturna` / `Virtual` |
| `modalidad` | texto | `Presencial` / `Distancia` / `Virtual` |
| `cupo_max` | Int64 | `CUPO` |
| `inscritos` | Int64 | `INSCRITOS` |
| `saldo` | Int64 | `SALDO` |
| `estado` | texto | `Activo` |
| `sede_id` | texto | `UMD` / `CBP` / `VGA` / `RUP` |
| `sede_nombre` | texto | Nombre canónico |
| `docente_id` | texto | `ID_DOCENTE` |
| `docente_nombre` | texto | `NOMBRE_DOCENTE` |
| `programa` | texto | Se completa desde la malla si hay match |
| `semestre` | Int64 | Se completa desde la malla si hay match |
| `horas` | Int64 | Horas del curso |

### `silver/horario.parquet` — una fila por sesión

| Columna | Tipo | Descripción |
|---|---|---|
| `id_nrc` | Int64 | FK a la oferta |
| `numero_sesion` | Int64 | 1..n por NRC |
| `dia` | Int64 | 1=Lunes … 7=Domingo |
| `hora_inicio` | texto | `HH:MM:SS` |
| `hora_fin` | texto | `HH:MM:SS` |
| `salon` | texto | `SALON` |
| `edificio` | texto | `EDIFICIO` |

### `silver/malla.parquet` — una fila por curso

| Columna | Tipo | Descripción |
|---|---|---|
| `programa` | texto | Programa de la malla |
| `sede` | texto | Sede de la malla |
| `semestre` | Int64 | Semestre del plan |
| `codigo` | texto | Código del curso |
| `nombre` | texto | Nombre |
| `creditos` | Int64 | Créditos |
| `horas` | Int64 | Horas |
| `prerrequisitos` | texto JSON | Lista serializada, ej. `["ISUMBG013"]` |

### `silver/docentes.parquet` y `silver/sedes.parquet`

| Archivo | Columnas |
|---|---|
| `docentes.parquet` | `id_docente` (texto), `nombre` (texto) |
| `sedes.parquet` | `codigo` (texto), `nombre` (texto) |

Los docentes sin `ID_DOCENTE` no se inventan: simplemente no aparecen en
`docentes.parquet` y `nrc.docente_id` queda nulo. En el archivo real hay
**840 docentes únicos** y **4 sedes**.

---

## 8. Capa Gold

### `gold/oferta_resumen.parquet`

Agrupa por `codigo_curso`, `sede_id` y `jornada`:

| Columna | Descripción |
|---|---|
| `codigo_curso`, `nombre_curso` | Curso |
| `sede_id`, `sede_nombre` | Sede |
| `jornada` | Jornada |
| `nrcs` | NRC distintos |
| `cupo_total`, `inscritos_total`, `saldo_total` | Sumas |
| `ocupacion` | `inscritos_total / cupo_total` (0 si el cupo es 0), redondeada a 4 decimales |
| `programa`, `semestre` | Desde la malla si hay match |

### `gold/malla_resumen.parquet`

Agrupa por `programa`, `sede` y `semestre`:

| Columna | Descripción |
|---|---|
| `num_cursos` | Cursos distintos |
| `creditos_total` | Suma de créditos |
| `horas_total` | Suma de horas |
| `cursos` | JSON con los códigos ordenados |

---

## 9. PostgreSQL

### 9.1 Tablas y migraciones

El script crea las tablas si faltan y agrega columnas con
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, por lo que es compatible con el
esquema base de `schema.sql` / `backend/init_db.py`.

```sql
curso (
    codigo_alfanumerico VARCHAR(10) PRIMARY KEY,
    nombre              TEXT NOT NULL,
    creditos            INTEGER NOT NULL,
    semestre            INTEGER,
    programa            TEXT,
    horas               INTEGER
)

docente (
    id_docente TEXT PRIMARY KEY,
    nombre     TEXT
)

sede (
    codigo VARCHAR(10) PRIMARY KEY,
    nombre TEXT NOT NULL
)

nrc (
    id_nrc      INTEGER PRIMARY KEY,
    curso_id    VARCHAR(10) NOT NULL REFERENCES curso,
    periodo     TEXT,
    docente_id  TEXT REFERENCES docente,
    sede_id     VARCHAR(10) REFERENCES sede,
    jornada     TEXT NOT NULL,
    modalidad   TEXT NOT NULL,
    cupo_max    INTEGER NOT NULL,
    inscritos   INTEGER DEFAULT 0,
    saldo       INTEGER DEFAULT 0,
    estado      TEXT DEFAULT 'Activo'
)

horario (
    id            SERIAL PRIMARY KEY,
    nrc_id        INTEGER NOT NULL REFERENCES nrc,
    numero_sesion INTEGER NOT NULL,
    dia           INTEGER NOT NULL,
    hora_inicio   TIME NOT NULL,
    hora_fin      TIME NOT NULL,
    salon         TEXT,
    edificio      TEXT
)
```

### 9.2 Estrategia idempotente

| Tabla | Estrategia |
|---|---|
| `curso` | `INSERT ... ON CONFLICT (codigo_alfanumerico) DO UPDATE` |
| `docente` | `INSERT ... ON CONFLICT (id_docente) DO UPDATE` |
| `sede` | `INSERT ... ON CONFLICT (codigo) DO UPDATE` |
| `nrc` | `INSERT ... ON CONFLICT (id_nrc) DO UPDATE` |
| `horario` | `DELETE FROM horario WHERE nrc_id = ANY(...)` + `INSERT` de las sesiones de los NRC cargados |

- **No se borran** `nrc` que ya no vienen en el Excel: podrían tener
  inscripciones.
- **No se borra** `inscripcion` ni `historial`; el reemplazo de `horario` es
  seguro porque nada referencia `horario.id`.
- Todo ocurre en **una transacción**: ante error se hace `ROLLBACK`.
- Ejecutar dos veces el mismo archivo deja los mismos conteos y no duplica
  filas. El `id` SERIAL de `horario` se regenera al reemplazar sesiones.

### 9.3 Variables de entorno

```bash
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=universidad
export DB_USER=postgres
export DB_PASSWORD=postgres
export DB_TIMEOUT=8          # segundos de conexión
```

Los valores por defecto son los de `docker-compose.yml`.

---

## 10. MongoDB (`inscripcion`)

Variables:

```bash
export MONGO_URI=mongodb://localhost:27017
export MONGO_DB=inscripcion
export MONGO_TIMEOUT_MS=5000
```

### 10.1 `malla_curricular`

Un documento por curso, con clave única `{programa, codigo}`:

```json
{
  "programa": "INGENIERÍA DE SISTEMAS",
  "sede": "BOGOTÁ",
  "semestre": 2,
  "codigo": "ISUMBG023",
  "nombre": "Programación Orientada a Objetos",
  "creditos": 3,
  "horas": 4,
  "prerrequisitos": ["ISUMBG013"],
  "creado_en": "2026-11-12T...",
  "actualizado_en": "2026-11-12T..."
}
```

- Índice único: `uq_malla_programa_codigo` sobre `{programa:1, codigo:1}`.
- Upsert con `$set` de los campos de la malla y `$setOnInsert` de `creado_en`.

### 10.2 `reglas_negocio`

Un documento por materia, con clave única `{materia}`:

```json
{
  "materia": "ISUMBG023",
  "prerrequisitos": ["ISUMBG013"],
  "nombre": "Programación Orientada a Objetos",
  "creditos": 3,
  "semestre": 2,
  "programa": "INGENIERÍA DE SISTEMAS",
  "creado_en": "2026-11-12T..."
}
```

- Índice único: `uq_reglas_materia` sobre `{materia:1}`.
- **No sobrescribe ediciones del Admin**: usa `$setOnInsert`. Si la regla ya
  existe, se conserva tal cual (aunque el JSON de malla tenga otros
  prerrequisitos). Solo se insertan las materias que faltan.

Si los índices únicos no se pueden crear porque ya hay duplicados, el script
falla con un mensaje que indica limpiar la colección. No borra documentos.

---

## 11. `RESUMEN_JSON` (contrato con el backend)

Al final de una corrida exitosa se imprime **una sola línea**:

```text
RESUMEN_JSON: {"cursos": 888, "nrcs": 3288, "sesiones": 3852, ...}
```

Ejemplo real (modo completo con los archivos de `datalake/entrada/`):

```json
{
  "modo": "completo",
  "cursos": 888,
  "nrcs": 3288,
  "sesiones": 3852,
  "docentes": 840,
  "sedes": 4,
  "malla_cursos": 59,
  "malla_semestres": 10,
  "periodo": "202660",
  "insertados": {
    "postgres": {
      "cursos": 888,
      "docentes": 840,
      "sedes": 4,
      "nrcs": 3288,
      "horarios": 3852,
      "horarios_borrados": 0
    },
    "mongo": {
      "malla_insertados": 59,
      "malla_actualizados": 0,
      "reglas_insertados": 59,
      "reglas_existentes": 0
    }
  }
}
```

El backend actual (`backend/main.py`) solo necesita `cursos`, `nrcs` y
`sesiones`; el resto es informativo. En `--sin-bd`, `insertados` va vacío.

---

## 12. Compatibilidad con los CSV históricos

El backend actual llama a la ingesta con `--oferta <csv> --solo-oferta` para
subir la oferta en formato CSV. Por eso se conservan:

### 12.1 `--oferta <ruta>`

- Si la ruta termina en `.csv`, se procesa el **formato histórico** (una fila
  por sesión) con las columnas:

  ```text
  codigo_curso, nombre_curso, creditos, id_nrc, jornada, modalidad,
  cupo_max, numero_sesion, dia, hora_inicio, hora_fin
  ```

  Se admiten además `periodo`, `sede_id`, `sede_nombre`, `docente_id`,
  `docente_nombre`, `inscritos`, `saldo`, `estado`, `semestre`, `programa` y
  `horas` como opcionales.

- Si la ruta termina en `.xlsx`, se procesa con el parser de Excel de la
  sección 5.

### 12.2 `--matriz <ruta>` / `--solo-matriz`

Modo legado para la matriz histórica de estudiantes. Escribe
`historial.parquet`, `matriz_por_ver.parquet` y `estudiante_perfil.parquet`, y
(opcionalmente) carga las tablas `historial`, `estudiante_perfil` y
`matriz_por_ver`. Acepta el formato antiguo (`estudiante_id,codigo_curso`) y el
enriquecido (`estudiante_id,carrera,semestre,estado,codigo_curso,nombre_curso`).

El backend nuevo ya no usa este modo (gestiona la matriz desde la API), pero se
mantiene para no romper scripts existentes.

---

## 13. Verificación

Sintaxis y ayuda:

```bash
python3 -m py_compile datalake/ingesta_datalake.py
python3 datalake/ingesta_datalake.py --help
```

Prueba sin bases de datos:

```bash
python3 datalake/ingesta_datalake.py --sin-bd
```

Salida esperada con los archivos reales:

```text
oferta.parquet           OK (3,288 filas)
horario.parquet          OK (3,852 filas)
docentes.parquet         OK (840 filas)
sedes.parquet            OK (4 filas)
oferta_resumen.parquet   OK (1,681 filas)
malla.parquet            OK (59 filas)
malla_resumen.parquet    OK (10 filas)
```

Comprobar los Parquet:

```bash
python3 - <<'PY'
import pandas as pd
for nombre in ("oferta", "horario", "malla", "docentes", "sedes"):
    df = pd.read_parquet(f"datalake/silver/{nombre}.parquet")
    print(nombre, df.shape, list(df.columns))
PY
```

Prueba con bases de datos (use una base de prueba, no la operativa):

```bash
DB_NAME=universidad_test MONGO_DB=inscripcion_test \
  python3 datalake/ingesta_datalake.py
```

---

## 14. Solución de problemas

| Mensaje / síntoma | Causa y solución |
|---|---|
| `Faltan dependencias para ... pandas y pyarrow` | `pip install pandas pyarrow openpyxl` |
| `Falta openpyxl` | `pip install openpyxl` |
| `No se encontró el Excel de oferta` | Deje el archivo en `datalake/entrada/` o pase `--excel <ruta>` |
| `No se encontró el PDF de malla` | Deje el archivo en `datalake/entrada/` o pase `--pdf <ruta>` |
| `El PDF no tiene capa de texto ... no existe el fallback JSON` | Cree `datalake/malla_curricular.json` o instale OCR (`pytesseract` + `tesseract`) |
| `OCR no disponible: el binario 'tesseract' no está en PATH` | `sudo dnf install tesseract tesseract-langpack-spa` (o el equivalente de su distro) |
| `No se pudo conectar a PostgreSQL` | Revise `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER`/`DB_PASSWORD` o use `--sin-bd` |
| `No se pudo conectar a MongoDB` | Revise `MONGO_URI`/`MONGO_DB` o use `--sin-bd` |
| `No se pudo crear el índice único ...` | Hay documentos duplicados previos en la colección; límpielos y reintente |
| `El Excel no produjo NRC activos válidos` | Revise `ESTADO_NRC` y las columnas `NRC`, `ALFA`, `NUM` |
| `violates foreign key constraint` | No debería ocurrir: el script filtra NRC/horarios huérfanos y crea curso/docente/sede antes de `nrc` |

---

## 15. Notas de alcance

- Este script **no modifica** `backend/` ni `frontend/`.
- No borra historial de estudiantes ni inscripciones.
- No inventa cursos, créditos ni prerrequisitos: cuando un dato falta, aplica
  un fallback documentado o deja el campo nulo, y lo informa por consola.
- El JSON de malla es una transcripción asistida; el Admin puede corregir los
  cursos y las reglas desde el dashboard. Las reglas ya editadas en MongoDB no
  se pisan en corridas posteriores.
