# -*- coding: utf-8 -*-
"""
main.py — API REST del Sistema de Inscripción de Materias (FastAPI).

Endpoints
---------
  POST /api/auth/login                   -> login admin/estudiante (registro implícito)
  GET  /api/admin/estudiantes            -> lista de estudiantes (solo lectura)
  GET  /api/oferta                       -> NRCs disponibles (con cupo e inscritos)
  POST /api/inscribir                    -> inscribe un estudiante en un NRC
  GET  /api/horario/{estudiante_id}      -> horario consolidado del estudiante
  GET  /api/estudiante/horario/{id}      -> horario guardado agrupado por NRC
  POST /api/horario/automatico           -> genera y guarda un horario automático
  GET  /api/admin/reglas                 -> lista reglas (prerrequisitos y metadatos)
  PUT  /api/admin/reglas                 -> crea/actualiza reglas de negocio dinámicas
  POST /api/admin/upload-oferta          -> sube Excel/CSV de oferta e ingesta a PostgreSQL
  POST /api/admin/upload-malla           -> sube PDF de malla e ingesta a MongoDB
  GET  /api/admin/malla                  -> cursos de la malla (dashboard editable)
  PUT  /api/admin/malla                  -> upsert de la malla + reglas_negocio
  GET  /api/admin/oferta                 -> NRCs editables (PostgreSQL)
  PUT  /api/admin/oferta                 -> actualiza NRC y horario (transaccional)
  GET  /api/catalogos                    -> programas, jornadas y sedes
  POST /api/estudiante/perfil            -> crea/actualiza perfil + matriz opcional
  GET  /api/estudiante/{id}/perfil       -> resumen del perfil y su matriz
  POST /api/estudiante/upload-matriz     -> (compatibilidad) sube CSV de matriz
  GET  /api/estudiante/{id}/matriz       -> (compatibilidad) resumen de la matriz

  * POST /api/admin/upload-oferta usa el Data Lake: ``--excel ... --solo-oferta``
    para .xlsx/.xlsm y ``--oferta ... --solo-oferta`` para CSV (compatibilidad).
    Refresca curso/nrc/horario y NO toca ``historial`` (aprobados intactos).
  * POST /api/admin/upload-malla usa ``--pdf ... --solo-malla`` y deja la malla
    en MongoDB (``malla_curricular``) y las reglas en ``reglas_negocio``.

Persistencia
------------
  * PostgreSQL vía psycopg2 (tablas: estudiante, curso, nrc, horario,
    inscripcion, historial, estudiante_perfil y matriz_por_ver). Las tablas se
    crearon sin comillas en ``schema.sql``, por lo que PostgreSQL las guarda en
    minúsculas; aquí se referencian en minúsculas.

    La tabla ``historial`` (materias aprobadas por estudiante) la crea el
    módulo de Data Lake (``datalake/ingesta_datalake.py``) con este esquema:

        historial(
            estudiante_id INTEGER NOT NULL,
            codigo_curso  VARCHAR(10) NOT NULL,
            PRIMARY KEY (estudiante_id, codigo_curso)
        )

    La matriz de pendientes vive en::

        matriz_por_ver(
            estudiante_id INTEGER NOT NULL,
            codigo_curso  VARCHAR(10) NOT NULL,
            PRIMARY KEY (estudiante_id, codigo_curso)
        )

  * MongoDB vía pymongo (colecciones ``catalogo_materias`` y ``reglas_negocio``).
    Se configura por variables de entorno MONGO_URI y MONGO_DB.

Ejecución
---------
  uvicorn main:app --host 0.0.0.0 --port 8000
"""

import io
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import time
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Rutas base del contenedor / repo
# ---------------------------------------------------------------------------
# En Docker: /app/main.py -> BASE_DIR="/app".
# En local:  backend/main.py -> BASE_DIR=".../backend".
BASE_DIR = Path(__file__).resolve().parent

logger = logging.getLogger("inscripcion.api")

# ---------------------------------------------------------------------------
# Configuración de conexión — PostgreSQL
# ---------------------------------------------------------------------------
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "universidad"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "postgres"),
}

# ---------------------------------------------------------------------------
# Configuración de conexión — MongoDB
# ---------------------------------------------------------------------------
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "inscripcion")
# Nombres de colección (ajustables por si el seed usa otros nombres).
MONGO_COL_CATALOGO = os.getenv("MONGO_COL_CATALOGO", "catalogo_materias")
MONGO_COL_REGLAS = os.getenv("MONGO_COL_REGLAS", "reglas_negocio")
# Nueva fuente de verdad de la malla (la alimenta POST /api/admin/upload-malla).
MONGO_COL_MALLA = os.getenv("MONGO_COL_MALLA", "malla_curricular")

# Import diferido de pymongo: si no está instalado, la API sigue funcionando
# para los endpoints que solo usan PostgreSQL y los endpoints Mongo devuelven
# un 503 claro.
try:
    import pymongo
    from pymongo.errors import DuplicateKeyError, PyMongoError
    MONGO_DISPONIBLE = True
except ImportError:  # pragma: no cover - entorno sin pymongo
    pymongo = None
    PyMongoError = Exception
    DuplicateKeyError = Exception
    MONGO_DISPONIBLE = False

app = FastAPI(
    title="Sistema de Inscripción de Materias — API",
    version="1.2.0",
)

# ---------------------------------------------------------------------------
# CORS — orígenes permitidos explícitamente.
#
# Se cubren los tres escenarios del proyecto:
#   * React/Vite en desarrollo:  http://localhost:3000 y http://localhost:5173
#   * Frontend dockerizado (Nginx sirviendo la SPA en el puerto 80):
#     http://localhost, http://localhost:80, http://127.0.0.1 y
#     http://127.0.0.1:80
#   * Variantes equivalentes con 127.0.0.1 en los puertos de desarrollo.
#
# Se usa una lista explícita (en lugar de "*") para poder habilitar
# credenciales si algún día se necesitan cookies de sesión. Se pueden añadir
# orígenes extra por entorno con CORS_ORIGINS="https://a,https://b".
# ---------------------------------------------------------------------------
CORS_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost",
    "http://localhost:80",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1",
    "http://127.0.0.1:80",
]

# Orígenes adicionales opcionales (separados por coma), sin duplicados.
for _origen in os.getenv("CORS_ORIGINS", "").split(","):
    _origen = _origen.strip().rstrip("/")
    if _origen and _origen not in CORS_ORIGINS:
        CORS_ORIGINS.append(_origen)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "Accept",
        "Origin",
        "X-Requested-With",
    ],
    expose_headers=["Content-Length"],
    max_age=600,
)

# Nombres legibles de los días (la columna horario.dia es 1..7).
DIA_NOMBRES = {
    1: "Lunes",
    2: "Martes",
    3: "Miércoles",
    4: "Jueves",
    5: "Viernes",
    6: "Sábado",
    7: "Domingo",
}


# ---------------------------------------------------------------------------
# Arranque: sembrado de reglas de negocio (MongoDB)
# ---------------------------------------------------------------------------
def _sembrar_reglas_con_reintentos(intentos=3):
    """
    Siembra OPCIONAL de reglas legacy (28 materias de ejemplo).

    En la arquitectura actual las reglas se cargan desde la **malla real**
    (PDF/JSON) mediante la ingesta del Data Lake, por lo que este seed está
    desactivado por defecto para no contaminar ``reglas_negocio`` con códigos
    que no pertenecen al plan. Se puede activar con ``SEED_REGLAS_LEGACY=1``.

    Reintenta hasta ``intentos`` veces con una espera corta (1s, 2s) porque
    MongoDB puede tardar unos segundos en aceptar conexiones cuando se levanta
    la pila con docker compose. Si el módulo no existe, Mongo no está listo o
    el seed falla, se registra una advertencia y la API arranca igualmente:
    el sembrado de reglas es opcional y nunca debe tumbar el servicio.
    """
    if os.getenv("SEED_REGLAS_LEGACY", "0").strip().lower() not in (
        "1", "true", "yes", "si",
    ):
        logger.info(
            "Seed legacy de 28 materias desactivado: las reglas se cargan "
            "desde la malla real (Data Lake). Usa SEED_REGLAS_LEGACY=1 para "
            "forzarlo."
        )
        return False

    for ruta in (str(BASE_DIR), str(BASE_DIR.parent)):
        if ruta not in sys.path:
            sys.path.append(ruta)

    for intento in range(1, intentos + 1):
        try:
            import importlib

            modulo = importlib.import_module("seed_reglas")
            sembrar = getattr(modulo, "sembrar_reglas", None)
            if not callable(sembrar):
                raise RuntimeError(
                    "seed_reglas no expone una función sembrar_reglas()"
                )
            sembrar()
            logger.info("Reglas de negocio sembradas correctamente (intento %s).", intento)
            return True
        except ModuleNotFoundError as exc:
            # Si falta el propio seed_reglas o una dependencia suya (p. ej.
            # pymongo), reintentar no aporta nada: se avisa y se continúa.
            logger.warning(
                "No se pudo sembrar reglas de negocio (%s). "
                "La API arranca sin seed.",
                exc,
            )
            return False
        except Exception as exc:  # noqa: BLE001 - el arranque nunca debe fallar
            if intento < intentos:
                espera = min(intento, 2)
                logger.warning(
                    "Intento %s/%s de sembrar reglas falló: %s. "
                    "Reintentando en %ss...",
                    intento,
                    intentos,
                    exc,
                    espera,
                )
                time.sleep(espera)
            else:
                logger.warning(
                    "No se pudieron sembrar reglas de negocio tras %s intentos: %s. "
                    "La API arranca sin seed.",
                    intentos,
                    exc,
                )
    return False


def _inicializar_bd_con_reintentos(
    intentos=2, timeout_por_intento=15.0, espera=2.0
):
    """
    Crea/verifica el esquema PostgreSQL al arrancar la API.

    El entrypoint del contenedor ya ejecuta ``python init_db.py`` antes de
    uvicorn (ver backend/Dockerfile y docker-compose.yml); esta llamada es una
    red de seguridad para arranques locales con ``uvicorn main:app`` o si el
    entrypoint no se usó. Es acotada: como máximo ``intentos`` llamadas de
    ``timeout_por_intento`` segundos, así que no bloquea indefinidamente.
    Si PostgreSQL no responde, se registra una advertencia y la API arranca
    igualmente (los endpoints de BD devolverán 503 con un mensaje claro).
    """
    for ruta in (str(BASE_DIR), str(BASE_DIR.parent)):
        if ruta not in sys.path:
            sys.path.append(ruta)

    for intento in range(1, intentos + 1):
        try:
            import importlib

            modulo = importlib.import_module("init_db")
            inicializar = getattr(modulo, "inicializar_bd", None)
            if not callable(inicializar):
                raise RuntimeError(
                    "init_db no expone una función inicializar_bd()"
                )
            if inicializar(timeout=timeout_por_intento):
                logger.info(
                    "Esquema PostgreSQL creado/verificado (intento %s).", intento
                )
                return True
            raise RuntimeError(
                "inicializar_bd() no pudo crear/verificar el esquema"
            )
        except ModuleNotFoundError as exc:
            # Si falta init_db.py o una dependencia (p. ej. psycopg2),
            # reintentar no aporta nada: se avisa y se continúa.
            logger.warning(
                "No se pudo inicializar el esquema PostgreSQL (%s). "
                "La API arranca igualmente; ejecuta 'python init_db.py'.",
                exc,
            )
            return False
        except Exception as exc:  # noqa: BLE001 - el arranque nunca debe fallar
            if intento < intentos:
                logger.warning(
                    "Intento %s/%s de inicializar el esquema PostgreSQL falló: "
                    "%s. Reintentando en %ss...",
                    intento,
                    intentos,
                    exc,
                    espera,
                )
                time.sleep(espera)
            else:
                logger.warning(
                    "No se pudo inicializar el esquema PostgreSQL tras %s "
                    "intentos: %s. La API arranca igualmente; ejecuta "
                    "'python init_db.py'.",
                    intentos,
                    exc,
                )
    return False


@app.on_event("startup")
def _startup_sembrar_reglas():
    """Hook de arranque tolerante a fallos (PostgreSQL/Mongo pueden no estar listos)."""
    # Primero el esquema (tablas base + auxiliares de la matriz), luego el seed.
    _inicializar_bd_con_reintentos()
    _sembrar_reglas_con_reintentos()


# ---------------------------------------------------------------------------
# Utilidades de base de datos — PostgreSQL
# ---------------------------------------------------------------------------
def conectar():
    """
    Abre una conexión con cursor tipo dict (RealDictCursor).

    Si PostgreSQL no está levantado (o las credenciales son incorrectas) se
    devuelve un 503 con un mensaje claro en lugar de un 500 genérico.
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError as exc:  # pragma: no cover - entorno sin psycopg2
        raise HTTPException(
            status_code=503,
            detail="psycopg2 no está instalado. Ejecuta: pip install psycopg2-binary",
        ) from exc

    try:
        return psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=503,
            detail=f"No se pudo conectar a PostgreSQL: {exc}",
        ) from exc


def horarios_por_nrc():
    """
    Devuelve {nrc_id: [sesiones...]} con las sesiones de cada NRC.

    Cada sesión incluye ``numero_sesion``, ``dia``, ``hora_inicio``,
    ``hora_fin`` y (si existen) ``salon`` y ``edificio``.
    """
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, nrc_id, numero_sesion, dia,
                       hora_inicio::text AS hora_inicio,
                       hora_fin::text    AS hora_fin,
                       salon, edificio
                FROM horario
                ORDER BY nrc_id, dia, hora_inicio
                """
            )
            filas = cur.fetchall()
    finally:
        conn.close()

    mapa = {}
    for fila in filas:
        mapa.setdefault(fila["nrc_id"], []).append(fila)
    return mapa


def _periodo_actual_nrc() -> Optional[str]:
    """
    Periodo vigente de la oferta.

    Prioridad:
      1. Variable de entorno ``PERIODO_ACTUAL`` (si tiene un valor no vacío).
      2. ``SELECT MAX(periodo) FROM nrc WHERE periodo IS NOT NULL``.
      3. ``None`` si la tabla no tiene periodos.
    """
    periodo_env = os.getenv("PERIODO_ACTUAL")
    if periodo_env is not None and periodo_env.strip():
        return periodo_env.strip()

    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(periodo) AS periodo FROM nrc "
                "WHERE periodo IS NOT NULL"
            )
            fila = cur.fetchone()
    finally:
        conn.close()

    if not fila or fila.get("periodo") is None:
        return None
    return str(fila["periodo"])


def _tiene_inscripciones_periodo(
    estudiante_id: int, periodo: Optional[str]
) -> bool:
    """
    True si el estudiante ya tiene al menos una inscripción en ``periodo``.

    Con ``periodo=None`` se considera cualquier inscripción (sin un periodo
    vigente no tiene sentido filtrar por periodo).
    """
    conn = conectar()
    try:
        with conn.cursor() as cur:
            if periodo is None:
                cur.execute(
                    """
                    SELECT 1
                    FROM inscripcion i
                    JOIN nrc n ON n.id_nrc = i.nrc_id
                    WHERE i.estudiante_id = %s
                    LIMIT 1
                    """,
                    (estudiante_id,),
                )
            else:
                cur.execute(
                    """
                    SELECT 1
                    FROM inscripcion i
                    JOIN nrc n ON n.id_nrc = i.nrc_id
                    WHERE i.estudiante_id = %s
                      AND n.periodo = %s
                    LIMIT 1
                    """,
                    (estudiante_id, periodo),
                )
            return cur.fetchone() is not None
    finally:
        conn.close()


def _horario_guardado(
    estudiante_id: int, periodo: Optional[str] = None
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """
    Horario ya persistido en ``inscripcion``, agrupado por NRC.

    Devuelve ``(periodo, horario)``:
      * ``periodo``: el solicitado o, si no se indicó, el máximo periodo de
        las inscripciones del estudiante (``None`` si no hay periodos).
      * ``horario``: lista de NRC con sus sesiones. Un estudiante sin
        inscripciones devuelve ``(None, [])``.

    Si se indica ``periodo`` se filtra por él; si no, se usan las
    inscripciones del periodo máximo detectado (o todas si no hay periodos).
    """
    periodo_solicitado = str(periodo).strip() if periodo is not None else ""
    periodo_efectivo: Optional[str] = periodo_solicitado or None

    conn = conectar()
    try:
        with conn.cursor() as cur:
            if periodo_efectivo is None:
                cur.execute(
                    """
                    SELECT MAX(n.periodo) AS periodo
                    FROM inscripcion i
                    JOIN nrc n ON n.id_nrc = i.nrc_id
                    WHERE i.estudiante_id = %s
                      AND n.periodo IS NOT NULL
                    """,
                    (estudiante_id,),
                )
                fila_periodo = cur.fetchone()
                if fila_periodo and fila_periodo.get("periodo") is not None:
                    periodo_efectivo = str(fila_periodo["periodo"])

            consulta = """
                SELECT
                    i.nrc_id,
                    c.codigo_alfanumerico AS codigo,
                    c.nombre              AS materia,
                    c.creditos,
                    d.nombre              AS docente_nombre,
                    s.nombre              AS sede_nombre,
                    h.numero_sesion,
                    h.dia,
                    h.hora_inicio::text   AS hora_inicio,
                    h.hora_fin::text      AS hora_fin,
                    h.salon,
                    h.edificio
                FROM inscripcion i
                JOIN nrc n      ON n.id_nrc = i.nrc_id
                JOIN curso c    ON c.codigo_alfanumerico = n.curso_id
                LEFT JOIN horario h ON h.nrc_id = n.id_nrc
                LEFT JOIN docente d ON d.id_docente = n.docente_id
                LEFT JOIN sede s    ON s.codigo = n.sede_id
                WHERE i.estudiante_id = %s
            """
            parametros: List[Any] = [estudiante_id]
            if periodo_efectivo is not None:
                consulta += " AND n.periodo = %s"
                parametros.append(periodo_efectivo)
            consulta += """
                ORDER BY c.codigo_alfanumerico, i.nrc_id,
                         h.dia, h.hora_inicio, h.numero_sesion
            """
            cur.execute(consulta, tuple(parametros))
            filas = cur.fetchall()
    finally:
        conn.close()

    horario: List[Dict[str, Any]] = []
    por_nrc: Dict[int, Dict[str, Any]] = {}
    for fila in filas:
        nrc_id = fila["nrc_id"]
        grupo = por_nrc.get(nrc_id)
        if grupo is None:
            grupo = {
                "codigo": fila["codigo"],
                "materia": fila["materia"],
                "creditos": fila["creditos"],
                "nrc_id": nrc_id,
                "docente_nombre": fila["docente_nombre"],
                "sede_nombre": fila["sede_nombre"],
                "sesiones": [],
            }
            por_nrc[nrc_id] = grupo
            horario.append(grupo)

        # ``LEFT JOIN``: un NRC sin sesiones conserva el grupo con lista vacía.
        if (
            fila["numero_sesion"] is None
            and fila["dia"] is None
            and fila["hora_inicio"] is None
        ):
            continue

        grupo["sesiones"].append(
            {
                "numero_sesion": fila["numero_sesion"],
                "dia": fila["dia"],
                "dia_nombre": DIA_NOMBRES.get(fila["dia"], fila["dia"]),
                "hora_inicio": fila["hora_inicio"],
                "hora_fin": fila["hora_fin"],
                "salon": fila["salon"],
                "edificio": fila["edificio"],
            }
        )

    return periodo_efectivo, horario


def codigos_de_tabla(cur, tabla, estudiante_id, obligatoria=False, mensaje=None):
    """
    Devuelve los ``codigo_curso`` (en mayúsculas) de ``tabla`` para un
    estudiante.

    ``tabla`` solo puede ser ``historial`` o ``matriz_por_ver`` (se interpola en
    el SQL, por eso se valida contra una lista blanca). Si la tabla no existe:
      * ``obligatoria=True`` -> HTTP 503 con un mensaje claro.
      * ``obligatoria=False`` -> lista vacía (la API sigue funcionando).
    """
    if tabla not in ("historial", "matriz_por_ver"):
        raise ValueError(f"Tabla no permitida: {tabla}")

    cur.execute("SELECT to_regclass(%s) AS tabla", (f"public.{tabla}",))
    if cur.fetchone()["tabla"] is None:
        if obligatoria:
            raise HTTPException(
                status_code=503,
                detail=mensaje
                or (
                    f"La tabla '{tabla}' no existe todavía. "
                    "Debe crearla el módulo de Data Lake antes de usar este endpoint."
                ),
            )
        return []

    cur.execute(
        f"SELECT DISTINCT codigo_curso FROM {tabla} WHERE estudiante_id = %s",
        (estudiante_id,),
    )
    codigos = set()
    for fila in cur.fetchall():
        codigo = str(fila["codigo_curso"] or "").strip().upper()
        if codigo:
            codigos.add(codigo)
    return sorted(codigos)


def materias_aprobadas(cur, estudiante_id):
    """
    Devuelve los códigos de curso (normalizados a mayúsculas) aprobados por el
    estudiante según la tabla ``historial`` (creada por el Data Lake).

    Se asume que la tabla tiene, como mínimo, las columnas ``estudiante_id`` y
    ``codigo_curso``. Si la tabla no existe se lanza un 503 con un mensaje claro.
    """
    return codigos_de_tabla(
        cur,
        "historial",
        estudiante_id,
        obligatoria=True,
        mensaje=(
            "La tabla 'historial' no existe todavía. "
            "Debe crearla el módulo de Data Lake antes de usar este endpoint."
        ),
    )


def materias_por_ver(cur, estudiante_id):
    """
    Códigos de materias pendientes (``matriz_por_ver``) del estudiante.

    Es opcional: si la tabla aún no existe (estudiante sin matriz cargada) se
    devuelve una lista vacía para no romper el generador automático.
    """
    return codigos_de_tabla(cur, "matriz_por_ver", estudiante_id, obligatoria=False)


# ---------------------------------------------------------------------------
# Utilidades de base de datos — MongoDB
# ---------------------------------------------------------------------------
_mongo_client = None


def mongo_db():
    """Devuelve la base de datos MongoDB (cliente perezoso y en caché)."""
    global _mongo_client
    if not MONGO_DISPONIBLE:
        raise HTTPException(
            status_code=503,
            detail="pymongo no está instalado. Ejecuta: pip install pymongo",
        )
    if _mongo_client is None:
        # serverSelectionTimeoutMS acota el tiempo de espera si Mongo no está
        # levantado, para que el endpoint falle rápido en lugar de colgarse.
        _mongo_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    return _mongo_client[MONGO_DB]


def _clave_texto(valor) -> str:
    """
    Clave de comparación tolerante: sin acentos, mayúsculas, espacios
    colapsados. Sirve para comparar programa/jornada/sede escritos por el
    usuario con los valores de la base de datos.
    """
    texto = _sin_acentos(str(valor if valor is not None else ""))
    return re.sub(r"\s+", " ", texto).strip().upper()


def _programa_coincide(a, b) -> bool:
    """True si dos nombres de programa son equivalentes (tolerante a formato)."""
    ca, cb = _clave_texto(a), _clave_texto(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    # Coincidencia por contención solo si el texto corto es suficientemente
    # específico (evita que "INGENIERÍA" haga match con todo).
    corto, largo = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
    return len(corto) >= 8 and corto in largo


def _entero_opcional(valor) -> Optional[int]:
    """Convierte un valor a int (o None) tolerando '3', '3.0', 3.0 y vacíos."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float):
        return int(valor) if valor == int(valor) else None
    texto = str(valor).strip().replace(",", ".")
    if not texto:
        return None
    try:
        numero = float(texto)
    except (TypeError, ValueError):
        return None
    return int(numero) if numero == int(numero) else None


def _lista_prerrequisitos(valor) -> List[str]:
    """Normaliza prerrequisitos vengan como lista o como texto separado."""
    if valor is None:
        return []
    if isinstance(valor, str):
        texto = valor.strip()
        if not texto:
            return []
        if any(sep in texto for sep in (",", ";", "|")):
            partes = re.split(r"[,;|]+", texto)
        elif " " in texto:
            partes = texto.split()
        else:
            partes = [texto]
    elif isinstance(valor, (list, tuple, set)):
        partes = list(valor)
    else:
        partes = [valor]

    resultado: List[str] = []
    vistos = set()
    for parte in partes:
        codigo = str(parte or "").strip().upper()
        if codigo and codigo not in vistos:
            vistos.add(codigo)
            resultado.append(codigo)
    return resultado


def _normalizar_curso_malla(item: Dict[str, Any], por_defecto: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Convierte un documento/ítem de malla (JSON de MongoDB o del dashboard) a la
    forma pública ``{programa, sede, semestre, codigo, nombre, creditos, horas,
    prerrequisitos}``. Devuelve None si no tiene código.
    """
    codigo = (
        item.get("codigo")
        or item.get("codigo_curso")
        or item.get("codigo_alfanumerico")
        or item.get("codigo_materia")
        or item.get("materia_codigo")
    )
    codigo = str(codigo or "").strip().upper()
    if not codigo:
        return None

    nombre = (
        item.get("nombre")
        or item.get("nombre_curso")
        or item.get("nombre_materia")
        or item.get("materia")
        or item.get("asignatura")
    )
    programa = item.get("programa") or item.get("plan") or item.get("carrera")
    if not programa:
        programa = por_defecto.get("programa")
    sede = item.get("sede") or por_defecto.get("sede")

    return {
        "programa": str(programa).strip() if programa else None,
        "sede": str(sede).strip() if sede else None,
        "semestre": _entero_opcional(item.get("semestre")),
        "codigo": codigo,
        "nombre": str(nombre).strip() if nombre else None,
        "creditos": _entero_opcional(
            item.get("creditos", item.get("créditos"))
        ),
        "horas": _entero_opcional(item.get("horas", item.get("horas_semanales"))),
        "prerrequisitos": _lista_prerrequisitos(
            item.get(
                "prerrequisitos",
                item.get("prerequisitos", item.get("requisitos")),
            )
        ),
    }


def _documentos_malla() -> List[Dict[str, Any]]:
    """Lee los documentos crudos de ``malla_curricular`` desde MongoDB."""
    db = mongo_db()
    try:
        return list(db[MONGO_COL_MALLA].find({}, {"_id": 0}))
    except PyMongoError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"No se pudo leer malla_curricular desde MongoDB: {exc}",
        ) from exc


def _cursos_malla(programa: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Devuelve la malla normalizada (lista de cursos).

    Tolera dos layouts en ``malla_curricular``:
      * un documento por curso: ``{programa, sede, codigo, nombre, ...}``;
      * un documento por programa/plan con un array ``cursos`` o ``materias``
        (es el formato de ``datalake/malla_curricular.json``).

    Si se indica ``programa`` se filtra por él (tolerante a acentos/mayúsculas).
    Los cursos sin programa se conservan porque pueden venir de un PDF parcial.
    """
    documentos = _documentos_malla()
    cursos: List[Dict[str, Any]] = []
    vistos = set()

    def _agregar(curso) -> None:
        if not curso:
            return
        clave_curso = (_clave_texto(curso["programa"]), curso["codigo"])
        if clave_curso in vistos:
            return
        vistos.add(clave_curso)
        cursos.append(curso)

    # Primero los documentos planos (un curso por documento). Así, si el
    # administrador editó un curso con PUT /api/admin/malla, esa versión gana
    # sobre la copia anidada dentro de un documento de plan.
    for doc in documentos:
        if not isinstance(doc, dict):
            continue
        # ``cursos`` es la señal fuerte de documento-plan anidado.
        if isinstance(doc.get("cursos"), list):
            continue
        anidados = doc.get("materias")
        # Un documento con código propio es un curso plano, aunque tenga un
        # campo "materias" (p. ej. metadatos); no se debe descartar.
        if isinstance(anidados, list) and not _codigo_item_malla(doc):
            continue
        por_defecto = {
            "programa": doc.get("programa") or doc.get("plan"),
            "sede": doc.get("sede"),
        }
        _agregar(_normalizar_curso_malla(doc, por_defecto))

    # Después los documentos por programa/plan con array de cursos.
    for doc in documentos:
        if not isinstance(doc, dict):
            continue
        anidados = doc.get("cursos")
        if not isinstance(anidados, list):
            anidados = (
                doc.get("materias")
                if isinstance(doc.get("materias"), list)
                else None
            )
        if anidados is None:
            continue
        por_defecto = {
            "programa": doc.get("programa") or doc.get("plan"),
            "sede": doc.get("sede"),
        }
        for item in anidados:
            if not isinstance(item, dict):
                # Lista simple de códigos: ["CAL-101", ...]
                item = {"codigo": item}
            _agregar(_normalizar_curso_malla(item, por_defecto))

    if programa:
        # Solo aplica el filtro si al menos un curso declara programa; si la
        # malla no trae programa, se devuelve completa (fallback tolerante).
        if any(c.get("programa") for c in cursos):
            cursos = [
                c
                for c in cursos
                if not c.get("programa")
                or _programa_coincide(c.get("programa"), programa)
            ]

    cursos.sort(
        key=lambda c: (
            _clave_texto(c.get("programa")),
            c.get("semestre") if c.get("semestre") is not None else 999,
            c.get("codigo") or "",
        )
    )
    return cursos


def _programas_malla() -> List[str]:
    """Programas distintos declarados en la malla (incluye planes sin cursos)."""
    programas = set()
    for doc in _documentos_malla():
        if not isinstance(doc, dict):
            continue
        valor = doc.get("programa") or doc.get("plan")
        if valor:
            programas.add(str(valor).strip())
    for curso in _cursos_malla():
        if curso.get("programa"):
            programas.add(str(curso["programa"]).strip())
    return sorted(
        (p for p in programas if p),
        key=lambda p: _clave_texto(p),
    )


def _codigo_item_malla(item) -> str:
    """Código de un ítem de malla (dict con alias o una cadena simple)."""
    if not isinstance(item, dict):
        return str(item or "").strip().upper()
    return str(
        item.get("codigo")
        or item.get("codigo_curso")
        or item.get("codigo_alfanumerico")
        or item.get("codigo_materia")
        or ""
    ).strip().upper()


def _actualizar_curso_malla_anidado(
    col_malla, programa: Optional[str], codigo: str, documento: Dict[str, Any]
) -> bool:
    """
    Actualiza un curso dentro de un documento de plan con array ``cursos`` o
    ``materias`` (formato del ``malla_curricular.json`` de fallback).

    Devuelve True si encontró y actualizó el curso. Si no hay documento
    anidado, el llamador hace el upsert plano por ``programa + codigo``.
    """
    filtro: Dict[str, Any] = {
        "$or": [
            {"cursos": {"$exists": True}},
            {"materias": {"$exists": True}},
        ]
    }
    if programa:
        filtro["programa"] = programa

    for doc in col_malla.find(filtro):
        for campo in ("cursos", "materias"):
            lista = doc.get(campo)
            if not isinstance(lista, list):
                continue
            for posicion, item in enumerate(lista):
                if _codigo_item_malla(item) != codigo:
                    continue
                nuevo = dict(item) if isinstance(item, dict) else {}
                for clave, valor in documento.items():
                    if valor is not None:
                        nuevo[clave] = valor
                nuevo["codigo"] = codigo
                nuevo["prerrequisitos"] = documento.get("prerrequisitos", [])
                lista_nueva = list(lista)
                lista_nueva[posicion] = nuevo
                col_malla.update_one(
                    {"_id": doc["_id"]}, {"$set": {campo: lista_nueva}}
                )
                return True
    return False


def _primer_semestre_malla(programa: Optional[str]) -> Tuple[List[Dict[str, Any]], int]:
    """
    Cursos del primer semestre (semestre mínimo) de la malla del programa.

    Devuelve ``([cursos], semestre)``. Si la malla no declara semestres, se
    devuelven todos los cursos con semestre 1.
    """
    cursos = _cursos_malla(programa)
    if not cursos:
        return [], 1

    semestres = [
        c["semestre"]
        for c in cursos
        if isinstance(c.get("semestre"), int) and c["semestre"] >= 0
    ]
    if not semestres:
        return cursos, 1

    minimo = min(semestres)
    return [c for c in cursos if c.get("semestre") == minimo], minimo


def obtener_catalogo(programa: Optional[str] = None):
    """
    Devuelve la lista de materias del plan de estudios.

    Fuente principal: ``malla_curricular`` (malla subida como PDF y editable
    desde el dashboard). Si está vacía, mantiene compatibilidad con la
    colección antigua ``catalogo_materias`` (y ``materias``).

    Cada materia tiene la forma {codigo, nombre, creditos, semestre,
    prerrequisitos, programa?, sede?, horas?}. Normaliza código y
    prerrequisitos a mayúsculas sin espacios. Si ``programa`` se informa, la
    malla se filtra por ese programa.
    """
    cursos = _cursos_malla(programa)
    if cursos:
        # El catálogo es indexado por código (los NRC referencian curso_id);
        # si un mismo código aparece en dos programas, gana el primero.
        por_codigo = {}
        for curso in cursos:
            por_codigo.setdefault(curso["codigo"], curso)
        return list(por_codigo.values())

    db = mongo_db()
    try:
        docs = list(db[MONGO_COL_CATALOGO].find({}, {"_id": 0}))
        if not docs:
            # Compatibilidad con el seed previo (colección "materias").
            docs = list(db["materias"].find({}, {"_id": 0}))
    except PyMongoError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"No se pudo leer el catálogo desde MongoDB: {exc}",
        ) from exc

    materias = []
    for doc in docs:
        # Documento tipo plan con array de materias anidado.
        if "materias" in doc and isinstance(doc["materias"], list):
            for item in doc["materias"]:
                if isinstance(item, dict):
                    materias.append(item)
                else:
                    materias.append({"codigo": str(item), "prerrequisitos": []})
        elif doc.get("codigo"):
            materias.append(doc)

    # Normalización.
    for m in materias:
        m["codigo"] = str(m.get("codigo", "")).strip().upper()
        m["prerrequisitos"] = [
            str(p).strip().upper() for p in (m.get("prerrequisitos") or [])
        ]
    return materias


def catalogo_desde_postgres(programa: Optional[str] = None):
    """
    Fallback: construye el catálogo (materias del plan) desde PostgreSQL cuando
    MongoDB no está disponible o ``malla_curricular``/``catalogo_materias``
    aún están vacías (por ejemplo, antes de que el Data Lake sincronice).

    Devuelve la misma forma que ``obtener_catalogo()``:
    [{codigo, nombre, creditos, semestre, prerrequisitos}]. Si se indica
    ``programa`` se filtra por ``curso.programa`` (tolerante a formato). Los
    prerrequisitos efectivos se resuelven aparte con ``reglas_negocio``
    (MongoDB) o, si no hay reglas, quedan vacíos.
    """
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT codigo_alfanumerico AS codigo, nombre, creditos,
                       semestre, programa
                FROM curso
                ORDER BY semestre NULLS LAST, codigo_alfanumerico
                """
            )
            filas = cur.fetchall()
    finally:
        conn.close()

    if programa and any(_texto_opcional(f["programa"]) for f in filas):
        filas = [
            f
            for f in filas
            if not _texto_opcional(f["programa"])
            or _programa_coincide(f["programa"], programa)
        ]

    return [
        {
            "codigo": str(f["codigo"]).strip().upper(),
            "nombre": f["nombre"],
            "creditos": f["creditos"],
            "semestre": f["semestre"],
            "prerrequisitos": [],
            "programa": f["programa"],
        }
        for f in filas
    ]


def obtener_reglas_completas():
    """
    Devuelve las reglas de negocio dinámicas desde MongoDB conservando los
    metadatos que siembra ``seed_reglas.py``.

    Forma: [{materia, prerrequisitos, nombre?, creditos?, semestre?}, ...]
    ordenado por materia. ``prerrequisitos`` y ``materia`` se normalizan a
    mayúsculas; ``nombre``/``creditos``/``semestre`` se incluyen solo si el
    documento los trae (así el contrato básico no cambia).
    """
    db = mongo_db()
    try:
        docs = list(db[MONGO_COL_REGLAS].find({}, {"_id": 0}))
    except PyMongoError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"No se pudo leer reglas_negocio desde MongoDB: {exc}",
        ) from exc

    por_materia = {}
    for doc in docs:
        materia = str(doc.get("materia", "")).strip().upper()
        if not materia:
            continue
        regla = {
            "materia": materia,
            "prerrequisitos": [
                str(p).strip().upper()
                for p in (doc.get("prerrequisitos") or [])
            ],
        }
        # Metadatos opcionales del catálogo sembrados por seed_reglas.py.
        for campo in ("nombre", "creditos", "semestre"):
            if doc.get(campo) is not None:
                regla[campo] = doc[campo]
        # Si hubiera documentos duplicados, gana el último (como antes).
        por_materia[materia] = regla
    return sorted(por_materia.values(), key=lambda r: r["materia"])


def obtener_reglas():
    """
    Devuelve las reglas de negocio dinámicas desde MongoDB como un diccionario
    {materia: [prerrequisitos]}, con claves/valores normalizados a mayúsculas.
    """
    return {
        regla["materia"]: regla["prerrequisitos"]
        for regla in obtener_reglas_completas()
    }


def prerrequisitos_efectivos(codigo, catalogo_map, reglas):
    """
    Devuelve los prerrequisitos efectivos de una materia.

    Prioridad: si existe una regla dinámica en ``reglas_negocio`` para la
    materia, ésta manda (el admin puede cambiarla sin tocar código); en caso
    contrario se usan los prerrequisitos estáticos del catálogo.
    """
    codigo = codigo.strip().upper()
    if codigo in reglas:
        return list(reglas[codigo])
    materia = catalogo_map.get(codigo)
    if materia is None:
        return []
    return list(materia.get("prerrequisitos") or [])


# ---------------------------------------------------------------------------
# Utilidades de horario (para el generador automático)
# ---------------------------------------------------------------------------
def a_minutos(hora):
    """Convierte 'HH:MM:SS' (o 'HH:MM') a minutos desde medianoche."""
    partes = str(hora).split(":")
    try:
        return int(partes[0]) * 60 + int(partes[1])
    except (IndexError, ValueError):
        return 0


def a_entero(valor, por_defecto=0):
    """Convierte a int tolerando None, cadenas vacías o valores no numéricos."""
    try:
        return int(valor)
    except (TypeError, ValueError):
        return por_defecto


def choca(sesiones, ocupados):
    """
    True si alguna sesión de ``sesiones`` solapa (mismo día + rangos de tiempo
    que se traslapan) con alguno de los intervalos ya ocupados.

    ``ocupados`` es una lista de tuplas (dia, inicio_min, fin_min).
    """
    for s in sesiones:
        dia = s["dia"]
        ini = a_minutos(s["hora_inicio"])
        fin = a_minutos(s["hora_fin"])
        for o_dia, o_ini, o_fin in ocupados:
            if o_dia == dia and ini < o_fin and o_ini < fin:
                return True
    return False


# ---------------------------------------------------------------------------
# Utilidades de carga de archivos (admin: oferta / estudiante: matriz)
# ---------------------------------------------------------------------------
# Tablas auxiliares de la matriz del estudiante. Se crean de forma idempotente
# aquí porque ``schema.sql`` no las incluye (igual que hace el Data Lake con
# ``historial``). Los nombres sin comillas se guardan en minúsculas en
# PostgreSQL.
DDL_ESTUDIANTE_PERFIL = """
    CREATE TABLE IF NOT EXISTS estudiante_perfil (
        estudiante_id INTEGER PRIMARY KEY,
        carrera       TEXT,
        semestre      INTEGER,
        programa      TEXT,
        jornada       TEXT,
        sede          TEXT
    )
"""

# Migración para perfiles creados con el esquema anterior (sin programa,
# jornada ni sede). Es idempotente y no borra datos.
DDL_ESTUDIANTE_PERFIL_ALTERS = (
    "ALTER TABLE estudiante_perfil "
    "ADD COLUMN IF NOT EXISTS programa TEXT",
    "ALTER TABLE estudiante_perfil "
    "ADD COLUMN IF NOT EXISTS jornada TEXT",
    "ALTER TABLE estudiante_perfil "
    "ADD COLUMN IF NOT EXISTS sede TEXT",
)

DDL_MATRIZ_POR_VER = """
    CREATE TABLE IF NOT EXISTS matriz_por_ver (
        estudiante_id INTEGER NOT NULL,
        codigo_curso  VARCHAR(10) NOT NULL,
        PRIMARY KEY (estudiante_id, codigo_curso)
    )
"""

DDL_HISTORIAL = """
    CREATE TABLE IF NOT EXISTS historial (
        estudiante_id INTEGER NOT NULL,
        codigo_curso  VARCHAR(10) NOT NULL,
        PRIMARY KEY (estudiante_id, codigo_curso)
    )
"""


def _asegurar_tablas_estudiante(cur) -> None:
    """
    Crea (si faltan) las tablas auxiliares de la matriz y migra el perfil a las
    columnas nuevas (programa/jornada/sede). Idempotente.
    """
    cur.execute(DDL_ESTUDIANTE_PERFIL)
    for sentencia in DDL_ESTUDIANTE_PERFIL_ALTERS:
        cur.execute(sentencia)
    cur.execute(DDL_MATRIZ_POR_VER)
    cur.execute(DDL_HISTORIAL)

# Columnas que debe traer el CSV de matriz (``nombre_curso`` es opcional: se
# acepta y se ignora, pero no se usa para poblar PostgreSQL).
COLUMNAS_MATRIZ_REQUERIDAS = [
    "estudiante_id",
    "carrera",
    "semestre",
    "estado",
    "codigo_curso",
]
COLUMNAS_MATRIZ_OPCIONALES = ["nombre_curso"]

# Estados del CSV -> conjunto destino.
ESTADOS_APROBADA = {
    "aprobada", "aprobadas", "aprobado", "aprobados",
    "vista", "vistas", "visto", "vistos",
}
ESTADOS_POR_VER = {
    "por_ver", "porver", "por_verificar", "pendiente", "pendientes",
}


def _sin_acentos(texto):
    """Quita diacríticos (para comparar 'Matemática' / 'matematica')."""
    descompuesto = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def _normalizar_columna(nombre):
    """
    Normaliza un encabezado de CSV: sin BOM, sin acentos, minúsculas y con
    espacios/guiones convertidos en ``_``.

    Ejemplos: ``" Estudiante ID "`` -> ``estudiante_id``;
    ``"Código_Curso"`` -> ``codigo_curso``.
    """
    texto = _sin_acentos(str(nombre)).lstrip("\ufeff").strip().lower()
    texto = re.sub(r"[\s\-]+", "_", texto)
    return re.sub(r"_+", "_", texto).strip("_")


def _texto_celda(valor):
    """Convierte una celda de pandas a texto limpio ('' para nulos)."""
    if valor is None:
        return ""
    if isinstance(valor, float) and valor != valor:  # NaN
        return ""
    texto = str(valor).strip()
    if texto.lower() in ("nan", "none", "<na>", "nat"):
        return ""
    return texto


def _normalizar_estado(valor):
    """Mapea el estado del CSV a 'aprobada' / 'por_ver' (o None si no es válido)."""
    clave = _normalizar_columna(_texto_celda(valor))
    if clave in ESTADOS_APROBADA:
        return "aprobada"
    if clave in ESTADOS_POR_VER:
        return "por_ver"
    return None


def _parsear_semestre(valor):
    """Convierte '3' o '3.0' a entero; devuelve None si está vacío o es inválido."""
    texto = _texto_celda(valor).replace(",", ".")
    if not texto:
        return None
    try:
        numero = float(texto)
    except ValueError:
        return None
    if numero != int(numero) or numero < 0:
        return None
    return int(numero)


def dir_uploads():
    """Directorio de uploads (UPLOADS_DIR; por defecto <backend|/app>/uploads)."""
    destino = Path(
        os.getenv("UPLOADS_DIR", str(BASE_DIR / "uploads"))
    ).expanduser()
    return destino.resolve()


def ruta_script_ingesta():
    """
    Ruta absoluta de ``datalake/ingesta_datalake.py`` calculada desde __file__.

    Cubre los dos layouts del proyecto:
      * Docker (contexto = raíz del repo): /app/main.py -> /app/datalake/...
      * Local: backend/main.py -> <repo>/datalake/...
    """
    candidatos = []
    ruta_env = os.getenv("INGESTA_SCRIPT")
    if ruta_env:
        candidatos.append(Path(ruta_env).expanduser())
    candidatos.extend(
        [
            BASE_DIR / "datalake" / "ingesta_datalake.py",
            BASE_DIR.parent / "datalake" / "ingesta_datalake.py",
        ]
    )
    for candidato in candidatos:
        if candidato.is_file():
            return candidato.resolve()
    return None


def _recortar_salida(texto, limite=8000):
    """Recorta la salida de un subproceso conservando el final (el error real)."""
    texto = texto or ""
    if len(texto) <= limite:
        return texto
    return f"...(salida recortada, últimos {limite} caracteres)...\n" + texto[-limite:]


def extraer_resumen_ingesta(salida):
    """
    Extrae {cursos, nrcs, sesiones} de la salida de la ingesta.

    Primero busca la línea ``RESUMEN_JSON: {...}`` que imprime el script; si no
    está (versión antigua), cae a parsear el resumen en texto. Devuelve 0 para
    los conteos que no se puedan determinar.
    """
    resumen = {}
    for linea in reversed((salida or "").splitlines()):
        if linea.strip().startswith("RESUMEN_JSON:"):
            try:
                datos = json.loads(linea.split(":", 1)[1].strip())
                if isinstance(datos, dict):
                    resumen = datos
                break
            except (ValueError, TypeError):
                continue

    for clave in ("cursos", "nrcs", "sesiones"):
        if clave in resumen:
            try:
                resumen[clave] = int(resumen[clave])
                continue
            except (TypeError, ValueError):
                pass
        coincidencia = re.search(
            rf"{clave}\s*:\s*([\d][\d.,]*)", salida or "", re.IGNORECASE
        )
        if coincidencia:
            digitos = re.sub(r"\D", "", coincidencia.group(1))
            resumen[clave] = int(digitos) if digitos else 0
        else:
            resumen[clave] = 0
    return {clave: resumen.get(clave, 0) for clave in ("cursos", "nrcs", "sesiones")}


def _unir_salida(stdout, stderr):
    """Une stdout/stderr de un subproceso en un único texto (admite bytes)."""
    partes = []
    for valor in (stdout, stderr):
        if not valor:
            continue
        if isinstance(valor, bytes):
            valor = valor.decode("utf-8", errors="replace")
        partes.append(str(valor).rstrip())
    return "\n".join(partes)


def _leer_csv_subido(datos, etiqueta):
    """
    Lee un CSV subido (bytes) con pandas, tolerando UTF-8/latin-1, BOM,
    separador coma o punto y coma, y encabezados con espacios/mayúsculas.

    Devuelve un DataFrame de texto (sin NaN) con columnas normalizadas.
    """
    if not datos:
        raise HTTPException(status_code=400, detail=f"{etiqueta}: el archivo está vacío")

    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - entorno sin pandas
        raise HTTPException(
            status_code=503,
            detail=(
                "pandas no está instalado en el backend. "
                "Ejecuta: pip install pandas pyarrow"
            ),
        ) from exc

    df = None
    ultimo_error = None
    for encoding in ("utf-8-sig", "latin-1"):
        muestra = datos[:8192].decode(encoding, errors="replace")
        primera_linea = muestra.splitlines()[0] if muestra.splitlines() else ""
        separador = ";" if primera_linea.count(";") > primera_linea.count(",") else ","
        try:
            df = pd.read_csv(
                io.BytesIO(datos),
                dtype=str,
                encoding=encoding,
                sep=separador,
                keep_default_na=False,
            )
            break
        except UnicodeDecodeError as exc:
            ultimo_error = exc
            continue
        except pd.errors.EmptyDataError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"{etiqueta}: el CSV está vacío o no tiene encabezados",
            ) from exc
        except Exception as exc:  # pandas.errors.ParserError y similares
            raise HTTPException(
                status_code=400,
                detail=f"{etiqueta}: no se pudo leer el CSV: {exc}",
            ) from exc

    if df is None:
        raise HTTPException(
            status_code=400,
            detail=f"{etiqueta}: no se pudo decodificar el CSV ({ultimo_error})",
        )

    # Encabezados normalizados y sin columnas duplicadas.
    columnas = [_normalizar_columna(c) for c in df.columns]
    if len(set(columnas)) != len(columnas):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{etiqueta}: hay columnas duplicadas tras normalizar los "
                f"encabezados: {columnas}"
            ),
        )
    df.columns = columnas

    # Fuera filas totalmente vacías y espacios sobrantes en los valores.
    df = df[
        df.apply(lambda fila: any(_texto_celda(v) for v in fila), axis=1)
    ].reset_index(drop=True)
    for columna in df.columns:
        df[columna] = df[columna].map(_texto_celda)

    if df.empty:
        raise HTTPException(
            status_code=400, detail=f"{etiqueta}: no contiene filas de datos"
        )
    return df


def _parsear_matriz(df, estudiante_id_form=None):
    """
    Valida y transforma el DataFrame de matriz en los datos a persistir.

    Devuelve {estudiante_id, carrera, semestre, aprobadas, por_ver}. Lanza 400
    con mensajes claros ante columnas faltantes, estados desconocidos, códigos
    inválidos, varios estudiantes o un ``estudiante_id`` que no coincide con el
    del formulario.
    """
    faltantes = [c for c in COLUMNAS_MATRIZ_REQUERIDAS if c not in df.columns]
    if faltantes:
        raise HTTPException(
            status_code=400,
            detail=(
                "La matriz no tiene las columnas requeridas: "
                f"{faltantes}. Formato esperado: "
                "estudiante_id,carrera,semestre,estado,codigo_curso,nombre_curso"
            ),
        )

    errores = []
    estudiantes = set()
    aprobadas = set()
    por_ver = set()
    carrera = None
    semestre = None

    for posicion, fila in df.iterrows():
        linea = posicion + 2  # +1 por índice 0 y +1 por el encabezado
        id_texto = _texto_celda(fila["estudiante_id"])
        codigo = _texto_celda(fila["codigo_curso"]).upper()
        estado_texto = _texto_celda(fila["estado"])

        if not id_texto and not codigo:
            continue

        try:
            estudiante_fila = int(id_texto)
        except (TypeError, ValueError):
            errores.append(
                f"línea {linea}: estudiante_id inválido ({id_texto!r})"
            )
            continue
        if estudiante_fila <= 0:
            errores.append(
                f"línea {linea}: estudiante_id debe ser positivo ({estudiante_fila})"
            )
            continue

        if not codigo:
            errores.append(f"línea {linea}: codigo_curso vacío")
            continue
        if len(codigo) > 10:
            errores.append(
                f"línea {linea}: codigo_curso supera VARCHAR(10) ({codigo!r})"
            )
            continue

        estado = _normalizar_estado(estado_texto)
        if estado is None:
            errores.append(
                f"línea {linea}: estado no reconocido ({estado_texto!r}); "
                "use aprobada/vista o por_ver/pendiente"
            )
            continue

        estudiantes.add(estudiante_fila)

        carrera_fila = _texto_celda(fila["carrera"])
        if carrera_fila and carrera is None:
            carrera = carrera_fila

        semestre_fila = _texto_celda(fila["semestre"])
        if semestre_fila:
            valor_semestre = _parsear_semestre(semestre_fila)
            if valor_semestre is None:
                errores.append(
                    f"línea {linea}: semestre inválido ({semestre_fila!r})"
                )
            elif semestre is None:
                semestre = valor_semestre

        if estado == "aprobada":
            aprobadas.add(codigo)
        else:
            por_ver.add(codigo)

    if len(estudiantes) > 1:
        raise HTTPException(
            status_code=400,
            detail=(
                "El CSV contiene varios estudiantes distintos "
                f"({', '.join(str(e) for e in sorted(estudiantes))}). "
                "Sube una matriz por estudiante."
            ),
        )

    if estudiante_id_form is not None and estudiantes:
        if estudiante_id_form not in estudiantes:
            encontrado = next(iter(estudiantes))
            raise HTTPException(
                status_code=400,
                detail=(
                    f"El estudiante_id del formulario ({estudiante_id_form}) no "
                    f"coincide con el del CSV ({encontrado})."
                ),
            )

    if errores:
        detalle = "\n".join(f"- {e}" for e in errores[:20])
        if len(errores) > 20:
            detalle += f"\n... y {len(errores) - 20} error(es) más"
        raise HTTPException(
            status_code=400,
            detail=f"La matriz contiene errores:\n{detalle}",
        )

    if not aprobadas and not por_ver:
        raise HTTPException(
            status_code=400,
            detail="La matriz no contiene materias aprobadas ni pendientes válidas.",
        )

    # Una materia no puede ser aprobada y pendiente a la vez: gana aprobada.
    por_ver -= aprobadas

    if estudiante_id_form is not None:
        estudiante_id = estudiante_id_form
    elif estudiantes:
        estudiante_id = next(iter(estudiantes))
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                "No se pudo determinar el estudiante_id: inclúyelo en el CSV o "
                "envíalo en el campo 'estudiante_id' del formulario."
            ),
        )

    return {
        "estudiante_id": estudiante_id,
        "carrera": carrera,
        "semestre": semestre,
        "aprobadas": sorted(aprobadas),
        "por_ver": sorted(por_ver),
    }


async def guardar_upload(archivo, prefijo, extensiones=(".csv",)):
    """
    Guarda un UploadFile como ``<prefijo>_<timestamp>_<uuid><ext>`` en uploads.

    ``extensiones`` es la lista blanca de sufijos aceptados (minúsculas, con
    punto). Devuelve la ruta absoluta del archivo guardado. Por defecto solo
    acepta CSV para conservar el comportamiento histórico.
    """
    nombre_original = Path(archivo.filename or "").name
    extension = Path(nombre_original).suffix.lower()
    permitidas = tuple(ext.lower() for ext in extensiones)
    if extension not in permitidas:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Extensión no permitida ({nombre_original!r}). "
                f"Se esperaba: {', '.join(permitidas)}"
            ),
        )

    destino_dir = dir_uploads()
    try:
        destino_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo crear el directorio de uploads {destino_dir}: {exc}",
        ) from exc

    marca = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    nombre = f"{prefijo}_{marca}_{uuid.uuid4().hex[:8]}{extension}"
    destino = destino_dir / nombre

    total = 0
    try:
        with destino.open("wb") as salida:
            while True:
                bloque = await archivo.read(1024 * 1024)
                if not bloque:
                    break
                salida.write(bloque)
                total += len(bloque)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo guardar el archivo en {destino}: {exc}",
        ) from exc
    finally:
        await archivo.close()

    if total == 0:
        try:
            destino.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail="El archivo subido está vacío")

    return destino


# ---------------------------------------------------------------------------
# Utilidades de ingesta (subproceso del Data Lake) y de archivos Excel
# ---------------------------------------------------------------------------
def _extraer_resumen_json(salida) -> Dict[str, Any]:
    """
    Extrae el objeto ``RESUMEN_JSON: {...}`` que imprime la ingesta.

    Tolera que el JSON venga seguido de más texto o que ocupe varias líneas.
    Devuelve ``{}`` si no se encuentra o no se puede parsear.
    """
    texto = salida or ""
    marca = "RESUMEN_JSON:"
    posicion = texto.rfind(marca)
    if posicion < 0:
        return {}
    resto = texto[posicion + len(marca):].strip()
    if not resto:
        return {}

    intentos = [resto]
    inicio = resto.find("{")
    fin = resto.rfind("}")
    if inicio >= 0 and fin > inicio:
        intentos.append(resto[inicio:fin + 1])
    for candidato in intentos:
        try:
            datos = json.loads(candidato)
        except (TypeError, ValueError):
            continue
        if isinstance(datos, dict):
            return datos
    return {}


def _contar_resumen(resumen: Dict[str, Any], salida: str, clave: str) -> int:
    """
    Devuelve un conteo entero para ``clave``.

    Prioriza ``RESUMEN_JSON`` (acepta int, lista o dict) y, si no está, busca
    ``clave: N`` en la salida de texto de la ingesta.
    """
    if clave in resumen:
        valor = resumen[clave]
        if isinstance(valor, (list, tuple, dict, set)):
            return len(valor)
        try:
            return int(valor)
        except (TypeError, ValueError):
            pass

    coincidencia = re.search(
        rf"\b{re.escape(clave)}\b\s*[:=]\s*([\d][\d.,]*)",
        salida or "",
        re.IGNORECASE,
    )
    if coincidencia:
        digitos = re.sub(r"\D", "", coincidencia.group(1))
        if digitos:
            return int(digitos)
    return 0


def _contar_resumen_o_tabla(
    resumen: Dict[str, Any], salida: str, clave: str, tabla: str
) -> int:
    """
    Conteo del resumen de la ingesta; si la clave no aparece en la salida, cae
    al conteo real de la tabla PostgreSQL (útil con versiones antiguas de la
    ingesta que no imprimen ``docentes``/``sedes``).
    """
    valor = _contar_resumen(resumen, salida, clave)
    if valor:
        return valor
    if clave in resumen:
        return 0
    if re.search(
        rf"\b{re.escape(clave)}\b\s*[:=]\s*[\d]",
        salida or "",
        re.IGNORECASE,
    ):
        return 0
    return _contar_tabla_postgres(tabla)


def _ejecutar_ingesta(comando, etiqueta, archivo, timeout=None, cwd=None):
    """
    Ejecuta el script de ingesta y devuelve su salida combinada.

    Lanza HTTP 400 (con ``{mensaje, archivo, salida}``) si el proceso falla o
    excede el tiempo, y 500 si no se puede lanzar. ``etiqueta`` se usa en los
    mensajes (por ejemplo "la oferta académica").
    """
    if timeout is None:
        try:
            timeout = float(os.getenv("INGESTA_TIMEOUT", "600"))
        except ValueError:
            timeout = 600.0
    if timeout <= 0:
        timeout = 600.0

    logger.info("Ejecutando ingesta de %s: %s", etiqueta, " ".join(comando))

    try:
        proceso = subprocess.run(
            comando,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        salida = _unir_salida(exc.stdout, exc.stderr)
        raise HTTPException(
            status_code=400,
            detail={
                "mensaje": (
                    f"La ingesta de {etiqueta} excedió el tiempo máximo "
                    f"({timeout:.0f}s)."
                ),
                "archivo": str(archivo),
                "salida": _recortar_salida(salida),
            },
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo ejecutar la ingesta de {etiqueta}: {exc}",
        ) from exc

    salida = _unir_salida(proceso.stdout, proceso.stderr)
    if proceso.returncode != 0:
        logger.warning(
            "La ingesta de %s falló (código %s): %s",
            etiqueta,
            proceso.returncode,
            _recortar_salida(salida, 2000),
        )
        raise HTTPException(
            status_code=400,
            detail={
                "mensaje": f"La ingesta de {etiqueta} falló.",
                "archivo": str(archivo),
                "codigo_retorno": proceso.returncode,
                "salida": _recortar_salida(salida),
            },
        )
    return salida


def _contar_tabla_postgres(tabla: str) -> int:
    """
    Cuenta filas de una tabla conocida (fallback cuando la salida de la
    ingesta no trae un resumen numérico). Devuelve 0 si no se puede.
    """
    permitidas = {"curso", "nrc", "horario", "docente", "sede"}
    if tabla not in permitidas:
        return 0
    try:
        conn = conectar()
    except HTTPException:
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM {tabla}")
            fila = cur.fetchone()
            return int(fila["total"]) if fila else 0
    except Exception:  # noqa: BLE001 - el conteo es informativo
        return 0
    finally:
        conn.close()


def _contar_malla_mongo() -> int:
    """Número de cursos distintos (programa+código) en ``malla_curricular``."""
    try:
        cursos = _cursos_malla()
    except HTTPException:
        return 0
    return len({(_clave_texto(c.get("programa")), c.get("codigo")) for c in cursos})


def _normalizar_dataframe(df, etiqueta):
    """
    Normaliza encabezados/valores de un DataFrame (CSV o Excel) y elimina
    filas totalmente vacías. Devuelve el DataFrame limpio.
    """
    columnas = [_normalizar_columna(c) for c in df.columns]
    if len(set(columnas)) != len(columnas):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{etiqueta}: hay columnas duplicadas tras normalizar los "
                f"encabezados: {columnas}"
            ),
        )
    df.columns = columnas

    df = df[
        df.apply(lambda fila: any(_texto_celda(v) for v in fila), axis=1)
    ].reset_index(drop=True)
    for columna in df.columns:
        df[columna] = df[columna].map(_texto_celda)

    if df.empty:
        raise HTTPException(
            status_code=400, detail=f"{etiqueta}: no contiene filas de datos"
        )
    return df


def _leer_excel_subido(datos, etiqueta):
    """Lee un .xlsx/.xlsm subido (bytes) con pandas + openpyxl."""
    if not datos:
        raise HTTPException(status_code=400, detail=f"{etiqueta}: el archivo está vacío")

    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - entorno sin pandas
        raise HTTPException(
            status_code=503,
            detail="pandas no está instalado. Ejecuta: pip install pandas openpyxl",
        ) from exc

    try:
        df = pd.read_excel(
            io.BytesIO(datos),
            dtype=str,
            engine="openpyxl",
            keep_default_na=False,
        )
    except ImportError as exc:  # pragma: no cover - falta openpyxl
        raise HTTPException(
            status_code=503,
            detail="openpyxl no está instalado. Ejecuta: pip install openpyxl",
        ) from exc
    except Exception as exc:  # noqa: BLE001 - archivo corrupto o formato no soportado
        raise HTTPException(
            status_code=400,
            detail=f"{etiqueta}: no se pudo leer el Excel: {exc}",
        ) from exc

    return _normalizar_dataframe(df, etiqueta)


def _leer_matriz_subida(datos, nombre_archivo, etiqueta="matriz"):
    """
    Lee la matriz del estudiante según la extensión: CSV o Excel
    (.xlsx/.xlsm). Devuelve un DataFrame normalizado.
    """
    extension = Path(str(nombre_archivo or "")).suffix.lower()
    if extension == ".csv":
        return _leer_csv_subido(datos, etiqueta)
    if extension in (".xlsx", ".xlsm"):
        return _leer_excel_subido(datos, etiqueta)
    raise HTTPException(
        status_code=400,
        detail=(
            f"{etiqueta}: formato no soportado ({nombre_archivo!r}). "
            "Usa .csv, .xlsx o .xlsm."
        ),
    )


# Columnas toleradas en la matriz del nuevo flujo (sin exigir el formato
# enriquecido del endpoint histórico).
COLUMNAS_CODIGO_MATRIZ = (
    "codigo_curso",
    "codigo",
    "codigo_materia",
    "codigo_alfanumerico",
    "materias_aprobadas",
    "materias_por_ver",
    "cursos_aprobados",
    "aprobadas",
    "por_ver",
    "pendientes",
    "materia",
    "materias",
    "curso",
    "asignatura",
)
COLUMNAS_ID_MATRIZ = ("estudiante_id", "id_estudiante", "student_id")
COLUMNAS_ESTADO_MATRIZ = ("estado", "estatus", "situacion", "condicion")
COLUMNAS_CARRERA_MATRIZ = ("carrera", "programa", "plan")
COLUMNAS_SEMESTRE_MATRIZ = ("semestre", "nivel")


def _codigos_de_celda(valor) -> List[str]:
    """Extrae uno o varios códigos de una celda (tolera listas separadas)."""
    texto = _texto_celda(valor)
    if not texto:
        return []
    if any(sep in texto for sep in (",", ";", "|")):
        partes = re.split(r"[,;|]+", texto)
    else:
        partes = [texto]
    codigos = []
    for parte in partes:
        codigo = str(parte).strip().strip("[]'\" ").upper()
        if codigo:
            codigos.append(codigo)
    return codigos


def _parsear_matriz_perfil(df, estudiante_id_form=None):
    """
    Parser tolerante de la matriz del nuevo flujo.

    Acepta:
      * formato enriquecido ``estudiante_id, estado, codigo_curso, ...``;
      * una lista simple de materias (``codigo_curso`` / ``materia`` ...), con
        o sin columna ``estado``.

    ``estado`` vacío/ausente se interpreta como ``por_ver`` (materias que el
    estudiante aún debe ver), salvo que el nombre de la columna indique
    aprobadas (p. ej. ``materias_aprobadas``).

    Devuelve ``{estudiante_id, carrera, semestre, aprobadas, por_ver}`` o lanza
    HTTP 400 con el detalle de los errores.
    """
    if df is None or df.empty:
        raise HTTPException(
            status_code=400, detail="La matriz no contiene filas de datos"
        )

    columna_codigo = next(
        (c for c in COLUMNAS_CODIGO_MATRIZ if c in df.columns), None
    )
    if columna_codigo is None:
        # Fallback: cualquier encabezado que hable de códigos/materias/cursos
        # (evitando nombre, docente, id, créditos, etc.).
        for columna in df.columns:
            if not any(
                clave in columna
                for clave in ("codigo", "materia", "asignatura", "curso")
            ):
                continue
            if any(
                ruido in columna
                for ruido in (
                    "nombre",
                    "docente",
                    "id_",
                    "credito",
                    "semestre",
                    "estado",
                    "carrera",
                    "programa",
                    "plan",
                )
            ):
                continue
            columna_codigo = columna
            break
    if columna_codigo is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "La matriz no tiene una columna de código de materia. "
                "Se aceptan: " + ", ".join(COLUMNAS_CODIGO_MATRIZ)
            ),
        )

    columna_id = next((c for c in COLUMNAS_ID_MATRIZ if c in df.columns), None)
    columna_estado = next(
        (c for c in COLUMNAS_ESTADO_MATRIZ if c in df.columns), None
    )
    columna_carrera = next(
        (c for c in COLUMNAS_CARRERA_MATRIZ if c in df.columns), None
    )
    columna_semestre = next(
        (c for c in COLUMNAS_SEMESTRE_MATRIZ if c in df.columns), None
    )

    # Si no hay estado, el nombre de la columna de códigos decide el destino.
    estado_por_defecto = "por_ver"
    if "aprob" in columna_codigo:
        estado_por_defecto = "aprobada"
    elif "pend" in columna_codigo or "por_ver" in columna_codigo:
        estado_por_defecto = "por_ver"

    errores: List[str] = []
    estudiantes = set()
    aprobadas = set()
    por_ver = set()
    carrera = None
    semestre = None

    for posicion, fila in df.iterrows():
        linea = posicion + 2  # +1 índice 0, +1 encabezado

        if columna_id is not None:
            id_texto = _texto_celda(fila.get(columna_id))
            if id_texto:
                try:
                    estudiante_fila = int(id_texto)
                except (TypeError, ValueError):
                    errores.append(
                        f"línea {linea}: estudiante_id inválido ({id_texto!r})"
                    )
                    continue
                if estudiante_fila <= 0:
                    errores.append(
                        f"línea {linea}: estudiante_id debe ser positivo "
                        f"({estudiante_fila})"
                    )
                    continue
                estudiantes.add(estudiante_fila)

        codigos = _codigos_de_celda(fila.get(columna_codigo))
        if not codigos:
            continue

        estado_fila = None
        if columna_estado is not None:
            estado_texto = _texto_celda(fila.get(columna_estado))
            if estado_texto:
                estado_fila = _normalizar_estado(estado_texto)
                if estado_fila is None:
                    errores.append(
                        f"línea {linea}: estado no reconocido "
                        f"({estado_texto!r}); use aprobada/vista o "
                        "por_ver/pendiente"
                    )
                    continue
        if estado_fila is None:
            estado_fila = estado_por_defecto

        for codigo in codigos:
            if len(codigo) > 10:
                errores.append(
                    f"línea {linea}: codigo_curso supera VARCHAR(10) "
                    f"({codigo!r})"
                )
                continue
            if estado_fila == "aprobada":
                aprobadas.add(codigo)
            else:
                por_ver.add(codigo)

        if columna_carrera is not None and carrera is None:
            valor = _texto_celda(fila.get(columna_carrera))
            if valor:
                carrera = valor

        if columna_semestre is not None and semestre is None:
            valor = _texto_celda(fila.get(columna_semestre))
            if valor:
                semestre_fila = _parsear_semestre(valor)
                if semestre_fila is None:
                    errores.append(
                        f"línea {linea}: semestre inválido ({valor!r})"
                    )
                else:
                    semestre = semestre_fila

    if len(estudiantes) > 1:
        raise HTTPException(
            status_code=400,
            detail=(
                "La matriz contiene varios estudiantes distintos "
                f"({', '.join(str(e) for e in sorted(estudiantes))}). "
                "Sube una matriz por estudiante."
            ),
        )

    if estudiante_id_form is not None and estudiantes:
        if estudiante_id_form not in estudiantes:
            encontrado = next(iter(estudiantes))
            raise HTTPException(
                status_code=400,
                detail=(
                    f"El estudiante_id del formulario ({estudiante_id_form}) "
                    f"no coincide con el de la matriz ({encontrado})."
                ),
            )

    if errores:
        detalle = "\n".join(f"- {e}" for e in errores[:20])
        if len(errores) > 20:
            detalle += f"\n... y {len(errores) - 20} error(es) más"
        raise HTTPException(
            status_code=400,
            detail=f"La matriz contiene errores:\n{detalle}",
        )

    if not aprobadas and not por_ver:
        raise HTTPException(
            status_code=400,
            detail=(
                "La matriz no contiene materias válidas. Incluye una columna "
                "con códigos (codigo_curso/materia) y, opcionalmente, estado."
            ),
        )

    por_ver -= aprobadas

    if estudiante_id_form is not None:
        estudiante_id = estudiante_id_form
    elif estudiantes:
        estudiante_id = next(iter(estudiantes))
    else:
        estudiante_id = None

    return {
        "estudiante_id": estudiante_id,
        "carrera": carrera,
        "semestre": semestre,
        "aprobadas": sorted(aprobadas),
        "por_ver": sorted(por_ver),
    }


def _normalizar_hora(valor) -> Optional[str]:
    """
    Normaliza una hora a ``HH:MM:SS``. Acepta ``HH:MM``, ``HH:MM:SS`` y
    objetos ``datetime.time``. Lanza ValueError si el formato es inválido.
    """
    if valor is None:
        return None
    if hasattr(valor, "hour") and hasattr(valor, "minute"):
        return f"{int(valor.hour):02d}:{int(valor.minute):02d}:{int(getattr(valor, 'second', 0)):02d}"
    texto = str(valor).strip()
    if not texto:
        return None
    coincidencia = re.match(
        r"^(\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?$", texto
    )
    if not coincidencia:
        raise ValueError(f"hora inválida ({texto!r}); use HH:MM o HH:MM:SS")
    hora = int(coincidencia.group(1))
    minuto = int(coincidencia.group(2))
    segundo = int(coincidencia.group(3) or 0)
    if hora > 23 or minuto > 59 or segundo > 59:
        raise ValueError(f"hora fuera de rango ({texto!r})")
    return f"{hora:02d}:{minuto:02d}:{segundo:02d}"


def _hora_a_segundos(hora: str) -> int:
    """Convierte 'HH:MM:SS' a segundos desde medianoche (0 si es inválida)."""
    partes = str(hora or "").split(":")
    try:
        return int(partes[0]) * 3600 + int(partes[1]) * 60 + int(partes[2])
    except (IndexError, ValueError):
        return 0


def _modelo_a_dict(modelo) -> Dict[str, Any]:
    """Pydantic v1/v2: dict solo con los campos enviados por el cliente."""
    if hasattr(modelo, "model_dump"):
        return modelo.model_dump(exclude_unset=True)
    return modelo.dict(exclude_unset=True)


def _texto_opcional(valor) -> Optional[str]:
    """Texto recortado; cadena vacía/None -> None."""
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def _codigo_unico_texto(cur, tabla: str, columna: str, prefijo: str, nombre: str) -> str:
    """
    Genera un código legible y único para ``docente``/``sede`` a partir del
    nombre (p. ej. ``DOC-JUAN-PEREZ``). Solo se usa cuando el dashboard edita
    un nombre y el NRC aún no tiene id de docente/sede.
    """
    if (tabla, columna) not in (("docente", "id_docente"), ("sede", "codigo")):
        raise ValueError("Tabla/columna no permitida")

    if tabla == "sede":
        # ``sede.codigo`` puede ser VARCHAR(10) si la tabla la creó la ingesta;
        # un código corto y único evita "value too long".
        sufijo = 1
        while True:
            candidato = f"{prefijo}-{sufijo}"[:10]
            cur.execute(
                f"SELECT 1 FROM {tabla} WHERE {columna} = %s", (candidato,)
            )
            if cur.fetchone() is None:
                return candidato
            sufijo += 1

    base = re.sub(r"[^A-Z0-9]+", "-", _clave_texto(nombre)).strip("-")[:24]
    base = base or "SIN-NOMBRE"
    candidato = f"{prefijo}-{base}"
    sufijo = 1
    while True:
        cur.execute(
            f"SELECT 1 FROM {tabla} WHERE {columna} = %s", (candidato,)
        )
        if cur.fetchone() is None:
            return candidato
        sufijo += 1
        candidato = f"{prefijo}-{base}-{sufijo}"


def _items_de_cuerpo(body, claves: Tuple[str, ...]) -> List[Any]:
    """
    Acepta una lista directa o un objeto que la envuelva en alguna de
    ``claves`` (p. ej. ``{"cursos": [...]}`` o ``{"nrcs": [...]}``).
    """
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for clave in claves:
            valor = body.get(clave)
            if isinstance(valor, list):
                return valor
        raise HTTPException(
            status_code=400,
            detail=(
                "Se esperaba una lista de elementos "
                f"(o un objeto con la clave {claves[0]!r})."
            ),
        )
    raise HTTPException(
        status_code=400, detail="El cuerpo debe ser una lista de elementos."
    )


def _convertir_items(items_raw: List[Any], modelo):
    """Convierte dicts a ``modelo`` (Pydantic) con errores 400 legibles."""
    convertidos = []
    for posicion, raw in enumerate(items_raw):
        if isinstance(raw, modelo):
            convertidos.append(raw)
        elif isinstance(raw, dict):
            try:
                convertidos.append(modelo(**raw))
            except Exception as exc:  # noqa: BLE001 - validación de entrada
                raise HTTPException(
                    status_code=400,
                    detail=f"Elemento {posicion} inválido: {exc}",
                ) from exc
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Elemento {posicion} inválido: se esperaba un objeto.",
            )
    return convertidos


# ---------------------------------------------------------------------------
# Modelos de entrada
# ---------------------------------------------------------------------------
class InscripcionIn(BaseModel):
    estudiante_id: int
    nrc_id: int


class HorarioAutomaticoIn(BaseModel):
    estudiante_id: int


class LoginIn(BaseModel):
    """
    Cuerpo de POST /api/auth/login.

    * ``rol='admin'``: requiere ``usuario`` y ``password``.
    * ``rol='estudiante'``: requiere ``estudiante_id`` (entero positivo).

    ``estudiante_id`` se recibe como ``Any`` a propósito para poder devolver un
    400 con un mensaje claro (en lugar del 422 genérico de la validación
    automática) cuando el valor no es un entero positivo.
    """

    rol: str
    usuario: Optional[str] = None
    password: Optional[str] = None
    estudiante_id: Optional[Any] = None


class ReglaNegocioIn(BaseModel):
    """Regla dinámica: {materia: 'CAL-201', prerrequisitos: ['CAL-101']}."""

    materia: str
    prerrequisitos: List[str] = Field(default_factory=list)


class CursoMallaIn(BaseModel):
    """Curso editable de la malla (dashboard del administrador)."""

    programa: Optional[str] = None
    sede: Optional[str] = None
    semestre: Optional[int] = None
    codigo: str
    nombre: Optional[str] = None
    creditos: Optional[int] = None
    horas: Optional[int] = None
    prerrequisitos: List[str] = Field(default_factory=list)


class HorarioNrcIn(BaseModel):
    """Sesión de horario editable de un NRC."""

    numero_sesion: Optional[int] = None
    dia: int
    hora_inicio: Union[str, int, float]
    hora_fin: Union[str, int, float]
    salon: Optional[str] = None
    edificio: Optional[str] = None


class NrcIn(BaseModel):
    """NRC editable desde GET/PUT /api/admin/oferta."""

    id_nrc: int
    curso_id: Optional[str] = None
    periodo: Optional[str] = None
    docente_id: Optional[str] = None
    docente_nombre: Optional[str] = None
    sede_id: Optional[str] = None
    sede_nombre: Optional[str] = None
    jornada: Optional[str] = None
    modalidad: Optional[str] = None
    cupo_max: Optional[int] = None
    inscritos: Optional[int] = None
    saldo: Optional[int] = None
    estado: Optional[str] = None
    horario: Optional[List[HorarioNrcIn]] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/")
def raiz():
    return {
        "servicio": "Sistema de Inscripción de Materias — API",
        "endpoints": [
            "/api/auth/login",
            "/api/oferta",
            "/api/inscribir",
            "/api/horario/{id}",
            "/api/estudiante/horario/{id}",
            "/api/horario/automatico",
            "/api/catalogos",
            "/api/estudiante/perfil",
            "/api/estudiante/{id}/perfil",
            "/api/admin/estudiantes",
            "/api/admin/reglas",
            "/api/admin/malla",
            "/api/admin/oferta",
            "/api/admin/upload-oferta",
            "/api/admin/upload-malla",
            "/api/estudiante/upload-matriz",
            "/api/estudiante/{id}/matriz",
        ],
    }


# Credenciales fijas del administrador (contrato del login del frontend).
# En esta entrega no hay JWT ni middleware: el token es un identificador de
# sesión opaco que el cliente puede guardar, pero el resto de endpoints NO lo
# exigen (así el frontend actual sigue funcionando sin adjuntarlo).
ADMIN_USUARIO = "admin"
ADMIN_PASSWORD = "Hola12345"
PLAN_POR_DEFECTO = "IS-2024"


def _token_sesion() -> str:
    """Genera un token de sesión opaco e impredecible (32 bytes en base64url)."""
    return secrets.token_urlsafe(32)


def _entero_positivo_login(valor: Any) -> Optional[int]:
    """
    Normaliza ``estudiante_id`` a un entero positivo.

    Acepta enteros JSON y cadenas numéricas (por ejemplo ``"7"``), pero rechaza
    ``null``, booleanos, decimales, negativos y texto no numérico. Devuelve
    ``None`` cuando el valor no es válido para que el endpoint responda 400.
    """
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, int):
        return valor if valor > 0 else None
    if isinstance(valor, str):
        texto = valor.strip()
        if texto.isdigit():
            numero = int(texto)
            return numero if numero > 0 else None
    return None


@app.post("/api/auth/login")
def login(body: LoginIn):
    """
    Inicia sesión como administrador o como estudiante.

    Administrador
    -------------
    Body: ``{"rol": "admin", "usuario": "admin", "password": "Hola12345"}``.
    Si las credenciales coinciden devuelve 200 con
    ``{ok, rol: "admin", token, usuario}``; si no, 401 con un detalle claro.

    Estudiante
    ----------
    Body: ``{"rol": "estudiante", "estudiante_id": 1}``. Si el estudiante
    existe devuelve ``{ok, rol, token, estudiante_id, nombre, nuevo: false}``.
    Si no existe, lo crea con nombre ``"Estudiante <id>"`` y
    ``plan_id="IS-2024"`` (Registro Implícito) y responde ``nuevo: true``.
    Un ``estudiante_id`` que no sea entero positivo devuelve 400.

    El ``token`` es una cadena opaca de ``secrets.token_urlsafe(32)``; sirve
    como identificador de sesión, no es un JWT y no se valida en el resto de
    endpoints en esta versión.
    """
    rol = str(body.rol or "").strip().lower()

    # ------------------------------------------------------------------
    # Administrador: credenciales fijas del contrato.
    # ------------------------------------------------------------------
    if rol == "admin":
        usuario = str(body.usuario or "").strip()
        password = str(body.password or "")
        credenciales_ok = secrets.compare_digest(
            usuario.encode("utf-8"), ADMIN_USUARIO.encode("utf-8")
        ) and secrets.compare_digest(
            password.encode("utf-8"), ADMIN_PASSWORD.encode("utf-8")
        )
        if not credenciales_ok:
            raise HTTPException(
                status_code=401,
                detail=(
                    "Usuario o contraseña de administrador incorrectos. "
                    "Verifica las credenciales e inténtalo de nuevo."
                ),
                headers={"WWW-Authenticate": "Bearer"},
            )
        return {
            "ok": True,
            "rol": "admin",
            "token": _token_sesion(),
            "usuario": ADMIN_USUARIO,
        }

    # ------------------------------------------------------------------
    # Estudiante: alta implícita si el ID no existe.
    # ------------------------------------------------------------------
    if rol == "estudiante":
        sid = _entero_positivo_login(body.estudiante_id)
        if sid is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "El campo 'estudiante_id' debe ser un entero positivo "
                    "(por ejemplo 1)."
                ),
            )

        conn = conectar()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, nombre FROM estudiante WHERE id = %s", (sid,)
                )
                fila = cur.fetchone()

                if fila is not None:
                    nombre = fila["nombre"] or f"Estudiante {sid}"
                    nuevo = False
                else:
                    nombre = f"Estudiante {sid}"
                    cur.execute(
                        """
                        INSERT INTO estudiante (id, nombre, plan_id)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (sid, nombre, PLAN_POR_DEFECTO),
                    )
                    nuevo = cur.rowcount > 0

                    # Si otra petición creó el mismo ID a la vez, se conserva
                    # el nombre ya guardado y se informa nuevo: false.
                    cur.execute(
                        "SELECT id, nombre FROM estudiante WHERE id = %s",
                        (sid,),
                    )
                    fila = cur.fetchone()
                    if fila is not None and fila["nombre"]:
                        nombre = fila["nombre"]

                    # Mantener la secuencia al día tras insertar un ID manual.
                    cur.execute(
                        "SELECT pg_get_serial_sequence("
                        "'estudiante', 'id') AS secuencia"
                    )
                    fila_secuencia = cur.fetchone()
                    secuencia = (
                        fila_secuencia["secuencia"] if fila_secuencia else None
                    )
                    if secuencia:
                        cur.execute(
                            "SELECT setval(%s::regclass, "
                            "(SELECT GREATEST(COALESCE(MAX(id), 0), 1) "
                            "FROM estudiante), true)",
                            (secuencia,),
                        )
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001 - se informa con detalle
            conn.rollback()
            logger.exception(
                "Error en el login/registro implícito del estudiante %s", sid
            )
            if getattr(exc, "pgcode", None) == "42P01":
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Falta la tabla 'estudiante' en PostgreSQL. El backend "
                        "la crea al arrancar con init_db.py; reinicia el "
                        "contenedor o ejecuta 'python init_db.py'. "
                        f"Detalle: {exc}"
                    ),
                ) from exc
            raise HTTPException(
                status_code=500,
                detail=(
                    "No se pudo iniciar la sesión del estudiante en "
                    f"PostgreSQL: {exc}"
                ),
            ) from exc
        finally:
            conn.close()

        return {
            "ok": True,
            "rol": "estudiante",
            "token": _token_sesion(),
            "estudiante_id": sid,
            "nombre": nombre,
            "nuevo": nuevo,
        }

    raise HTTPException(
        status_code=400,
        detail="Rol inválido. Usa 'admin' o 'estudiante'.",
    )


@app.get("/api/oferta")
def oferta():
    """
    Retorna los NRC ofertados con su materia, créditos, cupo e inscritos.
    Incluye la información del docente/sede y la lista de horarios (sesiones)
    de cada NRC.

    ``inscritos`` usa el contador del NRC (cargado desde la oferta) y, si no
    existe, cae al conteo real de ``inscripcion``.
    """
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    n.id_nrc,
                    n.curso_id,
                    c.nombre      AS materia,
                    c.nombre      AS nombre_curso,
                    c.creditos,
                    n.periodo,
                    n.docente_id,
                    d.nombre      AS docente_nombre,
                    n.sede_id,
                    s.nombre      AS sede_nombre,
                    n.jornada,
                    n.modalidad,
                    n.cupo_max,
                    n.estado,
                    GREATEST(
                        COALESCE(n.inscritos, 0), COALESCE(i.total, 0)
                    )                                             AS inscritos,
                    COALESCE(n.cupo_max, 0)
                        - GREATEST(
                            COALESCE(n.inscritos, 0), COALESCE(i.total, 0)
                        )                                         AS cupos_disponibles,
                    COALESCE(
                        n.saldo,
                        COALESCE(n.cupo_max, 0)
                            - GREATEST(
                                COALESCE(n.inscritos, 0), COALESCE(i.total, 0)
                            )
                    )                                             AS saldo
                FROM nrc n
                JOIN curso c ON c.codigo_alfanumerico = n.curso_id
                LEFT JOIN docente d ON d.id_docente = n.docente_id
                LEFT JOIN sede s    ON s.codigo = n.sede_id
                LEFT JOIN (
                    SELECT nrc_id, COUNT(*) AS total
                    FROM inscripcion
                    GROUP BY nrc_id
                ) i ON i.nrc_id = n.id_nrc
                ORDER BY n.id_nrc
                """
            )
            nrcs = cur.fetchall()
    finally:
        conn.close()

    horarios = horarios_por_nrc()
    for nrc in nrcs:
        nrc["disponible"] = nrc["cupos_disponibles"] > 0
        nrc["horario"] = horarios.get(nrc["id_nrc"], [])
    return nrcs


# ---------------------------------------------------------------------------
# Carga de archivos — Administrador (oferta Excel/CSV, malla PDF) y
# Estudiante (matriz CSV/Excel)
# ---------------------------------------------------------------------------
EXTENSIONES_OFERTA = (".xlsx", ".xlsm", ".csv")


@app.post("/api/admin/upload-oferta")
async def upload_oferta(archivo: UploadFile = File(...)):
    """
    Sube la oferta académica y puebla PostgreSQL (curso, nrc, horario).

    Acepta ``.xlsx``/``.xlsm`` (flujo nuevo, una hoja con cursos/NRC/sesiones)
    y ``.csv`` (compatibilidad con el flujo anterior). El archivo se guarda en
    ``UPLOADS_DIR`` con un nombre único y se ejecuta la ingesta del Data Lake
    con el mismo intérprete de la API (``sys.executable``) y ruta absoluta:

      * Excel: ``--excel <archivo> --solo-oferta``
      * CSV:   ``--oferta <archivo> --solo-oferta``

    Devuelve ``{ok, archivo, cursos, nrcs, sesiones, docentes, sedes, salida}``.
    El modo ``--solo-oferta`` no lee ni vacía ``historial`` (aprobados intactos).
    Si la ingesta falla se devuelve HTTP 400 con ``{mensaje, archivo, salida}``.
    """
    destino = await guardar_upload(archivo, "oferta", EXTENSIONES_OFERTA)

    script = ruta_script_ingesta()
    if script is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "No se encontró datalake/ingesta_datalake.py. Se buscó en "
                f"{BASE_DIR / 'datalake'} y {BASE_DIR.parent / 'datalake'} "
                "(configura INGESTA_SCRIPT para indicar otra ruta)."
            ),
        )

    if destino.suffix.lower() == ".csv":
        comando = [
            sys.executable,
            str(script),
            "--oferta",
            str(destino),
            "--solo-oferta",
        ]
        etiqueta = "la oferta académica (CSV)"
    else:
        comando = [
            sys.executable,
            str(script),
            "--excel",
            str(destino),
            "--solo-oferta",
        ]
        etiqueta = "la oferta académica (Excel)"

    salida = _ejecutar_ingesta(
        comando, etiqueta, destino, cwd=script.parent
    )
    resumen = _extraer_resumen_json(salida)

    cursos = _contar_resumen_o_tabla(resumen, salida, "cursos", "curso")
    nrcs = _contar_resumen_o_tabla(resumen, salida, "nrcs", "nrc")
    sesiones = _contar_resumen_o_tabla(resumen, salida, "sesiones", "horario")
    docentes = _contar_resumen_o_tabla(resumen, salida, "docentes", "docente")
    sedes = _contar_resumen_o_tabla(resumen, salida, "sedes", "sede")

    logger.info(
        "Oferta cargada desde %s: %s cursos, %s NRC, %s sesiones, "
        "%s docentes, %s sedes",
        destino,
        cursos,
        nrcs,
        sesiones,
        docentes,
        sedes,
    )
    return {
        "ok": True,
        "archivo": str(destino),
        "cursos": cursos,
        "nrcs": nrcs,
        "sesiones": sesiones,
        "docentes": docentes,
        "sedes": sedes,
        "salida": salida,
    }


@app.post("/api/admin/upload-malla")
async def upload_malla(archivo: UploadFile = File(...)):
    """
    Sube el PDF de la malla curricular y la ingesta en el Data Lake.

    Flujo:
      1. Guarda el PDF en ``UPLOADS_DIR`` (``malla_<timestamp>_<uuid>.pdf``).
      2. Ejecuta ``datalake/ingesta_datalake.py --pdf <archivo> --solo-malla``
         con ``sys.executable`` y ruta absoluta.
      3. Devuelve ``{ok, archivo, malla_cursos, programas, salida}``.

    La malla queda en MongoDB (``malla_curricular``) y los prerrequisitos en
    ``reglas_negocio``. Si la ingesta falla se devuelve HTTP 400 con detalle.
    """
    destino = await guardar_upload(archivo, "malla", (".pdf",))

    script = ruta_script_ingesta()
    if script is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "No se encontró datalake/ingesta_datalake.py. Se buscó en "
                f"{BASE_DIR / 'datalake'} y {BASE_DIR.parent / 'datalake'} "
                "(configura INGESTA_SCRIPT para indicar otra ruta)."
            ),
        )

    comando = [
        sys.executable,
        str(script),
        "--pdf",
        str(destino),
        "--solo-malla",
    ]
    salida = _ejecutar_ingesta(
        comando, "la malla curricular (PDF)", destino, cwd=script.parent
    )
    resumen = _extraer_resumen_json(salida)

    malla_cursos = _contar_resumen(resumen, salida, "malla_cursos")
    if not malla_cursos and "malla_cursos" not in resumen:
        malla_cursos = _contar_malla_mongo()

    try:
        programas_lista = _programas_malla()
    except HTTPException:
        programas_lista = []

    programas = _contar_resumen(resumen, salida, "programas")
    if not programas:
        programas = len(programas_lista)

    logger.info(
        "Malla cargada desde %s: %s cursos, %s programa(s)",
        destino,
        malla_cursos,
        programas,
    )
    return {
        "ok": True,
        "archivo": str(destino),
        "malla_cursos": malla_cursos,
        # Alias que consume el panel de administración (tarjeta de resumen);
        # el contrato principal sigue siendo ``malla_cursos``.
        "cursos": malla_cursos,
        "programas": programas,
        # Extra útil para el dashboard (la clave ``programas`` es un conteo).
        "programas_lista": programas_lista,
        "salida": salida,
    }


@app.get("/api/admin/malla")
def listar_malla(
    plano: Optional[bool] = None,
    formato: Optional[str] = None,
):
    """
    Lista la malla curricular desde MongoDB (``malla_curricular``).

    Formato por defecto (agrupado)::

        {
          "ok": true,
          "programa": "INGENIERÍA DE SISTEMAS" | null,
          "programas": ["..."],
          "total": 59,
          "semestres": [
            {"semestre": 1, "cursos": [
              {"codigo", "nombre", "creditos", "horas", "prerrequisitos"}
            ]},
            ...
          ]
        }

    Los semestres se ordenan de forma ascendente y los cursos por código; los
    cursos sin semestre quedan al final (``semestre: null``).

    Compatibilidad: ``?plano=1`` o ``?formato=plano`` devuelven la lista plana
    histórica de ``_cursos_malla()``, tal como la consume el dashboard anterior.
    """
    if plano or (formato or "").strip().lower() == "plano":
        return _cursos_malla()

    cursos = _cursos_malla()
    if not cursos:
        return {
            "ok": True,
            "programa": None,
            "programas": [],
            "total": 0,
            "semestres": [],
        }

    # Programas distintos respetando el orden alfabético tolerante a acentos.
    programas: List[str] = []
    vistos = set()
    for curso in cursos:
        valor = _texto_opcional(curso.get("programa"))
        if valor and valor not in vistos:
            vistos.add(valor)
            programas.append(valor)
    programas.sort(key=_clave_texto)

    # Agrupa por semestre. Un semestre ausente/no entero se normaliza a None
    # para que el JSON lo exponga como null y quede al final.
    grupos: Dict[Optional[int], List[Dict[str, Any]]] = {}
    for curso in cursos:
        semestre = curso.get("semestre")
        if isinstance(semestre, bool) or not isinstance(semestre, int):
            semestre = None
        grupos.setdefault(semestre, []).append(
            {
                "codigo": curso.get("codigo"),
                "nombre": curso.get("nombre"),
                "creditos": curso.get("creditos"),
                "horas": curso.get("horas"),
                "prerrequisitos": curso.get("prerrequisitos") or [],
            }
        )

    def _clave_semestre(valor: Optional[int]):
        return (1, 0) if valor is None else (0, valor)

    semestres = []
    for semestre in sorted(grupos, key=_clave_semestre):
        cursos_semestre = sorted(
            grupos[semestre],
            key=lambda curso: str(curso.get("codigo") or ""),
        )
        semestres.append({"semestre": semestre, "cursos": cursos_semestre})

    return {
        "ok": True,
        "programa": programas[0] if programas else None,
        "programas": programas,
        "total": len(cursos),
        "semestres": semestres,
    }


@app.put("/api/admin/malla")
def actualizar_malla(body: Union[List[CursoMallaIn], Dict[str, Any]]):
    """
    Upsert de la malla curricular editable por el administrador.

    Recibe una lista de cursos (o ``{"cursos": [...]}``). El upsert se hace por
    ``programa + codigo`` en ``malla_curricular`` y, para que el grafo/negocio
    quede coherente, también se actualiza ``reglas_negocio`` con
    ``materia = codigo`` y ``prerrequisitos`` del curso.

    Devuelve ``{ok, total, insertados, actualizados, reglas_actualizadas}``.
    No elimina cursos que no vengan en la lista.
    """
    if (
        isinstance(body, dict)
        and body.get("codigo")
        and "cursos" not in body
        and "malla" not in body
    ):
        items_raw = [body]
    else:
        items_raw = _items_de_cuerpo(body, ("cursos", "malla"))
    items = _convertir_items(items_raw, CursoMallaIn)

    db = mongo_db()
    col_malla = db[MONGO_COL_MALLA]
    col_reglas = db[MONGO_COL_REGLAS]

    insertados = 0
    actualizados = 0
    reglas_actualizadas = 0
    try:
        for posicion, item in enumerate(items):
            codigo = str(item.codigo or "").strip().upper()
            if not codigo:
                raise HTTPException(
                    status_code=400,
                    detail=f"Elemento {posicion}: 'codigo' es obligatorio.",
                )
            for campo in ("semestre", "creditos", "horas"):
                valor = getattr(item, campo)
                if valor is not None and valor < 0:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Elemento {posicion} ({codigo}): '{campo}' no "
                            "puede ser negativo."
                        ),
                    )

            programa = _texto_opcional(item.programa)
            if programa is None:
                # Si el dashboard no envía programa, conserva el del curso
                # existente para no crear un duplicado huérfano.
                existente = col_malla.find_one(
                    {"codigo": codigo}, {"programa": 1}
                )
                if existente and existente.get("programa"):
                    programa = existente["programa"]

            prerrequisitos = _lista_prerrequisitos(item.prerrequisitos)
            documento = {
                "programa": programa,
                "sede": _texto_opcional(item.sede),
                "semestre": item.semestre,
                "codigo": codigo,
                "nombre": _texto_opcional(item.nombre),
                "creditos": item.creditos,
                "horas": item.horas,
                "prerrequisitos": prerrequisitos,
            }
            if _actualizar_curso_malla_anidado(
                col_malla, programa, codigo, documento
            ):
                actualizados += 1
            else:
                try:
                    resultado = col_malla.update_one(
                        {"programa": programa, "codigo": codigo},
                        {"$set": documento},
                        upsert=True,
                    )
                    if resultado.upserted_id is not None:
                        insertados += 1
                    else:
                        actualizados += 1
                except DuplicateKeyError:
                    # Si la colección tiene un índice único por ``codigo``
                    # (sin programa), actualiza ese documento en lugar de
                    # fallar al insertar un duplicado.
                    col_malla.update_one(
                        {"codigo": codigo}, {"$set": documento}
                    )
                    actualizados += 1

            datos_regla: Dict[str, Any] = {
                "prerrequisitos": prerrequisitos
            }
            if item.nombre is not None:
                datos_regla["nombre"] = _texto_opcional(item.nombre)
            if item.creditos is not None:
                datos_regla["creditos"] = item.creditos
            if item.semestre is not None:
                datos_regla["semestre"] = item.semestre
            col_reglas.update_one(
                {"materia": codigo},
                {
                    "$set": datos_regla,
                    "$setOnInsert": {"materia": codigo},
                },
                upsert=True,
            )
            reglas_actualizadas += 1
    except HTTPException:
        raise
    except PyMongoError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"No se pudo guardar la malla en MongoDB: {exc}",
        ) from exc

    return {
        "ok": True,
        "total": len(items),
        "insertados": insertados,
        "actualizados": actualizados,
        "reglas_actualizadas": reglas_actualizadas,
    }


@app.get("/api/admin/oferta")
def listar_oferta_admin():
    """
    Lista los NRC desde PostgreSQL para el dashboard editable.

    Cada elemento incluye curso, materia, créditos, docente/sede (id y
    nombre), jornada, modalidad, cupo, inscritos, saldo, estado, periodo y su
    arreglo ``horario[]`` con las sesiones (día, horas, salón, edificio).
    """
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    n.id_nrc,
                    n.curso_id,
                    n.curso_id AS curso,
                    c.nombre AS materia,
                    c.nombre AS nombre_curso,
                    c.creditos,
                    n.periodo,
                    n.docente_id,
                    d.nombre AS docente_nombre,
                    n.sede_id,
                    s.nombre AS sede_nombre,
                    n.jornada,
                    n.modalidad,
                    n.cupo_max,
                    GREATEST(
                        COALESCE(n.inscritos, 0), COALESCE(i.total, 0)
                    ) AS inscritos,
                    CASE
                        WHEN n.cupo_max IS NULL THEN NULL
                        ELSE COALESCE(
                            n.saldo,
                            n.cupo_max - GREATEST(
                                COALESCE(n.inscritos, 0),
                                COALESCE(i.total, 0)
                            )
                        )
                    END AS saldo,
                    n.estado
                FROM nrc n
                JOIN curso c ON c.codigo_alfanumerico = n.curso_id
                LEFT JOIN docente d ON d.id_docente = n.docente_id
                LEFT JOIN sede s    ON s.codigo = n.sede_id
                LEFT JOIN (
                    SELECT nrc_id, COUNT(*) AS total
                    FROM inscripcion
                    GROUP BY nrc_id
                ) i ON i.nrc_id = n.id_nrc
                ORDER BY n.id_nrc
                """
            )
            nrcs = cur.fetchall()
    finally:
        conn.close()

    horarios = horarios_por_nrc()
    for nrc in nrcs:
        nrc["horario"] = horarios.get(nrc["id_nrc"], [])
    return nrcs


@app.put("/api/admin/oferta")
def actualizar_oferta_admin(body: Union[List[NrcIn], Dict[str, Any]]):
    """
    Actualiza NRC y horario desde el dashboard, en una sola transacción.

    Acepta una lista de NRC (o ``{"nrcs": [...]}``). Solo se modifican los
    campos enviados; ``horario``, si viene, reemplaza todas las sesiones de ese
    NRC (una lista vacía las elimina).

    Validaciones mínimas: ``id_nrc`` existente, ``curso_id`` existente,
    ``cupo_max``/``inscritos`` no negativos, ``dia`` entre 1 y 7 y horas
    ``HH:MM[:SS]`` con ``hora_fin > hora_inicio``.

    Devuelve ``{ok, total, actualizados, sesiones}``.
    """
    if (
        isinstance(body, dict)
        and body.get("id_nrc") is not None
        and "nrcs" not in body
        and "oferta" not in body
    ):
        items_raw = [body]
    else:
        items_raw = _items_de_cuerpo(body, ("nrcs", "oferta"))
    items = _convertir_items(items_raw, NrcIn)

    vistos = set()
    for item in items:
        if item.id_nrc in vistos:
            raise HTTPException(
                status_code=400,
                detail=f"El NRC {item.id_nrc} está repetido en el cuerpo.",
            )
        vistos.add(item.id_nrc)

    conn = conectar()
    actualizados = 0
    sesiones_guardadas = 0
    try:
        with conn.cursor() as cur:
            for item in items:
                datos = _modelo_a_dict(item)
                horario_incluido = "horario" in datos
                horario_enviado = datos.pop("horario", None)
                datos.pop("id_nrc", None)

                cur.execute(
                    """
                    SELECT id_nrc, curso_id, periodo, docente_id, sede_id,
                           jornada, modalidad, cupo_max, inscritos, saldo, estado
                    FROM nrc
                    WHERE id_nrc = %s
                    FOR UPDATE
                    """,
                    (item.id_nrc,),
                )
                actual = cur.fetchone()
                if actual is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"El NRC {item.id_nrc} no existe.",
                    )

                if "curso_id" in datos:
                    curso_id = _texto_opcional(datos["curso_id"])
                    if not curso_id:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"NRC {item.id_nrc}: 'curso_id' no puede "
                                "estar vacío."
                            ),
                        )
                    curso_id = curso_id.upper()
                    cur.execute(
                        "SELECT 1 FROM curso "
                        "WHERE codigo_alfanumerico = %s",
                        (curso_id,),
                    )
                    if cur.fetchone() is None:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"NRC {item.id_nrc}: el curso {curso_id!r} "
                                "no existe en 'curso'."
                            ),
                        )
                else:
                    curso_id = actual["curso_id"]

                cupo_max = (
                    datos["cupo_max"]
                    if "cupo_max" in datos
                    else actual["cupo_max"]
                )
                inscritos = (
                    datos["inscritos"]
                    if "inscritos" in datos
                    else actual["inscritos"]
                )
                if cupo_max is not None and int(cupo_max) < 0:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"NRC {item.id_nrc}: 'cupo_max' no puede ser "
                            "negativo."
                        ),
                    )
                if inscritos is not None and int(inscritos) < 0:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"NRC {item.id_nrc}: 'inscritos' no puede ser "
                            "negativo."
                        ),
                    )

                cur.execute(
                    "SELECT COUNT(*) AS total FROM inscripcion "
                    "WHERE nrc_id = %s",
                    (item.id_nrc,),
                )
                inscritos_local = int(cur.fetchone()["total"] or 0)
                inscritos_efectivos = max(
                    int(inscritos or 0), inscritos_local
                )
                if "saldo" in datos and datos["saldo"] is not None:
                    saldo = int(datos["saldo"])
                else:
                    saldo = (
                        int(cupo_max) - inscritos_efectivos
                        if cupo_max is not None
                        else None
                    )

                texto = {
                    campo: (
                        _texto_opcional(datos[campo])
                        if campo in datos
                        else actual[campo]
                    )
                    for campo in (
                        "periodo",
                        "docente_id",
                        "sede_id",
                        "jornada",
                        "modalidad",
                        "estado",
                    )
                }

                # El dashboard edita ``docente_nombre``/``sede_nombre`` (no
                # envía los ids). Se reflejan en las tablas docente/sede y se
                # enlazan al NRC para que el cambio persista.
                if "docente_nombre" in datos:
                    nombre_docente = _texto_opcional(datos["docente_nombre"])
                    if nombre_docente is None:
                        texto["docente_id"] = None
                    else:
                        docente_id = texto.get("docente_id")
                        if not docente_id:
                            cur.execute(
                                "SELECT id_docente FROM docente "
                                "WHERE UPPER(BTRIM(nombre)) = UPPER(BTRIM(%s)) "
                                "ORDER BY id_docente LIMIT 1",
                                (nombre_docente,),
                            )
                            fila_docente = cur.fetchone()
                            if fila_docente:
                                docente_id = fila_docente["id_docente"]
                            else:
                                docente_id = _codigo_unico_texto(
                                    cur,
                                    "docente",
                                    "id_docente",
                                    "DOC",
                                    nombre_docente,
                                )
                        cur.execute(
                            """
                            INSERT INTO docente (id_docente, nombre)
                            VALUES (%s, %s)
                            ON CONFLICT (id_docente) DO UPDATE
                                SET nombre = EXCLUDED.nombre
                            """,
                            (docente_id, nombre_docente),
                        )
                        texto["docente_id"] = docente_id

                if "sede_nombre" in datos:
                    nombre_sede = _texto_opcional(datos["sede_nombre"])
                    if nombre_sede is None:
                        texto["sede_id"] = None
                    else:
                        sede_id = texto.get("sede_id")
                        if not sede_id:
                            cur.execute(
                                "SELECT codigo FROM sede "
                                "WHERE UPPER(BTRIM(nombre)) = UPPER(BTRIM(%s)) "
                                "ORDER BY codigo LIMIT 1",
                                (nombre_sede,),
                            )
                            fila_sede = cur.fetchone()
                            if fila_sede:
                                sede_id = fila_sede["codigo"]
                            else:
                                sede_id = _codigo_unico_texto(
                                    cur, "sede", "codigo", "SEDE", nombre_sede
                                )
                        cur.execute(
                            """
                            INSERT INTO sede (codigo, nombre)
                            VALUES (%s, %s)
                            ON CONFLICT (codigo) DO UPDATE
                                SET nombre = EXCLUDED.nombre
                            """,
                            (sede_id, nombre_sede),
                        )
                        texto["sede_id"] = sede_id

                cur.execute(
                    """
                    UPDATE nrc SET
                        curso_id   = %s,
                        periodo    = %s,
                        docente_id = %s,
                        sede_id    = %s,
                        jornada    = %s,
                        modalidad  = %s,
                        cupo_max   = %s,
                        inscritos  = %s,
                        saldo      = %s,
                        estado     = %s
                    WHERE id_nrc = %s
                    """,
                    (
                        curso_id,
                        texto["periodo"],
                        texto["docente_id"],
                        texto["sede_id"],
                        texto["jornada"],
                        texto["modalidad"],
                        int(cupo_max) if cupo_max is not None else None,
                        int(inscritos) if inscritos is not None else None,
                        saldo,
                        texto["estado"],
                        item.id_nrc,
                    ),
                )

                if horario_incluido and horario_enviado is not None:
                    sesiones = []
                    for posicion, sesion in enumerate(horario_enviado or []):
                        if not isinstance(sesion, dict):
                            sesion = _modelo_a_dict(sesion)
                        dia = sesion.get("dia")
                        if dia is None:
                            raise HTTPException(
                                status_code=400,
                                detail=(
                                    f"NRC {item.id_nrc}: la sesión {posicion + 1} "
                                    "no tiene 'dia'."
                                ),
                            )
                        try:
                            dia = int(dia)
                        except (TypeError, ValueError) as exc:
                            raise HTTPException(
                                status_code=400,
                                detail=(
                                    f"NRC {item.id_nrc}: 'dia' inválido en la "
                                    f"sesión {posicion + 1} ({dia!r})."
                                ),
                            ) from exc
                        if dia < 1 or dia > 7:
                            raise HTTPException(
                                status_code=400,
                                detail=(
                                    f"NRC {item.id_nrc}: 'dia' debe estar entre "
                                    f"1 y 7 (sesión {posicion + 1})."
                                ),
                            )

                        try:
                            hora_inicio = _normalizar_hora(
                                sesion.get("hora_inicio")
                            )
                            hora_fin = _normalizar_hora(sesion.get("hora_fin"))
                        except ValueError as exc:
                            raise HTTPException(
                                status_code=400,
                                detail=(
                                    f"NRC {item.id_nrc}: {exc} "
                                    f"(sesión {posicion + 1})."
                                ),
                            ) from exc
                        if hora_inicio is None or hora_fin is None:
                            raise HTTPException(
                                status_code=400,
                                detail=(
                                    f"NRC {item.id_nrc}: la sesión "
                                    f"{posicion + 1} requiere hora_inicio y "
                                    "hora_fin."
                                ),
                            )
                        if _hora_a_segundos(hora_fin) <= _hora_a_segundos(
                            hora_inicio
                        ):
                            raise HTTPException(
                                status_code=400,
                                detail=(
                                    f"NRC {item.id_nrc}: en la sesión "
                                    f"{posicion + 1}, hora_fin debe ser mayor "
                                    "que hora_inicio."
                                ),
                            )

                        numero_sesion = sesion.get("numero_sesion")
                        try:
                            numero_sesion = (
                                int(numero_sesion)
                                if numero_sesion is not None
                                else posicion + 1
                            )
                        except (TypeError, ValueError):
                            numero_sesion = posicion + 1
                        if numero_sesion <= 0:
                            numero_sesion = posicion + 1

                        sesiones.append(
                            (
                                item.id_nrc,
                                numero_sesion,
                                dia,
                                hora_inicio,
                                hora_fin,
                                _texto_opcional(sesion.get("salon")),
                                _texto_opcional(sesion.get("edificio")),
                            )
                        )

                    cur.execute(
                        "DELETE FROM horario WHERE nrc_id = %s", (item.id_nrc,)
                    )
                    if sesiones:
                        cur.executemany(
                            """
                            INSERT INTO horario (
                                nrc_id, numero_sesion, dia, hora_inicio,
                                hora_fin, salon, edificio
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            sesiones,
                        )
                    sesiones_guardadas += len(sesiones)

                actualizados += 1
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 - se informa con detalle
        conn.rollback()
        logger.exception("Error actualizando la oferta desde el dashboard")
        if getattr(exc, "pgcode", None) in ("23503", "22P02", "23502"):
            raise HTTPException(
                status_code=400,
                detail=f"No se pudo actualizar la oferta: {exc}",
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo actualizar la oferta en PostgreSQL: {exc}",
        ) from exc
    finally:
        conn.close()

    return {
        "ok": True,
        "total": len(items),
        "actualizados": actualizados,
        "sesiones": sesiones_guardadas,
    }


@app.get("/api/admin/estudiantes")
def listar_estudiantes_admin():
    """
    Lista TODOS los estudiantes con su perfil y contadores de avance.

    Endpoint de **solo lectura** para el panel del administrador: no existe
    PUT/DELETE de estudiantes.

    Cada elemento tiene el contrato::

        {id, nombre, plan_id, programa, jornada, sede, semestre,
         total_aprobadas, total_por_ver, tiene_perfil}

    ``total_aprobadas`` cuenta filas de ``historial`` y ``total_por_ver`` las de
    ``matriz_por_ver``. ``tiene_perfil`` indica si existe la fila en
    ``estudiante_perfil`` (aunque sus campos estén vacíos). Las tablas
    auxiliares se detectan con ``to_regclass``: si alguna no existe todavía, el
    endpoint responde igualmente con valores neutros (NULL/0/false).
    """
    conn = conectar()
    try:
        with conn.cursor() as cur:
            def tabla_existe(nombre: str) -> bool:
                cur.execute(
                    "SELECT to_regclass(%s) AS tabla",
                    (f"public.{nombre}",),
                )
                fila = cur.fetchone()
                return bool(fila and fila["tabla"] is not None)

            hay_perfil = tabla_existe("estudiante_perfil")
            hay_historial = tabla_existe("historial")
            hay_matriz = tabla_existe("matriz_por_ver")

            columnas = ["e.id", "e.nombre", "e.plan_id"]
            joins: List[str] = []

            if hay_perfil:
                # ``programa`` es la columna nueva; en perfiles antiguos solo
                # existe ``carrera``, así que se usa como respaldo.
                columnas.extend(
                    [
                        "COALESCE(p.programa, p.carrera) AS programa",
                        "p.jornada",
                        "p.sede",
                        "p.semestre",
                        "(p.estudiante_id IS NOT NULL) AS tiene_perfil",
                    ]
                )
                joins.append(
                    "LEFT JOIN estudiante_perfil p "
                    "ON p.estudiante_id = e.id"
                )
            else:
                columnas.extend(
                    [
                        "NULL::text AS programa",
                        "NULL::text AS jornada",
                        "NULL::text AS sede",
                        "NULL::integer AS semestre",
                        "FALSE AS tiene_perfil",
                    ]
                )

            if hay_historial:
                columnas.append(
                    "COALESCE(h.total, 0) AS total_aprobadas"
                )
                joins.append(
                    "LEFT JOIN ("
                    "SELECT estudiante_id, COUNT(*) AS total "
                    "FROM historial GROUP BY estudiante_id"
                    ") h ON h.estudiante_id = e.id"
                )
            else:
                columnas.append("0 AS total_aprobadas")

            if hay_matriz:
                columnas.append(
                    "COALESCE(m.total, 0) AS total_por_ver"
                )
                joins.append(
                    "LEFT JOIN ("
                    "SELECT estudiante_id, COUNT(*) AS total "
                    "FROM matriz_por_ver GROUP BY estudiante_id"
                    ") m ON m.estudiante_id = e.id"
                )
            else:
                columnas.append("0 AS total_por_ver")

            # Los fragmentos son constantes internas (nunca entrada del
            # usuario), por eso es seguro interpolarlos.
            consulta = (
                f"SELECT {', '.join(columnas)} "
                "FROM estudiante e "
                f"{' '.join(joins)} "
                "ORDER BY e.id"
            )
            cur.execute(consulta)
            filas = cur.fetchall()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - se informa con detalle
        logger.exception("Error listando estudiantes para el administrador")
        if getattr(exc, "pgcode", None) == "42P01":
            raise HTTPException(
                status_code=503,
                detail=(
                    "Falta la tabla 'estudiante' en PostgreSQL. El backend la "
                    "crea al arrancar con init_db.py; reinicia el contenedor o "
                    "ejecuta 'python init_db.py'. "
                    f"Detalle: {exc}"
                ),
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=f"No se pudieron listar los estudiantes: {exc}",
        ) from exc
    finally:
        conn.close()

    return [dict(fila) for fila in filas]


@app.post("/api/estudiante/upload-matriz")
async def upload_matriz(
    archivo: UploadFile = File(...),
    estudiante_id: Optional[int] = Form(None),
):
    """
    Sube la matriz académica de UN estudiante y reemplaza sus aprobadas/pendientes.

    Contrato del CSV (encabezados con espacios o mayúsculas son tolerados)::

        estudiante_id,carrera,semestre,estado,codigo_curso,nombre_curso

    * ``estado`` ∈ {``aprobada``, ``vista``} -> se guarda en ``historial``
      (materias aprobadas).
    * ``estado`` ∈ {``por_ver``, ``pendiente``} -> se guarda en
      ``matriz_por_ver`` (materias pendientes).
    * ``nombre_curso`` es opcional (se acepta pero no se persiste).

    Reglas:
      * El CSV debe contener un único ``estudiante_id``. Si el formulario trae
        ``estudiante_id`` y no coincide con el del CSV, se devuelve 400.
      * Crea si no existen ``estudiante_perfil``, ``matriz_por_ver`` e
        ``historial``.
      * Hace upsert del estudiante (nombre ``'Estudiante <id>'`` si es nuevo)
        y REEMPLAZA (DELETE + INSERT) solo los datos de ESE estudiante.

    Devuelve::

        {ok, estudiante_id, carrera, semestre, aprobadas: [...],
         por_ver: [...], total_aprobadas, total_por_ver}
    """
    if estudiante_id is not None and estudiante_id <= 0:
        raise HTTPException(
            status_code=400,
            detail="El estudiante_id del formulario debe ser un entero positivo.",
        )

    try:
        datos = await archivo.read()
    finally:
        await archivo.close()

    df = _leer_csv_subido(datos, "matriz de estudiantes")
    matriz = _parsear_matriz(df, estudiante_id_form=estudiante_id)

    sid = matriz["estudiante_id"]
    aprobadas = matriz["aprobadas"]
    por_ver = matriz["por_ver"]
    carrera = matriz["carrera"]
    semestre = matriz["semestre"]

    conn = conectar()
    try:
        with conn.cursor() as cur:
            # Tablas auxiliares idempotentes (el esquema base no las trae).
            _asegurar_tablas_estudiante(cur)

            # Upsert del estudiante: si ya existe se conserva su nombre real.
            cur.execute(
                """
                INSERT INTO estudiante (id, nombre, plan_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (sid, f"Estudiante {sid}", PLAN_POR_DEFECTO),
            )

            # Perfil: actualiza solo los valores informados (no borra con NULL).
            cur.execute(
                """
                INSERT INTO estudiante_perfil (estudiante_id, carrera, semestre)
                VALUES (%s, %s, %s)
                ON CONFLICT (estudiante_id) DO UPDATE SET
                    carrera  = COALESCE(EXCLUDED.carrera,
                                        estudiante_perfil.carrera),
                    semestre = COALESCE(EXCLUDED.semestre,
                                        estudiante_perfil.semestre)
                """,
                (sid, carrera, semestre),
            )

            # Reemplazo total SOLO del historial de este estudiante.
            cur.execute("DELETE FROM historial WHERE estudiante_id = %s", (sid,))
            if aprobadas:
                cur.executemany(
                    "INSERT INTO historial (estudiante_id, codigo_curso) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    [(sid, codigo) for codigo in aprobadas],
                )

            # Reemplazo total SOLO de la matriz por ver de este estudiante.
            cur.execute(
                "DELETE FROM matriz_por_ver WHERE estudiante_id = %s", (sid,)
            )
            if por_ver:
                cur.executemany(
                    "INSERT INTO matriz_por_ver (estudiante_id, codigo_curso) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    [(sid, codigo) for codigo in por_ver],
                )
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 - se informa con detalle
        conn.rollback()
        logger.exception("Error guardando la matriz del estudiante %s", sid)
        # 42P01 = undefined_table: el volumen de PostgreSQL existe pero le
        # faltan las tablas base (schema.sql no se ejecutó nunca). init_db.py
        # las crea al arrancar; damos una instrucción accionable en vez de un
        # 500 genérico.
        if getattr(exc, "pgcode", None) == "42P01":
            raise HTTPException(
                status_code=503,
                detail=(
                    "Faltan tablas básicas en PostgreSQL (por ejemplo "
                    "'estudiante'). El backend las crea al arrancar con "
                    "init_db.py; reinicia el contenedor del backend o ejecuta "
                    "'python init_db.py' dentro de él. No se borrará ningún "
                    f"dato. Detalle: {exc}"
                ),
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo guardar la matriz en PostgreSQL: {exc}",
        ) from exc
    finally:
        conn.close()

    return {
        "ok": True,
        "estudiante_id": sid,
        "carrera": carrera,
        "semestre": semestre,
        "aprobadas": aprobadas,
        "por_ver": por_ver,
        "total_aprobadas": len(aprobadas),
        "total_por_ver": len(por_ver),
    }


@app.get("/api/estudiante/{estudiante_id}/matriz")
def obtener_matriz_estudiante(estudiante_id: int):
    """
    Devuelve el resumen de la matriz cargada para un estudiante.

    Respuesta::

        {estudiante_id, carrera, semestre, aprobadas: [...], por_ver: [...],
         total_aprobadas, total_por_ver}

    Devuelve 404 si el estudiante no tiene perfil (matriz nunca cargada).
    """
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT to_regclass('public.estudiante_perfil') AS tabla"
            )
            if cur.fetchone()["tabla"] is None:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        "No hay perfil cargado para el estudiante "
                        f"{estudiante_id}. Sube su matriz primero."
                    ),
                )

            cur.execute(
                "SELECT carrera, semestre FROM estudiante_perfil "
                "WHERE estudiante_id = %s",
                (estudiante_id,),
            )
            perfil = cur.fetchone()
            if perfil is None:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        "No hay perfil cargado para el estudiante "
                        f"{estudiante_id}. Sube su matriz primero."
                    ),
                )

            aprobadas = codigos_de_tabla(
                cur, "historial", estudiante_id, obligatoria=False
            )
            por_ver = codigos_de_tabla(
                cur, "matriz_por_ver", estudiante_id, obligatoria=False
            )
    finally:
        conn.close()

    return {
        "estudiante_id": estudiante_id,
        "carrera": perfil["carrera"],
        "semestre": perfil["semestre"],
        "aprobadas": aprobadas,
        "por_ver": por_ver,
        "total_aprobadas": len(aprobadas),
        "total_por_ver": len(por_ver),
    }


@app.get("/api/catalogos")
def catalogos():
    """
    Catálogos para el formulario del estudiante.

    * ``programas``: distintos de ``malla_curricular`` (MongoDB) y, como
      respaldo, distintos de ``curso.programa`` en PostgreSQL.
    * ``jornadas``: ``['Diurna', 'Nocturna', 'Mixta', 'Virtual']``.
    * ``sedes``: ``[{codigo, nombre}]`` desde la tabla ``sede`` de PostgreSQL
      (el ``codigo`` es el valor que coincide con ``nrc.sede_id``).
      ``sedes_nombres`` ofrece la lista simple de nombres.
    """
    programas: Dict[str, str] = {}

    def _agregar_programa(valor):
        texto = _texto_opcional(valor)
        if texto:
            programas.setdefault(_clave_texto(texto), texto)

    try:
        for nombre in _programas_malla():
            _agregar_programa(nombre)
    except HTTPException as exc:
        logger.warning("No se pudo leer la malla desde MongoDB: %s", exc.detail)

    sedes_detalle: List[Dict[str, Any]] = []
    conn = None
    try:
        conn = conectar()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT programa
                FROM curso
                WHERE programa IS NOT NULL AND BTRIM(programa) <> ''
                ORDER BY programa
                """
            )
            for fila in cur.fetchall():
                _agregar_programa(fila["programa"])

            cur.execute(
                """
                SELECT codigo, nombre
                FROM sede
                ORDER BY nombre NULLS LAST, codigo
                """
            )
            for fila in cur.fetchall():
                codigo = _texto_opcional(fila["codigo"])
                if codigo:
                    sedes_detalle.append(
                        {
                            "codigo": codigo,
                            "nombre": _texto_opcional(fila["nombre"]) or codigo,
                        }
                    )
    except HTTPException as exc:
        logger.warning(
            "No se pudieron leer catálogos de PostgreSQL: %s", exc.detail
        )
    except Exception as exc:  # noqa: BLE001 - catálogo tolerante a fallos
        logger.warning("No se pudieron leer catálogos de PostgreSQL: %s", exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    return {
        "programas": sorted(programas.values(), key=lambda p: _clave_texto(p)),
        "jornadas": ["Diurna", "Nocturna", "Mixta", "Virtual"],
        # Objetos {codigo, nombre}: el frontend usa el código como valor del
        # <select> y el nombre como etiqueta (así el perfil guarda el código
        # que coincide con nrc.sede_id).
        "sedes": sedes_detalle,
        "sedes_nombres": [s["nombre"] for s in sedes_detalle],
    }


@app.post("/api/estudiante/perfil")
@app.put("/api/estudiante/perfil")
async def crear_perfil_estudiante(
    programa: Optional[str] = Form(None),
    jornada: Optional[str] = Form(None),
    sede: Optional[str] = Form(None),
    estudiante_id: Optional[str] = Form(None),
    matriz: Optional[UploadFile] = File(None),
):
    """
    Crea/actualiza el perfil del estudiante y, opcionalmente, su matriz.

    Se expone tanto en ``POST`` como en ``PUT`` (mismo contrato multipart).

    Form (multipart/form-data):
      * ``programa`` (obligatorio), ``jornada``, ``sede``;
      * ``estudiante_id`` opcional (si no viene, se crea ``max(id)+1``);
      * ``matriz`` opcional (CSV/XLSX/XLSM).

    Invalidación del horario: si el perfil ya existía y cambia el
    ``programa``, la ``jornada`` o la ``sede`` (comparación tolerante a
    mayúsculas, acentos y espacios), dentro de la MISMA transacción se borran
    todas las inscripciones del estudiante y se descuentan de ``nrc`` los
    cupos que ocupaban (``inscritos``/``saldo``), preservando los inscritos
    externos de la oferta. Así ``POST /api/horario/automatico`` deja de ser
    idempotente y puede generar un horario nuevo. Guardar los mismos valores
    o solo la matriz NO borra inscripciones.

    Matriz aceptada (columnas tolerantes):
      * ``estudiante_id`` (opcional), ``codigo_curso``/``materia`` y
        ``estado`` opcional (``aprobada``/``vista`` -> historial;
        ``por_ver``/``pendiente`` -> matriz_por_ver). Sin estado, las materias
        se consideran ``por_ver``.
      * o una lista simple de materias (una por fila o separadas por coma).

    Sin matriz: se consulta la malla del programa en MongoDB y se asignan a
    ``matriz_por_ver`` los cursos del primer semestre (semestre mínimo);
    ``historial`` queda vacío.

    Devuelve ``{ok, estudiante_id, programa, jornada, sede, carrera,
    semestre, aprobadas, por_ver, total_aprobadas, total_por_ver,
    horario_invalidado, nrc_liberados, mensaje}``.
    """
    programa = _texto_opcional(programa)
    if not programa:
        raise HTTPException(
            status_code=400,
            detail="El campo 'programa' es obligatorio.",
        )
    jornada = _texto_opcional(jornada)
    sede = _texto_opcional(sede)

    sid_form = None
    if estudiante_id is not None and str(estudiante_id).strip() != "":
        try:
            sid_form = int(str(estudiante_id).strip())
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail="El estudiante_id debe ser un entero positivo.",
            ) from exc
        if sid_form <= 0:
            raise HTTPException(
                status_code=400,
                detail="El estudiante_id debe ser un entero positivo.",
            )

    # ------------------------------------------------------------------
    # 1. Leer la matriz opcional (CSV/XLSX/XLSM).
    # ------------------------------------------------------------------
    matriz_datos = None
    if matriz is not None:
        nombre_matriz = matriz.filename or ""
        try:
            datos = await matriz.read()
        finally:
            await matriz.close()
        df = _leer_matriz_subida(
            datos, nombre_matriz, "matriz del estudiante"
        )
        matriz_datos = _parsear_matriz_perfil(df, sid_form)

    # ------------------------------------------------------------------
    # 2. Malla del programa: cursos del primer semestre y semestre mínimo.
    #    Solo se consulta si hace falta (sin matriz, o matriz sin semestre).
    # ------------------------------------------------------------------
    malla_cursos: List[Dict[str, Any]] = []
    malla_semestre: Optional[int] = None
    try:
        if matriz_datos is None:
            malla_cursos, malla_semestre = _primer_semestre_malla(programa)
        elif matriz_datos.get("semestre") is None:
            _, malla_semestre = _primer_semestre_malla(programa)
    except HTTPException as exc:
        logger.warning(
            "No se pudo consultar la malla para %s: %s", programa, exc.detail
        )

    # ------------------------------------------------------------------
    # 3. Determinar el estudiante.
    # ------------------------------------------------------------------
    if sid_form is not None:
        sid = sid_form
    elif matriz_datos and matriz_datos.get("estudiante_id") is not None:
        sid = matriz_datos["estudiante_id"]
    else:
        sid = None  # se crea dentro de la transacción (max(id)+1)

    # Valores efectivos de perfil (si el form no envía jornada/sede, se
    # conservan los ya guardados por el COALESCE del upsert).
    jornada_guardada = jornada
    sede_guardada = sede

    # Invalidación transaccional del horario anterior. Solo se activa cuando
    # hay un cambio REAL de programa/jornada/sede en un perfil ya existente.
    horario_invalidado = False
    nrc_liberados = 0
    mensaje_perfil = None

    conn = conectar()
    try:
        with conn.cursor() as cur:
            # 3.1 Tablas auxiliares idempotentes.
            _asegurar_tablas_estudiante(cur)

            # 3.2 Crear estudiante si no se indicó uno.
            if sid is None:
                cur.execute(
                    "SELECT pg_advisory_xact_lock("
                    "hashtext('inscripcion.estudiante_nuevo'))"
                )
                cur.execute(
                    "SELECT COALESCE(MAX(id), 0) + 1 AS nuevo FROM estudiante"
                )
                sid = int(cur.fetchone()["nuevo"])
                cur.execute(
                    "INSERT INTO estudiante (id, nombre, plan_id) "
                    "VALUES (%s, %s, %s)",
                    (sid, f"Estudiante {sid}", PLAN_POR_DEFECTO),
                )
            else:
                cur.execute(
                    "INSERT INTO estudiante (id, nombre, plan_id) "
                    "VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
                    (sid, f"Estudiante {sid}", PLAN_POR_DEFECTO),
                )

            # 3.3 Carrera = programa; semestre del perfil/matriz/malla.
            cur.execute(
                "SELECT programa, semestre, jornada, sede "
                "FROM estudiante_perfil WHERE estudiante_id = %s",
                (sid,),
            )
            fila_perfil = cur.fetchone()
            programa_existente = (
                fila_perfil["programa"] if fila_perfil else None
            )
            semestre_existente = (
                fila_perfil["semestre"] if fila_perfil else None
            )
            jornada_existente = (
                fila_perfil["jornada"] if fila_perfil else None
            )
            sede_existente = fila_perfil["sede"] if fila_perfil else None
            if fila_perfil:
                if jornada_guardada is None:
                    jornada_guardada = fila_perfil["jornada"]
                if sede_guardada is None:
                    sede_guardada = fila_perfil["sede"]

            # 3.4 ¿Cambió el perfil? Comparación tolerante (mayúsculas,
            #     acentos y espacios). Un perfil nuevo no tiene horario previo
            #     que invalidar y, si el valor existente es nulo, tampoco se
            #     considera cambio (no hay nada que comparar).
            cambio_perfil = False
            if fila_perfil:
                if (
                    _texto_opcional(programa_existente) is not None
                    and _clave_texto(programa_existente)
                    != _clave_texto(programa)
                ):
                    cambio_perfil = True
                if (
                    _texto_opcional(jornada_existente) is not None
                    and _clave_texto(jornada_existente)
                    != _clave_texto(jornada_guardada)
                ):
                    cambio_perfil = True
                if (
                    _texto_opcional(sede_existente) is not None
                    and _clave_texto(sede_existente)
                    != _clave_texto(sede_guardada)
                ):
                    cambio_perfil = True

            aprobadas: List[str] = []
            por_ver: List[str] = []

            if matriz_datos is not None:
                aprobadas = list(matriz_datos.get("aprobadas") or [])
                por_ver = list(matriz_datos.get("por_ver") or [])
                semestre = matriz_datos.get("semestre")
            else:
                # Sin matriz: primer semestre de la malla. Si MongoDB no
                # respondió, se intenta el catálogo de PostgreSQL.
                if not malla_cursos:
                    cur.execute(
                        """
                        SELECT codigo_alfanumerico AS codigo, semestre, programa
                        FROM curso
                        """
                    )
                    filas_curso = cur.fetchall()
                    con_programa = [
                        f for f in filas_curso if _texto_opcional(f["programa"])
                    ]
                    if con_programa:
                        filas_curso = [
                            f
                            for f in filas_curso
                            if _programa_coincide(f["programa"], programa)
                        ]
                    semestres = [
                        int(f["semestre"])
                        for f in filas_curso
                        if f["semestre"] is not None
                    ]
                    if semestres:
                        malla_semestre = min(semestres)
                        malla_cursos = [
                            {"codigo": f["codigo"]}
                            for f in filas_curso
                            if f["semestre"] is not None
                            and int(f["semestre"]) == malla_semestre
                        ]
                    else:
                        malla_cursos = [
                            {"codigo": f["codigo"]} for f in filas_curso
                        ]
                por_ver = [
                    str(c.get("codigo")).strip().upper()
                    for c in malla_cursos
                    if c.get("codigo")
                ]
                semestre = malla_semestre

            if semestre is None:
                semestre = semestre_existente
            if semestre is None:
                semestre = 1
            semestre = int(semestre)

            # 3.4 El estudiante no puede tener un código aprobado y pendiente.
            aprobadas = sorted(
                {
                    str(c).strip().upper()
                    for c in aprobadas
                    if str(c).strip()
                }
            )
            por_ver = sorted(
                {
                    str(c).strip().upper()
                    for c in por_ver
                    if str(c).strip() and str(c).strip().upper() not in aprobadas
                }
            )
            # VARCHAR(10): se descartan códigos imposibles de persistir.
            aprobadas = [c for c in aprobadas if len(c) <= 10]
            por_ver = [c for c in por_ver if len(c) <= 10]

            # 3.5 Perfil (carrera = programa).
            cur.execute(
                """
                INSERT INTO estudiante_perfil (
                    estudiante_id, carrera, semestre, programa, jornada, sede
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (estudiante_id) DO UPDATE SET
                    carrera  = EXCLUDED.carrera,
                    semestre = EXCLUDED.semestre,
                    programa = EXCLUDED.programa,
                    jornada  = COALESCE(EXCLUDED.jornada,
                                        estudiante_perfil.jornada),
                    sede     = COALESCE(EXCLUDED.sede,
                                        estudiante_perfil.sede)
                """,
                (sid, programa, semestre, programa, jornada, sede),
            )

            # 3.6 Invalidación transaccional del horario anterior.
            #     Solo ocurre con un cambio real de programa/jornada/sede y si
            #     el estudiante tenía inscripciones. Se cuenta por NRC antes
            #     de borrar para descontar exactamente esos cupos (los
            #     inscritos externos de la oferta se conservan).
            if cambio_perfil:
                cur.execute(
                    """
                    SELECT nrc_id, COUNT(*) AS total
                    FROM inscripcion
                    WHERE estudiante_id = %s
                    GROUP BY nrc_id
                    ORDER BY nrc_id
                    """,
                    (sid,),
                )
                conteos = cur.fetchall()
                if conteos:
                    cur.execute(
                        "DELETE FROM inscripcion WHERE estudiante_id = %s",
                        (sid,),
                    )
                    for fila_conteo in conteos:
                        total = int(fila_conteo["total"] or 0)
                        # PostgreSQL evalúa ambos SET con el valor ANTERIOR de
                        # ``inscritos``, por eso se pasa ``total`` dos veces.
                        cur.execute(
                            """
                            UPDATE nrc SET
                                inscritos = GREATEST(
                                    COALESCE(inscritos, 0) - %s, 0
                                ),
                                saldo = CASE
                                    WHEN cupo_max IS NULL THEN NULL
                                    ELSE GREATEST(
                                        cupo_max - GREATEST(
                                            COALESCE(inscritos, 0) - %s, 0
                                        ),
                                        0
                                    )
                                END
                            WHERE id_nrc = %s
                            """,
                            (total, total, fila_conteo["nrc_id"]),
                        )
                    horario_invalidado = True
                    nrc_liberados = len(conteos)
                    mensaje_perfil = (
                        "Perfil actualizado. Tu horario anterior fue liberado, "
                        "por favor genera uno nuevo."
                    )

            # 3.7 Reemplazo SOLO de la matriz de este estudiante.
            cur.execute(
                "DELETE FROM historial WHERE estudiante_id = %s", (sid,)
            )
            if aprobadas:
                cur.executemany(
                    "INSERT INTO historial (estudiante_id, codigo_curso) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    [(sid, codigo) for codigo in aprobadas],
                )

            cur.execute(
                "DELETE FROM matriz_por_ver WHERE estudiante_id = %s", (sid,)
            )
            if por_ver:
                cur.executemany(
                    "INSERT INTO matriz_por_ver (estudiante_id, codigo_curso) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    [(sid, codigo) for codigo in por_ver],
                )

            # 3.8 Mantener la secuencia de estudiante al día.
            cur.execute(
                "SELECT pg_get_serial_sequence('estudiante', 'id') AS secuencia"
            )
            fila_secuencia = cur.fetchone()
            secuencia = fila_secuencia["secuencia"] if fila_secuencia else None
            if secuencia:
                cur.execute(
                    "SELECT setval(%s, "
                    "(SELECT GREATEST(COALESCE(MAX(id), 0), 1) "
                    "FROM estudiante), true)",
                    (secuencia,),
                )
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 - se informa con detalle
        conn.rollback()
        logger.exception(
            "Error guardando el perfil del estudiante %s", sid
        )
        if getattr(exc, "pgcode", None) == "42P01":
            raise HTTPException(
                status_code=503,
                detail=(
                    "Faltan tablas básicas en PostgreSQL. El backend las crea "
                    "al arrancar con init_db.py; reinicia el contenedor o "
                    "ejecuta 'python init_db.py'. No se borrará ningún dato. "
                    f"Detalle: {exc}"
                ),
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo guardar el perfil en PostgreSQL: {exc}",
        ) from exc
    finally:
        conn.close()

    return {
        "ok": True,
        "estudiante_id": sid,
        "programa": programa,
        "jornada": jornada_guardada,
        "sede": sede_guardada,
        "carrera": programa,
        "semestre": semestre,
        "aprobadas": aprobadas,
        "por_ver": por_ver,
        "total_aprobadas": len(aprobadas),
        "total_por_ver": len(por_ver),
        "horario_invalidado": horario_invalidado,
        "nrc_liberados": nrc_liberados,
        "mensaje": mensaje_perfil,
    }


@app.get("/api/estudiante/{estudiante_id}/perfil")
def obtener_perfil_estudiante(estudiante_id: int):
    """
    Devuelve el resumen del perfil y la matriz de un estudiante.

    Incluye ``{estudiante_id, nombre, programa, jornada, sede, carrera,
    semestre, aprobadas, por_ver, total_aprobadas, total_por_ver, perfil}``.
    Devuelve 404 si el estudiante o su perfil no existen.
    """
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    e.id,
                    e.nombre,
                    p.estudiante_id AS perfil_id,
                    p.carrera,
                    p.semestre,
                    p.programa,
                    p.jornada,
                    p.sede
                FROM estudiante e
                LEFT JOIN estudiante_perfil p
                       ON p.estudiante_id = e.id
                WHERE e.id = %s
                """,
                (estudiante_id,),
            )
            fila = cur.fetchone()
            if fila is None or fila.get("perfil_id") is None:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"El estudiante {estudiante_id} no tiene perfil "
                        "cargado. Regístralo primero en /api/estudiante/perfil."
                    ),
                )

            aprobadas = codigos_de_tabla(
                cur, "historial", estudiante_id, obligatoria=False
            )
            por_ver = codigos_de_tabla(
                cur, "matriz_por_ver", estudiante_id, obligatoria=False
            )
    finally:
        conn.close()

    programa_efectivo = fila["programa"] or fila["carrera"]
    carrera_efectiva = fila["carrera"] or programa_efectivo
    perfil = {
        "estudiante_id": estudiante_id,
        "nombre": fila["nombre"],
        "programa": programa_efectivo,
        "jornada": fila["jornada"],
        "sede": fila["sede"],
        "carrera": carrera_efectiva,
        "semestre": fila["semestre"],
    }
    return {
        "estudiante_id": estudiante_id,
        "nombre": fila["nombre"],
        "programa": programa_efectivo,
        "jornada": fila["jornada"],
        "sede": fila["sede"],
        "carrera": carrera_efectiva,
        "semestre": fila["semestre"],
        "aprobadas": aprobadas,
        "por_ver": por_ver,
        "total_aprobadas": len(aprobadas),
        "total_por_ver": len(por_ver),
        "perfil": perfil,
    }


@app.post("/api/inscribir")
def inscribir(body: InscripcionIn):
    """
    Inscribe un estudiante en un NRC aplicando las validaciones:
      1. El NRC y el estudiante existen.
      2. El NRC tiene cupo disponible (usa ``nrc.inscritos`` si la oferta lo
         trae; si no, el conteo real de ``inscripcion``).
      3. No hay choque de horario con NRCs ya inscritos (misma consulta de
         solapamiento de ``schema.sql``).
      4. El estudiante no está ya inscrito en ese NRC (PK compuesta).
    """
    conn = conectar()
    try:
        with conn.cursor() as cur:
            # 1. Existencia del NRC (bloquea la fila para validar cupo sin
            #    carreras entre peticiones concurrentes).
            cur.execute(
                "SELECT id_nrc, cupo_max, inscritos FROM nrc "
                "WHERE id_nrc = %s FOR UPDATE",
                (body.nrc_id,),
            )
            nrc = cur.fetchone()
            if nrc is None:
                raise HTTPException(status_code=404, detail="El NRC no existe")

            # 2. Existencia del estudiante
            cur.execute(
                "SELECT id FROM estudiante WHERE id = %s",
                (body.estudiante_id,),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="El estudiante no existe")

            # 3. Ya inscrito en el mismo NRC
            cur.execute(
                "SELECT 1 FROM inscripcion WHERE estudiante_id = %s AND nrc_id = %s",
                (body.estudiante_id, body.nrc_id),
            )
            if cur.fetchone():
                raise HTTPException(
                    status_code=409,
                    detail="El estudiante ya está inscrito en este NRC",
                )

            # 4. Cupo disponible
            cur.execute(
                "SELECT COUNT(*) AS total FROM inscripcion WHERE nrc_id = %s",
                (body.nrc_id,),
            )
            inscritos_local = int(cur.fetchone()["total"] or 0)
            inscritos = max(int(nrc["inscritos"] or 0), inscritos_local)
            if nrc["cupo_max"] is not None and inscritos >= int(nrc["cupo_max"]):
                raise HTTPException(
                    status_code=409,
                    detail="El NRC no tiene cupo disponible",
                )

            # 5. Choque de horario (mismo día y rangos solapados)
            cur.execute(
                """
                SELECT 1
                FROM inscripcion i
                JOIN horario h1 ON h1.nrc_id = i.nrc_id
                JOIN horario h2 ON h2.nrc_id = %(nrc_id)s
                WHERE i.estudiante_id = %(estudiante_id)s
                  AND h2.dia = h1.dia
                  AND h2.hora_inicio < h1.hora_fin
                  AND h1.hora_inicio < h2.hora_fin
                LIMIT 1
                """,
                {"nrc_id": body.nrc_id, "estudiante_id": body.estudiante_id},
            )
            if cur.fetchone():
                raise HTTPException(
                    status_code=409,
                    detail="Choque de horario con un NRC ya inscrito",
                )

            # Todo OK -> insertar
            cur.execute(
                """
                INSERT INTO inscripcion (estudiante_id, nrc_id, fecha)
                VALUES (%s, %s, CURRENT_DATE)
                """,
                (body.estudiante_id, body.nrc_id),
            )

            # Mantiene coherentes los contadores desnormalizados del NRC. El
            # máximo entre ``nrc.inscritos`` y las inscripciones locales evita
            # subcontar cuando la oferta no incluye las inscripciones hechas
            # desde la API.
            cur.execute(
                """
                UPDATE nrc SET
                    inscritos = GREATEST(COALESCE(inscritos, 0), %s) + 1,
                    saldo = CASE
                        WHEN cupo_max IS NOT NULL
                            THEN cupo_max
                                 - (GREATEST(COALESCE(inscritos, 0), %s) + 1)
                        ELSE saldo
                    END
                WHERE id_nrc = %s
                """,
                (inscritos_local, inscritos_local, body.nrc_id),
            )
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "ok": True,
        "mensaje": "Inscripción exitosa",
        "estudiante_id": body.estudiante_id,
        "nrc_id": body.nrc_id,
    }


@app.get("/api/horario/{estudiante_id}")
def horario_estudiante(estudiante_id: int):
    """Retorna el horario consolidado (todas las sesiones) del estudiante."""
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    h.nrc_id,
                    c.codigo_alfanumerico,
                    c.nombre            AS materia,
                    c.creditos,
                    h.numero_sesion,
                    h.dia,
                    h.hora_inicio::text AS hora_inicio,
                    h.hora_fin::text    AS hora_fin
                FROM inscripcion i
                JOIN nrc n   ON n.id_nrc = i.nrc_id
                JOIN curso c ON c.codigo_alfanumerico = n.curso_id
                JOIN horario h ON h.nrc_id = n.id_nrc
                WHERE i.estudiante_id = %s
                ORDER BY h.dia, h.hora_inicio, h.nrc_id
                """,
                (estudiante_id,),
            )
            filas = cur.fetchall()
    finally:
        conn.close()
    return filas


@app.get("/api/estudiante/horario/{estudiante_id}")
def horario_guardado_estudiante(
    estudiante_id: int, periodo: Optional[str] = None
):
    """
    Horario ya inscrito del estudiante, agrupado por NRC (solo lectura).

    Parámetros
    ----------
    ``estudiante_id``: id del estudiante.
    ``periodo``: filtro opcional (p. ej. ``?periodo=202660``). Si se omite, se
    usa el periodo máximo de sus inscripciones; si no hay periodos, ``null``.

    Respuesta::

        {
          "estudiante_id": 1,
          "periodo": "202660" | null,
          "total": 3,
          "horario": [
            {
              "codigo": "ISUMBG043",
              "materia": "...",
              "creditos": 3,
              "nrc_id": 123,
              "docente_nombre": "...",
              "sede_nombre": "...",
              "sesiones": [
                {"numero_sesion": 1, "dia": 1, "dia_nombre": "Lunes",
                 "hora_inicio": "07:00:00", "hora_fin": "08:30:00",
                 "salon": "...", "edificio": "..."}
              ]
            }
          ]
        }

    Un estudiante sin inscripciones devuelve ``total: 0`` y ``horario: []``
    (nunca 404).
    """
    periodo_efectivo, horario = _horario_guardado(estudiante_id, periodo)
    return {
        "estudiante_id": estudiante_id,
        "periodo": periodo_efectivo,
        "total": len(horario),
        "horario": horario,
    }


# ---------------------------------------------------------------------------
# A. Generador de Horario Automático
# ---------------------------------------------------------------------------
@app.post("/api/horario/automatico")
def horario_automatico(body: HorarioAutomaticoIn):
    """
    Genera un horario completo y sin choques para un estudiante, y lo guarda
    automáticamente en ``inscripcion``.

    Es **idempotente por periodo**: si el estudiante ya tiene inscripciones en
    el periodo vigente (``PERIODO_ACTUAL`` o ``MAX(nrc.periodo)``), no vuelve
    a generar ni insertar; devuelve el horario guardado con ``ya_existe=true``.
    En caso contrario genera, inserta con ``ON CONFLICT DO NOTHING`` y añade
    ``ya_existe=false`` a la respuesta.

    Usa el perfil del estudiante (``estudiante_perfil``):
      * ``sede`` y ``jornada`` filtran los NRC candidatos (si están definidas;
        si no, no se filtra);
      * ``programa`` acota la malla/catálogo del que salen las materias
        faltantes.

    Algoritmo (heurístico/greedy):
      1. Lee las materias aprobadas del estudiante desde ``historial``.
      2. Lee la malla/catálogo (MongoDB) y las reglas dinámicas (MongoDB) para
         calcular qué materias faltan y cuáles son elegibles (prerrequisitos
         satisfechos).
      3. Lee los NRC con cupo y sus sesiones desde PostgreSQL, incluyendo
         docente y sede.
      4. Ordena las materias elegibles (primero las de ``matriz_por_ver`` si
         existen, luego semestre, las más restringidas y las de más créditos)
         y asigna, para cada una, el primer NRC que no choque con lo ya
         seleccionado.
      5. Inserta las inscripciones elegidas, re-validando cupo/choque contra la
         base de datos dentro de una transacción.

    Devuelve ``{horario, no_asignadas, resumen}``. Cada materia del horario
    incluye ``docente_nombre`` y ``sede_nombre`` cuando están disponibles.
    """
    estudiante_id = body.estudiante_id

    # ------------------------------------------------------------------
    # 0. Idempotencia: si ya hay un horario guardado para el periodo
    #    vigente, se devuelve tal cual y no se genera ni inserta nada.
    # ------------------------------------------------------------------
    periodo_actual = _periodo_actual_nrc()
    if _tiene_inscripciones_periodo(estudiante_id, periodo_actual):
        periodo_guardado, horario_guardado = _horario_guardado(
            estudiante_id, periodo_actual
        )
        return {
            "ok": True,
            "ya_existe": True,
            "mensaje": (
                "El estudiante ya tiene un horario generado para el "
                f"periodo {periodo_guardado}."
            ),
            "estudiante_id": estudiante_id,
            "periodo": periodo_guardado,
            "resumen": {
                "asignadas": len(horario_guardado),
                "no_asignadas": 0,
            },
            "horario": horario_guardado,
            "no_asignadas": [],
        }

    # ------------------------------------------------------------------
    # 1. Datos de PostgreSQL: estudiante, perfil, aprobadas, inscritas y
    #    NRCs con cupo (incluye docente/sede y contadores de la oferta).
    # ------------------------------------------------------------------
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, plan_id FROM estudiante WHERE id = %s",
                (estudiante_id,),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="El estudiante no existe")

            # 1.1 Perfil del estudiante (programa/jornada/sede). Si no existe
            #     el perfil, no se filtra por jornada/sede.
            perfil = None
            cur.execute(
                "SELECT to_regclass('public.estudiante_perfil') AS tabla"
            )
            if cur.fetchone()["tabla"] is not None:
                cur.execute(
                    "SELECT programa, jornada, sede "
                    "FROM estudiante_perfil WHERE estudiante_id = %s",
                    (estudiante_id,),
                )
                perfil = cur.fetchone()

            # 1.2 Materias aprobadas (tabla historial). La matriz por ver es
            #     opcional y solo se usa para priorizar (si existe).
            aprobadas = set(materias_aprobadas(cur, estudiante_id))
            por_ver = set(materias_por_ver(cur, estudiante_id))

            # 1.3 Materias en las que ya está inscrito actualmente (evita duplicar).
            cur.execute(
                """
                SELECT DISTINCT n.curso_id
                FROM inscripcion i
                JOIN nrc n ON n.id_nrc = i.nrc_id
                WHERE i.estudiante_id = %s
                """,
                (estudiante_id,),
            )
            inscritas = {
                str(fila["curso_id"] or "").strip().upper() for fila in cur.fetchall()
            }
            inscritas.discard("")

            # 1.4 NRCs con cupo. El cupo efectivo usa ``nrc.inscritos`` (dato
            #     de la oferta) y cae al conteo real de ``inscripcion``.
            cur.execute(
                """
                SELECT
                    n.id_nrc,
                    n.curso_id,
                    c.nombre   AS materia,
                    c.creditos,
                    n.periodo,
                    n.jornada,
                    n.modalidad,
                    n.cupo_max,
                    n.inscritos,
                    n.saldo,
                    n.estado,
                    n.docente_id,
                    d.nombre   AS docente_nombre,
                    n.sede_id,
                    s.nombre   AS sede_nombre,
                    COALESCE(i.total, 0) AS inscritos_local,
                    GREATEST(
                        COALESCE(n.inscritos, 0), COALESCE(i.total, 0)
                    ) AS inscritos_efectivos
                FROM nrc n
                JOIN curso c ON c.codigo_alfanumerico = n.curso_id
                LEFT JOIN docente d ON d.id_docente = n.docente_id
                LEFT JOIN sede s    ON s.codigo = n.sede_id
                LEFT JOIN (
                    SELECT nrc_id, COUNT(*) AS total
                    FROM inscripcion
                    GROUP BY nrc_id
                ) i ON i.nrc_id = n.id_nrc
                WHERE COALESCE(n.cupo_max, 0)
                      > GREATEST(
                            COALESCE(n.inscritos, 0), COALESCE(i.total, 0)
                        )
                ORDER BY n.id_nrc
                """
            )
            nrcs = cur.fetchall()

            # 1.5 Resolver la sede del perfil (puede venir como código o
            #     nombre) para compararla con ``nrc.sede_id``.
            sede_objetivo = None
            sede_perfil_texto = _texto_opcional(
                perfil.get("sede") if perfil else None
            )
            if sede_perfil_texto:
                clave_sede = _clave_texto(sede_perfil_texto)
                cur.execute("SELECT to_regclass('public.sede') AS tabla")
                if cur.fetchone()["tabla"] is not None:
                    cur.execute("SELECT codigo, nombre FROM sede")
                    for fila_sede in cur.fetchall():
                        if clave_sede in (
                            _clave_texto(fila_sede["codigo"]),
                            _clave_texto(fila_sede["nombre"]),
                        ):
                            sede_objetivo = fila_sede["codigo"]
                            break
    finally:
        conn.close()

    # ------------------------------------------------------------------
    # 1.6 Filtro por perfil: sede y jornada (si están definidas). El
    #     programa se usa más abajo para acotar la malla/catálogo.
    # ------------------------------------------------------------------
    if perfil:
        jornada_perfil = _texto_opcional(perfil.get("jornada"))
        sede_perfil = _texto_opcional(perfil.get("sede"))
        if jornada_perfil:
            clave_jornada = _clave_texto(jornada_perfil)
            nrcs = [
                n for n in nrcs
                if _clave_texto(n.get("jornada")) == clave_jornada
            ]
        if sede_perfil:
            claves_sede = {_clave_texto(sede_perfil)}
            if sede_objetivo:
                claves_sede.add(_clave_texto(sede_objetivo))
            nrcs = [
                n
                for n in nrcs
                if _clave_texto(n.get("sede_id")) in claves_sede
                or _clave_texto(n.get("sede_nombre")) in claves_sede
            ]

    # Sesiones de todos los NRCs (mapa {nrc_id: [sesiones]}).
    horarios = horarios_por_nrc()

    # Agrupa los NRCs con cupo por curso, y solo conserva NRCs que tengan
    # al menos una sesión (un NRC sin horario no se puede programar).
    nrcs_por_curso = {}
    for nrc in nrcs:
        if not horarios.get(nrc["id_nrc"]):
            continue
        nrc["curso_id"] = str(nrc["curso_id"] or "").strip().upper()
        nrcs_por_curso.setdefault(nrc["curso_id"], []).append(nrc)

    # ------------------------------------------------------------------
    # 2. Catálogo y reglas desde MongoDB, acotados al programa del perfil.
    #    Si MongoDB no está disponible o la malla está vacía, se degrada a
    #    PostgreSQL (curso + reglas opcionales) para que el generador siga
    #    siendo útil.
    # ------------------------------------------------------------------
    programa_perfil = (
        _texto_opcional(perfil.get("programa")) if perfil else None
    )
    try:
        catalogo = obtener_catalogo(programa_perfil)
    except HTTPException:
        catalogo = []
    if not catalogo:
        try:
            catalogo = catalogo_desde_postgres(programa_perfil)
        except HTTPException:
            catalogo = []

    try:
        reglas = obtener_reglas()
    except HTTPException:
        reglas = {}

    catalogo_map = {
        str(m.get("codigo")).strip().upper(): m
        for m in catalogo
        if m.get("codigo")
    }

    # ------------------------------------------------------------------
    # 3. Materias que faltan (no aprobadas ni inscritas), sin duplicados.
    # ------------------------------------------------------------------
    faltantes = []
    vistos_catalogo = set()
    for m in catalogo:
        codigo = str(m.get("codigo") or "").strip().upper()
        if not codigo or codigo in vistos_catalogo:
            continue
        vistos_catalogo.add(codigo)
        if codigo not in aprobadas and codigo not in inscritas:
            faltantes.append(m)

    # ------------------------------------------------------------------
    # 4. Filtra elegibles por prerrequisitos.
    # ------------------------------------------------------------------
    elegibles = []
    no_asignadas = []
    for m in faltantes:
        prereqs = prerrequisitos_efectivos(m["codigo"], catalogo_map, reglas)
        pendientes = [p for p in prereqs if p not in aprobadas]
        if pendientes:
            no_asignadas.append(
                {
                    "codigo": m["codigo"],
                    "materia": m.get("nombre"),
                    "motivo": "Prerrequisitos pendientes",
                    "prerrequisitos_pendientes": pendientes,
                }
            )
        else:
            elegibles.append(m)

    # ------------------------------------------------------------------
    # 5. Selección greedy sin choques.
    #    Orden: primero las materias que el estudiante marcó como pendientes en
    #    matriz_por_ver (si hay datos), luego semestre ascendente, luego las
    #    materias con menos NRCs disponibles (las más restringidas) y por
    #    último las de más créditos. Sin matriz, el orden es el de siempre.
    # ------------------------------------------------------------------
    def _nrcs_de(materia):
        return nrcs_por_curso.get(materia["codigo"], [])

    elegibles.sort(
        key=lambda m: (
            0 if m["codigo"] in por_ver else 1,
            a_entero(m.get("semestre"), 999),
            len(_nrcs_de(m)),
            -a_entero(m.get("creditos"), 0),
        )
    )

    asignadas = []   # materias con NRC elegido y sus sesiones
    ocupados = []    # intervalos ya usados: (dia, inicio_min, fin_min)

    for m in elegibles:
        candidatos = _nrcs_de(m)
        # Prefiere NRCs con menos sesiones y menos inscritos (más fácil de
        # encajar y reparte la carga).
        candidatos.sort(
            key=lambda n: (
                len(horarios.get(n["id_nrc"], [])),
                a_entero(n.get("inscritos_efectivos"), 0),
            )
        )

        elegido = None
        for nrc in candidatos:
            sesiones = horarios[nrc["id_nrc"]]
            if not choca(sesiones, ocupados):
                elegido = (nrc, sesiones)
                break

        if elegido is None:
            motivo = (
                "Choque de horario con las materias asignadas"
                if candidatos
                else "Sin NRC disponible (sin cupo u oferta)"
            )
            no_asignadas.append(
                {"codigo": m["codigo"], "materia": m.get("nombre"), "motivo": motivo}
            )
            continue

        nrc, sesiones = elegido
        asignadas.append(
            {
                "codigo": m["codigo"],
                "materia": nrc["materia"],
                "creditos": nrc["creditos"],
                "nrc_id": nrc["id_nrc"],
                "periodo": nrc.get("periodo"),
                "jornada": nrc["jornada"],
                "modalidad": nrc["modalidad"],
                "estado": nrc.get("estado"),
                "docente_id": nrc.get("docente_id"),
                "docente_nombre": nrc.get("docente_nombre"),
                "sede_id": nrc.get("sede_id"),
                "sede_nombre": nrc.get("sede_nombre"),
                "cupo_max": nrc.get("cupo_max"),
                "inscritos": nrc.get("inscritos_efectivos"),
                "sesiones": [
                    {
                        "numero_sesion": s["numero_sesion"],
                        "dia": s["dia"],
                        "dia_nombre": DIA_NOMBRES.get(s["dia"], s["dia"]),
                        "hora_inicio": s["hora_inicio"],
                        "hora_fin": s["hora_fin"],
                        "salon": s.get("salon"),
                        "edificio": s.get("edificio"),
                    }
                    for s in sesiones
                ],
            }
        )
        for s in sesiones:
            ocupados.append(
                (s["dia"], a_minutos(s["hora_inicio"]), a_minutos(s["hora_fin"]))
            )

    # ------------------------------------------------------------------
    # 6. Persistencia: inserta las inscripciones elegidas, re-validando
    #    cupo y choque dentro de una transacción (defensa ante carreras).
    # ------------------------------------------------------------------
    confirmadas = []
    conn = conectar()
    try:
        with conn.cursor() as cur:
            for a in asignadas:
                nrc_id = a["nrc_id"]

                # Cupo real (bloquea la fila durante la validación).
                cur.execute(
                    "SELECT cupo_max, inscritos FROM nrc "
                    "WHERE id_nrc = %s FOR UPDATE",
                    (nrc_id,),
                )
                fila_nrc = cur.fetchone()
                if fila_nrc is None:
                    continue
                cur.execute(
                    "SELECT COUNT(*) AS total FROM inscripcion WHERE nrc_id = %s",
                    (nrc_id,),
                )
                inscritos_local = int(cur.fetchone()["total"] or 0)
                inscritos_efectivos = max(
                    int(fila_nrc["inscritos"] or 0), inscritos_local
                )
                if (
                    fila_nrc["cupo_max"] is not None
                    and inscritos_efectivos >= int(fila_nrc["cupo_max"])
                ):
                    continue

                # No duplicar una inscripción ya existente.
                cur.execute(
                    "SELECT 1 FROM inscripcion "
                    "WHERE estudiante_id = %s AND nrc_id = %s",
                    (estudiante_id, nrc_id),
                )
                if cur.fetchone():
                    continue

                # Choque contra lo ya inscrito (incluye lo insertado en esta tx).
                cur.execute(
                    """
                    SELECT 1
                    FROM inscripcion i
                    JOIN horario h1 ON h1.nrc_id = i.nrc_id
                    JOIN horario h2 ON h2.nrc_id = %(nrc_id)s
                    WHERE i.estudiante_id = %(estudiante_id)s
                      AND h2.dia = h1.dia
                      AND h2.hora_inicio < h1.hora_fin
                      AND h1.hora_inicio < h2.hora_fin
                    LIMIT 1
                    """,
                    {"nrc_id": nrc_id, "estudiante_id": estudiante_id},
                )
                if cur.fetchone():
                    continue

                cur.execute(
                    """
                    INSERT INTO inscripcion (estudiante_id, nrc_id, fecha)
                    VALUES (%s, %s, CURRENT_DATE)
                    ON CONFLICT (estudiante_id, nrc_id) DO NOTHING
                    """,
                    (estudiante_id, nrc_id),
                )
                if cur.rowcount == 0:
                    # Otra transacción ya insertó esta inscripción: se evita
                    # duplicar y no se vuelve a sumar al contador del NRC.
                    confirmadas.append(a)
                    continue

                # Mantiene coherentes los contadores del NRC. Igual que en
                # POST /api/inscribir: el máximo entre el contador de la oferta
                # y las inscripciones locales evita subcontar.
                cur.execute(
                    """
                    UPDATE nrc SET
                        inscritos = GREATEST(COALESCE(inscritos, 0), %s) + 1,
                        saldo = CASE
                            WHEN cupo_max IS NOT NULL
                                THEN cupo_max
                                     - (GREATEST(COALESCE(inscritos, 0), %s)
                                        + 1)
                            ELSE saldo
                        END
                    WHERE id_nrc = %s
                    """,
                    (inscritos_local, inscritos_local, nrc_id),
                )
                confirmadas.append(a)
        conn.commit()
    finally:
        conn.close()

    # Las que no se confirmaron pasan a "no asignadas".
    confirmadas_ids = {a["nrc_id"] for a in confirmadas}
    for a in asignadas:
        if a["nrc_id"] not in confirmadas_ids:
            no_asignadas.append(
                {
                    "codigo": a["codigo"],
                    "materia": a["materia"],
                    "motivo": "Cupo o choque detectado al confirmar la inscripción",
                }
            )

    return {
        "estudiante_id": estudiante_id,
        "ya_existe": False,
        "resumen": {
            "asignadas": len(confirmadas),
            "no_asignadas": len(no_asignadas),
        },
        "horario": confirmadas,
        "no_asignadas": no_asignadas,
    }


# ---------------------------------------------------------------------------
# B. Panel de Administrador — Reglas de negocio dinámicas (MongoDB)
# ---------------------------------------------------------------------------
@app.get("/api/admin/reglas")
def listar_reglas():
    """
    Devuelve las reglas de negocio dinámicas guardadas en ``reglas_negocio``,
    ordenadas por materia.

    Cada elemento conserva el contrato básico ``{materia, prerrequisitos}`` y,
    si el documento fue sembrado por ``seed_reglas.py``, incluye además
    ``nombre``, ``creditos`` y ``semestre`` para el panel de administración.
    """
    return obtener_reglas_completas()


@app.put("/api/admin/reglas")
def actualizar_reglas(body: Union[ReglaNegocioIn, List[ReglaNegocioIn]]):
    """
    Crea/actualiza reglas de negocio dinámicas en ``reglas_negocio``.

    Acepta una regla suelta:
        {"materia": "CAL-201", "prerrequisitos": ["CAL-101"]}
    o una lista de reglas. Usa upsert por ``materia``, así el administrador
    puede cambiar los prerrequisitos sin tocar código.

    Solo se actualiza ``prerrequisitos`` con ``$set``; los metadatos que siembra
    ``seed_reglas.py`` (``nombre``, ``creditos``, ``semestre``) se conservan.
    Para una materia nueva solo se crea ``materia`` con ``$setOnInsert``.
    """
    reglas = body if isinstance(body, list) else [body]

    # Validación previa: toda regla debe indicar una materia.
    for r in reglas:
        if not r.materia.strip():
            raise HTTPException(
                status_code=422,
                detail="Cada regla debe indicar el código de la materia en 'materia'",
            )

    db = mongo_db()
    col = db[MONGO_COL_REGLAS]

    guardadas = []
    try:
        for r in reglas:
            materia = r.materia.strip().upper()
            if not materia:
                continue
            # Deduplica y normaliza los prerrequisitos, conservando el orden.
            prereqs = []
            vistos = set()
            for p in r.prerrequisitos:
                p_norm = str(p).strip().upper()
                if p_norm and p_norm not in vistos:
                    vistos.add(p_norm)
                    prereqs.append(p_norm)

            # Actualiza SOLO los prerrequisitos. Los metadatos del catálogo
            # (nombre, creditos, semestre) que sembró seed_reglas.py no se
            # sobrescriben. En un documento nuevo solo se crea la materia.
            col.update_one(
                {"materia": materia},
                {
                    "$set": {"prerrequisitos": prereqs},
                    "$setOnInsert": {"materia": materia},
                },
                upsert=True,
            )
            guardadas.append({"materia": materia, "prerrequisitos": prereqs})
    except PyMongoError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"No se pudo escribir reglas_negocio en MongoDB: {exc}",
        ) from exc

    return {"ok": True, "reglas": guardadas}
