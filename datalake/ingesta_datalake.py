#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ingesta_datalake.py
===================
Ingesta **Data-Driven** del Sistema de Inscripción de Materias.

Fuentes reales
--------------
  * ``datalake/entrada/chorarios_26082026.xlsx``
      Oferta académica (una fila por NRC/sesión, 43 columnas).
  * ``datalake/entrada/ruta_nueva_isum_v2.pdf``
      Malla curricular de Ingeniería de Sistemas. Es un **escaneo sin capa de
      texto**; el script intenta pdfplumber, luego OCR opcional y, si no hay
      texto utilizable, cae al fallback ``datalake/malla_curricular.json``.
  * ``datalake/malla_curricular.json``
      Transcripción asistida (best-effort) de esa malla. No se inventan datos:
      el JSON declara su origen y el Admin puede corregirlo.

Arquitectura Medallón
---------------------
  BRONZE  copia cruda de los archivos de entrada en ``datalake/bronze/`` con
          marca de tiempo.
  SILVER  Parquet limpio en ``datalake/silver/``:
            oferta.parquet, horario.parquet, malla.parquet,
            docentes.parquet, sedes.parquet
  GOLD    Agregados en ``datalake/gold/``:
            oferta_resumen.parquet, malla_resumen.parquet
  POSTGRESQL
          Upsert idempotente de curso, docente, sede, nrc y horario.
  MONGODB (``inscripcion``)
          Colecciones ``malla_curricular`` y ``reglas_negocio``.

Modos (nombres exactos que usa el backend)
------------------------------------------
  --excel <ruta> --solo-oferta
      Solo oferta: Bronze/Silver/Gold + PostgreSQL. No toca malla/Mongo.
  --pdf <ruta> --solo-malla
      Solo malla: Bronze/Silver malla + MongoDB. No toca oferta/PostgreSQL.
  --sin-bd
      Solo Bronze/Silver/Gold (ni PostgreSQL ni MongoDB).
  (sin flags)
      Procesa ambos desde ``datalake/entrada/`` si existen.

Compatibilidad CSV
------------------
  El backend actual llama ``--oferta <csv> --solo-oferta`` para la carga de
  oferta en formato CSV. Se conserva ese alias, además de ``--matriz`` /
  ``--solo-matriz`` para la matriz histórica de estudiantes.

Salida machine-readable
-----------------------
  Al final imprime una única línea::

      RESUMEN_JSON: {"cursos": ..., "nrcs": ..., "sesiones": ..., ...}

  El backend la parsea para devolver los conteos de la carga.

Dependencias
------------
  pip install pandas openpyxl pyarrow pdfplumber psycopg2-binary pymongo
  # OCR opcional:
  pip install pytesseract pillow pypdfium2
  # y el binario del sistema: tesseract (con datos de idioma español)

  ``pandas/pyarrow/openpyxl`` son obligatorios para procesar archivos.
  ``pdfplumber`` solo es necesario para intentar leer el PDF; si falta, se usa
  el fallback JSON. ``psycopg2`` y ``pymongo`` solo se usan cuando el modo los necesita
  (``--sin-bd`` no se conecta a ninguna base).

Códigos de salida
-----------------
  0  Éxito.
  2  Error controlado (archivo faltante, dependencia, BD no disponible...).
  1  Error inesperado (se muestra traza solo con --debug).

Uso
---
  python3 datalake/ingesta_datalake.py --excel datalake/entrada/chorarios_26082026.xlsx --solo-oferta
  python3 datalake/ingesta_datalake.py --pdf datalake/entrada/ruta_nueva_isum_v2.pdf --solo-malla
  python3 datalake/ingesta_datalake.py --sin-bd
  python3 datalake/ingesta_datalake.py
  python3 datalake/ingesta_datalake.py --oferta datalake/ejemplos/oferta_academica.csv --solo-oferta
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import time
import traceback
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Dependencias opcionales. Se importan de forma tolerante para que
# ``python -m py_compile`` y ``--help`` funcionen sin instalar nada.
# ---------------------------------------------------------------------------
try:
    import pandas as pd
    import pyarrow  # noqa: F401  # engine de pandas.to_parquet
except ImportError as _exc_pandas:  # pragma: no cover - depende del entorno
    pd = None
    pyarrow = None
    _ERROR_PANDAS: Optional[ImportError] = _exc_pandas
else:
    _ERROR_PANDAS = None

try:
    import pdfplumber
except ImportError as _exc_pdfplumber:  # pragma: no cover
    pdfplumber = None
    _ERROR_PDFPLUMBER: Optional[ImportError] = _exc_pdfplumber
else:
    _ERROR_PDFPLUMBER = None

try:
    import psycopg2
except ImportError as _exc_psycopg2:  # pragma: no cover
    psycopg2 = None
    _ERROR_PSYCOPG2: Optional[ImportError] = _exc_psycopg2
else:
    _ERROR_PSYCOPG2 = None

try:
    from pymongo import MongoClient, UpdateOne
    from pymongo.errors import PyMongoError
except ImportError as _exc_pymongo:  # pragma: no cover
    MongoClient = None
    UpdateOne = None

    class PyMongoError(Exception):
        """Sustituto local si pymongo no está instalado."""

    _ERROR_PYMONGO: Optional[ImportError] = _exc_pymongo
else:
    _ERROR_PYMONGO = None

try:
    import pytesseract
except ImportError:  # pragma: no cover - OCR opcional
    pytesseract = None

try:
    from PIL import Image  # noqa: F401  # usado por pytesseract/page.to_image
except ImportError:  # pragma: no cover - OCR opcional
    Image = None


# ---------------------------------------------------------------------------
# Rutas del Data Lake
# ---------------------------------------------------------------------------
RAIZ = Path(__file__).resolve().parent
BRONZE = RAIZ / "bronze"
SILVER = RAIZ / "silver"
GOLD = RAIZ / "gold"
ENTRADA = RAIZ / "entrada"

EXCEL_DEFAULT = ENTRADA / "chorarios_26082026.xlsx"
PDF_DEFAULT = ENTRADA / "ruta_nueva_isum_v2.pdf"
MALLA_JSON_DEFAULT = RAIZ / "malla_curricular.json"
MATRIZ_DEFAULT = RAIZ / "matriz_estudiante.csv"


# ---------------------------------------------------------------------------
# Configuración PostgreSQL / MongoDB
# ---------------------------------------------------------------------------
def _entero_env(nombre: str, default: int) -> int:
    valor = os.getenv(nombre)
    if valor is None or not str(valor).strip():
        return default
    try:
        return int(valor)
    except (TypeError, ValueError):
        return default


DB_CONFIG: Dict[str, Any] = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": _entero_env("DB_PORT", 5432),
    "dbname": os.getenv("DB_NAME", "universidad"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "postgres"),
}

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "inscripcion")
MONGO_TIMEOUT_MS = _entero_env("MONGO_TIMEOUT_MS", 5000)
DB_TIMEOUT = _entero_env("DB_TIMEOUT", 8)

MAX_CODIGO = 10  # VARCHAR(10) de curso.codigo_alfanumerico / nrc.curso_id

# ---------------------------------------------------------------------------
# Contratos de columnas
# ---------------------------------------------------------------------------
# Columnas que realmente usa el ETL del Excel. El archivo real trae 43; las
# demás se ignoran de forma deliberada.
COLS_EXCEL_REQUERIDAS = [
    "periodo", "nrc", "alfa", "num", "estado_nrc", "titulo",
    "horas_minimas", "horas_maximas", "horas_creditos_sede",
    "cupo", "inscritos", "saldo", "id_docente", "nombre_docente",
    "sede", "desc_sede", "edificio", "salon", "hi", "hf",
    "l", "m", "i", "j", "v", "s", "d",
    "tipo_horario", "metodo_educativo",
]

COLS_OFERTA = [
    "periodo", "id_nrc", "codigo_curso", "nombre_curso", "creditos",
    "jornada", "modalidad", "cupo_max", "inscritos", "saldo", "estado",
    "sede_id", "sede_nombre", "docente_id", "docente_nombre",
    # Extras útiles para PostgreSQL/Gold; no rompen el contrato principal.
    "programa", "semestre", "horas",
]
COLS_OFERTA_ENTERAS = ["id_nrc", "creditos", "cupo_max", "inscritos", "saldo",
                       "semestre", "horas"]
COLS_OFERTA_TEXTO = ["periodo", "codigo_curso", "nombre_curso", "jornada",
                     "modalidad", "estado", "sede_id", "sede_nombre",
                     "docente_id", "docente_nombre", "programa"]

COLS_HORARIO = ["id_nrc", "numero_sesion", "dia", "hora_inicio", "hora_fin",
                "salon", "edificio"]
COLS_HORARIO_ENTERAS = ["id_nrc", "numero_sesion", "dia"]
COLS_HORARIO_TEXTO = ["hora_inicio", "hora_fin", "salon", "edificio"]

COLS_DOCENTES = ["id_docente", "nombre"]
COLS_SEDES = ["codigo", "nombre"]

COLS_MALLA = ["programa", "sede", "semestre", "codigo", "nombre", "creditos",
              "horas", "prerrequisitos"]
COLS_MALLA_ENTERAS = ["semestre", "creditos", "horas"]
COLS_MALLA_TEXTO = ["programa", "sede", "codigo", "nombre", "prerrequisitos"]

COLS_OFERTA_RESUMEN = [
    "codigo_curso", "nombre_curso", "sede_id", "sede_nombre", "jornada",
    "nrcs", "cupo_total", "inscritos_total", "saldo_total", "ocupacion",
    "programa", "semestre",
]
COLS_MALLA_RESUMEN = [
    "programa", "sede", "semestre", "num_cursos", "creditos_total",
    "horas_total", "cursos",
]

# Días de la semana: el nombre de columna del Excel es un marcador; lo que
# importa es que la celda NO esté vacía. L=1 (lunes) ... D=7 (domingo).
DIAS = [("l", 1), ("m", 2), ("i", 3), ("j", 4), ("v", 5), ("s", 6), ("d", 7)]
SEDES_CANONICAS = {
    "UMD": "ENGATIVA PRESENCIAL",
    "CBP": "CD BOLIVAR PRESENCIAL",
    "VGA": "VICERECTORIA GENERAL ACADEMICA",
    "RUP": "SANTAFE PRESENCIAL",
}

# CSV de oferta (formato histórico, una fila por sesión).
COLS_CSV_OFERTA = [
    "codigo_curso", "nombre_curso", "creditos", "id_nrc", "jornada",
    "modalidad", "cupo_max", "numero_sesion", "dia", "hora_inicio",
    "hora_fin",
]
COLS_CSV_MATRIZ = ["estudiante_id", "codigo_curso"]

ESTADOS_APROBADA = {
    "aprobada", "aprobadas", "aprobado", "aprobados",
    "vista", "vistas", "visto", "vistos",
}
ESTADOS_POR_VER = {
    "por_ver", "porver", "por_verificar", "pendiente", "pendientes",
}


# ---------------------------------------------------------------------------
# Excepciones y utilidades de salida
# ---------------------------------------------------------------------------
class IngestaError(Exception):
    """Error controlado de la ingesta (se informa sin traza)."""


class DependenciaFaltante(IngestaError):
    """Falta una dependencia de Python o un binario externo."""


def _log(mensaje: str = "") -> None:
    print(mensaje, flush=True)


def _aviso(mensaje: str) -> None:
    print(f"   [AVISO] {mensaje}", flush=True)


def _info(mensaje: str) -> None:
    print(f"   [INFO] {mensaje}", flush=True)


def _banner() -> None:
    _log("=" * 78)
    _log("Ingesta Data Lake — Sistema de Inscripción de Materias")
    _log("Arquitectura Medallón: Bronze -> Silver -> Gold -> PostgreSQL/MongoDB")
    _log("=" * 78)


def _asegurar_dirs() -> None:
    for carpeta in (BRONZE, SILVER, GOLD):
        carpeta.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Utilidades de normalización
# ---------------------------------------------------------------------------
def _sin_acentos(texto: Any) -> str:
    descompuesto = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def _normalizar_columna(nombre: Any) -> str:
    """Encabezado -> minúsculas, sin BOM/acentos, espacios/guiones a ``_``."""
    texto = _sin_acentos(nombre).lstrip("\ufeff").strip().lower()
    texto = re.sub(r"[\s\-]+", "_", texto)
    return re.sub(r"_+", "_", texto).strip("_")


def _es_nulo(valor: Any) -> bool:
    if valor is None:
        return True
    if isinstance(valor, str):
        return False
    if isinstance(valor, (list, dict, tuple, set)):
        return False
    try:
        if pd is not None:
            return bool(pd.isna(valor))
    except (TypeError, ValueError):
        return False
    return False


def _texto(valor: Any) -> str:
    """Texto limpio: nulos -> '', números enteros sin ``.0``, espacios colapsados."""
    if valor is None:
        return ""
    if isinstance(valor, str):
        return re.sub(r"\s+", " ", valor).strip()
    if isinstance(valor, (list, dict, tuple, set)):
        return str(valor)
    if _es_nulo(valor):
        return ""
    if isinstance(valor, float):
        if math.isnan(valor) or math.isinf(valor):
            return ""
        if valor.is_integer():
            return str(int(valor))
        return format(valor, ".15g")
    return re.sub(r"\s+", " ", str(valor)).strip()


def _codigo(valor: Any) -> str:
    """Código alfanumérico: sin espacios, MAYÚSCULAS (conserva guiones)."""
    return re.sub(r"\s+", "", _texto(valor)).upper()


def _entero(valor: Any) -> Optional[int]:
    """Entero nullable; rechaza decimales y texto no numérico."""
    texto = _texto(valor).replace(",", "")
    if not texto:
        return None
    try:
        numero = float(texto)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numero) or not numero.is_integer():
        return None
    return int(numero)


def _hora_hhmmss(valor: Any) -> Optional[str]:
    """
    Normaliza una hora a ``HH:MM:SS``.

    Acepta ``HH:MM``, ``HH:MM:SS`` y el formato HHMM del Excel (915 -> 09:15,
    2030 -> 20:30). Devuelve ``None`` si está vacía o fuera de rango.
    """
    texto = _texto(valor)
    if not texto:
        return None

    if ":" in texto:
        partes = texto.split(":")
        if len(partes) == 2:
            hh_s, mm_s = partes
            ss_s = "0"
        elif len(partes) == 3:
            hh_s, mm_s, ss_s = partes
        else:
            return None
        try:
            hh, mm, ss = int(hh_s), int(mm_s), int(ss_s)
        except (TypeError, ValueError):
            return None
    else:
        digitos = re.sub(r"\D", "", texto)
        if not digitos:
            return None
        if len(digitos) <= 2:
            hh, mm, ss = int(digitos), 0, 0
        elif len(digitos) == 3:
            hh, mm, ss = int(digitos[0]), int(digitos[1:]), 0
        elif len(digitos) == 4:
            hh, mm, ss = int(digitos[:2]), int(digitos[2:]), 0
        elif len(digitos) == 6:
            hh, mm, ss = int(digitos[:2]), int(digitos[2:4]), int(digitos[4:])
        else:
            return None

    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
        return None
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


def _jornada_desde_hora(hora_inicio: Optional[str]) -> str:
    if not hora_inicio:
        return "Virtual"
    hora = int(hora_inicio[:2])
    if hora < 12:
        return "Diurna"
    if hora < 18:
        return "Mixta"
    return "Nocturna"


def _modalidad_desde_metodo(metodo: Any, tipo_horario: Any,
                            hora_inicio: Optional[str]) -> str:
    if not hora_inicio:
        return "Virtual"
    metodo_norm = _sin_acentos(_texto(metodo)).upper()
    if metodo_norm in {"VIRT", "VIRTUAL", "DISTANCIA"}:
        return "Virtual"
    if metodo_norm in {"DIST", "DISTANCIA", "HIBRIDA", "HÍBRIDA"}:
        return "Distancia"
    if metodo_norm in {"TRAD", "TRADICIONAL", "PRESENCIAL"}:
        return "Presencial"
    tipo_norm = _sin_acentos(_texto(tipo_horario)).upper()
    if tipo_norm in {"VIR", "VIRTUAL"}:
        return "Virtual"
    return "Presencial"


def _sede_nombre(codigo: str, descripcion: Any) -> str:
    desc = _texto(descripcion)
    return SEDES_CANONICAS.get(codigo, desc or codigo)


def _normalizar_prerrequisitos(valor: Any) -> List[str]:
    """Convierte lista/JSON/texto separado por comas en una lista de códigos."""
    if valor is None or _es_nulo(valor):
        return []
    if isinstance(valor, (list, tuple, set)):
        crudos = list(valor)
    elif isinstance(valor, str):
        texto = valor.strip()
        if not texto or texto in {"[]", "-", "N/A", "NA", "NINGUNO"}:
            return []
        if texto.startswith("["):
            try:
                cargados = json.loads(texto)
            except (TypeError, ValueError):
                crudos = re.split(r"[,;|/]", texto)
            else:
                crudos = cargados if isinstance(cargados, list) else [cargados]
        else:
            crudos = re.split(r"[,;|/]", texto)
    else:
        crudos = [valor]
    resultado: List[str] = []
    for crudo in crudos:
        codigo = _codigo(crudo)
        if codigo and codigo not in resultado:
            resultado.append(codigo)
    return resultado


def _json_lista(codigos: Sequence[str]) -> str:
    return json.dumps(list(codigos), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Construcción de DataFrames tipados
# ---------------------------------------------------------------------------
def _df_tipado(
    registros: Iterable[Dict[str, Any]],
    columnas: Sequence[str],
    enteras: Sequence[str] = (),
    textos: Sequence[str] = (),
    flotantes: Sequence[str] = (),
) -> Any:
    if pd is None:  # pragma: no cover - se valida antes
        raise DependenciaFaltante("pandas no está instalado.")
    df = pd.DataFrame(list(registros), columns=list(columnas))
    for columna in textos:
        df[columna] = df[columna].astype("string")
    for columna in enteras:
        df[columna] = pd.to_numeric(df[columna], errors="coerce").astype("Int64")
    for columna in flotantes:
        df[columna] = pd.to_numeric(df[columna], errors="coerce").astype("float64")
    return df


def _df_vacio_oferta() -> Any:
    return _df_tipado([], COLS_OFERTA, COLS_OFERTA_ENTERAS, COLS_OFERTA_TEXTO)


def _df_vacio_horario() -> Any:
    return _df_tipado([], COLS_HORARIO, COLS_HORARIO_ENTERAS, COLS_HORARIO_TEXTO)


def _df_vacio_docentes() -> Any:
    return _df_tipado([], COLS_DOCENTES, [], ["id_docente", "nombre"])


def _df_vacio_sedes() -> Any:
    return _df_tipado([], COLS_SEDES, [], ["codigo", "nombre"])


def _df_vacio_malla() -> Any:
    return _df_tipado([], COLS_MALLA, COLS_MALLA_ENTERAS, COLS_MALLA_TEXTO)


def _df_vacio_oferta_resumen() -> Any:
    return _df_tipado(
        [], COLS_OFERTA_RESUMEN,
        ["nrcs", "cupo_total", "inscritos_total", "saldo_total", "semestre"],
        ["codigo_curso", "nombre_curso", "sede_id", "sede_nombre", "jornada",
         "programa"],
        ["ocupacion"],
    )


def _df_vacio_malla_resumen() -> Any:
    return _df_tipado(
        [], COLS_MALLA_RESUMEN,
        ["semestre", "num_cursos", "creditos_total", "horas_total"],
        ["programa", "sede", "cursos"],
    )


# ---------------------------------------------------------------------------
# Dependencias
# ---------------------------------------------------------------------------
def _requerir_pandas(etiqueta: str) -> None:
    if pd is None or pyarrow is None:
        detalle = f" ({_ERROR_PANDAS})" if _ERROR_PANDAS else ""
        raise DependenciaFaltante(
            f"Faltan dependencias para {etiqueta}: pandas y pyarrow{detalle}. "
            "Instálelas con: pip install pandas pyarrow openpyxl"
        )


def _requerir_openpyxl() -> None:
    try:
        import openpyxl  # noqa: F401
    except ImportError as exc:
        raise DependenciaFaltante(
            "Falta openpyxl, necesario para leer el Excel. "
            "Instálelo con: pip install openpyxl"
        ) from exc


def _requerir_psycopg2() -> None:
    if psycopg2 is None:
        detalle = f" ({_ERROR_PSYCOPG2})" if _ERROR_PSYCOPG2 else ""
        raise DependenciaFaltante(
            f"Falta psycopg2{detalle}. Instálelo con: "
            "pip install psycopg2-binary"
        )


def _requerir_pymongo() -> None:
    if MongoClient is None:
        detalle = f" ({_ERROR_PYMONGO})" if _ERROR_PYMONGO else ""
        raise DependenciaFaltante(
            f"Falta pymongo{detalle}. Instálelo con: pip install pymongo"
        )


# ---------------------------------------------------------------------------
# Lectura de archivos
# ---------------------------------------------------------------------------
def _leer_excel_df(ruta: Path) -> Any:
    _requerir_pandas("el Excel de oferta")
    _requerir_openpyxl()
    try:
        df = pd.read_excel(ruta, sheet_name=0, dtype=object, engine="openpyxl")
    except Exception as exc:  # noqa: BLE001 - se informa con contexto
        raise IngestaError(
            f"No se pudo leer el Excel '{ruta}': {exc}. "
            "Verifique que sea un .xlsx válido y que openpyxl esté instalado."
        ) from exc

    if df.empty and len(df.columns) == 0:
        raise IngestaError(f"El Excel '{ruta}' no tiene hojas/columnas legibles.")

    df.columns = [_normalizar_columna(c) for c in df.columns]
    duplicadas = [c for c in pd.Index(df.columns) if list(df.columns).count(c) > 1]
    if duplicadas:
        raise IngestaError(
            f"El Excel '{ruta}' tiene columnas duplicadas tras normalizar: "
            f"{sorted(set(duplicadas))}"
        )
    _validar_columnas(df, COLS_EXCEL_REQUERIDAS, f"Excel '{ruta.name}'")
    return df


def _leer_csv_df(ruta: Path, etiqueta: str) -> Any:
    _requerir_pandas(etiqueta)
    ultimo_error: Optional[Exception] = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            df = pd.read_csv(
                ruta, dtype=str, keep_default_na=False, encoding=encoding,
                sep=",",
            )
            break
        except UnicodeDecodeError as exc:
            ultimo_error = exc
            continue
        except Exception as exc:  # noqa: BLE001
            raise IngestaError(
                f"No se pudo leer el CSV '{ruta}': {exc}"
            ) from exc
    else:
        raise IngestaError(
            f"No se pudo decodificar el CSV '{ruta}' "
            f"(utf-8/latin-1): {ultimo_error}"
        )

    if len(df.columns) == 1 and ";" in str(df.columns[0]):
        # Tolerancia a CSV separados por punto y coma.
        try:
            df = pd.read_csv(
                ruta, dtype=str, keep_default_na=False, encoding=encoding,
                sep=";",
            )
        except Exception as exc:  # noqa: BLE001
            raise IngestaError(
                f"No se pudo leer el CSV '{ruta}' con separador ';': {exc}"
            ) from exc

    df.columns = [_normalizar_columna(c) for c in df.columns]
    if len(set(df.columns)) != len(df.columns):
        raise IngestaError(
            f"El CSV '{ruta}' tiene columnas duplicadas tras normalizar."
        )
    df = df.loc[~(df.astype(str).apply(lambda fila: fila.str.strip().eq("").all(), axis=1))]
    return df.reset_index(drop=True)


def _validar_columnas(df: Any, requeridas: Sequence[str], etiqueta: str) -> None:
    faltan = [c for c in requeridas if c not in df.columns]
    if faltan:
        raise IngestaError(
            f"{etiqueta}: faltan columnas obligatorias {faltan}. "
            f"Columnas encontradas: {list(df.columns)}"
        )


# ---------------------------------------------------------------------------
# Oferta: Excel
# ---------------------------------------------------------------------------
@dataclass
class OfertaData:
    oferta: Any
    horario: Any
    docentes: Any
    sedes: Any
    metricas: Dict[str, int] = field(default_factory=dict)
    origen: str = "excel"
    archivo: Optional[Path] = None


def _atributos_nrc_desde_fila(fila: Any, periodo_override: Optional[str]) -> Dict[str, Any]:
    """Extrae de una fila del Excel los atributos de un NRC."""
    sede_id = _codigo(fila.get("sede"))
    desc_sede = fila.get("desc_sede")
    codigo_curso = _codigo(fila.get("alfa")) + _codigo(fila.get("num"))
    hora_inicio = _hora_hhmmss(fila.get("hi"))
    hora_fin = _hora_hhmmss(fila.get("hf"))

    # Orden de preferencia: horas/creditos de la sede, horas mínimas, horas
    # máximas e intensidad horaria. Es un fallback documentado, no un dato
    # inventado: si todo falta, el crédito queda en 0 y se advierte.
    creditos = _entero(fila.get("horas_creditos_sede"))
    if creditos is None or creditos <= 0:
        creditos = _entero(fila.get("horas_minimas"))
    if creditos is None or creditos <= 0:
        creditos = _entero(fila.get("horas_maximas"))
    if creditos is None or creditos <= 0:
        creditos = _entero(fila.get("intensidad_horaria"))
    if creditos is None:
        creditos = 0

    horas = _entero(fila.get("horas_minimas"))
    if horas is None or horas <= 0:
        horas = _entero(fila.get("horas_maximas"))
    if horas is None or horas <= 0:
        horas = _entero(fila.get("intensidad_horaria"))
    if horas is None:
        horas = None

    periodo = _texto(periodo_override) if periodo_override else _texto(fila.get("periodo"))

    return {
        "periodo": periodo or None,
        "codigo_curso": codigo_curso,
        "nombre_curso": _texto(fila.get("titulo")) or codigo_curso,
        "creditos": creditos,
        "jornada": _jornada_desde_hora(hora_inicio),
        "modalidad": _modalidad_desde_metodo(
            fila.get("metodo_educativo"), fila.get("tipo_horario"), hora_inicio
        ),
        "cupo_max": _entero(fila.get("cupo")) or 0,
        "inscritos": _entero(fila.get("inscritos")) or 0,
        "saldo": _entero(fila.get("saldo")) or 0,
        "estado": "Activo",
        "sede_id": sede_id or None,
        "sede_nombre": _sede_nombre(sede_id, desc_sede) if sede_id else None,
        "docente_id": _texto(fila.get("id_docente")) or None,
        "docente_nombre": _texto(fila.get("nombre_docente")) or None,
        "programa": None,
        "semestre": None,
        "horas": horas,
        "_hora_inicio": hora_inicio,
        "_hora_fin": hora_fin,
        "_hora_inicio_min": hora_inicio,
        "_metodo": fila.get("metodo_educativo"),
        "_tipo_horario": fila.get("tipo_horario"),
    }


def procesar_oferta_excel(ruta: Path, periodo_override: Optional[str] = None) -> OfertaData:
    """Lee y limpia el Excel de oferta; devuelve oferta, horario, docentes y sedes."""
    df = _leer_excel_df(ruta)
    metricas: Dict[str, int] = {
        "filas_excel": int(len(df)),
        "filas_activas": 0,
        "filas_inactivas": 0,
        "nrc_sin_id": 0,
        "nrc_sin_codigo": 0,
        "codigos_largos": 0,
        "nrc_sin_nombre": 0,
        "nrc_sin_creditos": 0,
        "nrc_sin_docente": 0,
        "filas_sin_dia": 0,
        "sesiones_sin_horas": 0,
        "horas_invalidas": 0,
        "hora_inicio_mayor": 0,
        "nrc_sin_sesiones": 0,
        "conflictos_nrc": 0,
        "conflictos_curso": 0,
    }

    nrc_map: Dict[int, Dict[str, Any]] = {}
    sesiones: List[Dict[str, Any]] = []
    nombres_por_curso: Dict[str, str] = {}

    for _, fila in df.iterrows():
        estado = _sin_acentos(_texto(fila.get("estado_nrc"))).strip().upper()
        if estado != "ACTIVO":
            metricas["filas_inactivas"] += 1
            continue
        metricas["filas_activas"] += 1

        nrc = _entero(fila.get("nrc"))
        if nrc is None:
            metricas["nrc_sin_id"] += 1
            continue

        atributos = _atributos_nrc_desde_fila(fila, periodo_override)
        atributos["id_nrc"] = nrc
        codigo_curso = atributos["codigo_curso"]
        if not codigo_curso:
            metricas["nrc_sin_codigo"] += 1
            continue
        if len(codigo_curso) > MAX_CODIGO:
            metricas["codigos_largos"] += 1
            continue
        if not _texto(fila.get("titulo")):
            metricas["nrc_sin_nombre"] += 1
        if atributos["creditos"] == 0:
            metricas["nrc_sin_creditos"] += 1
        if not atributos["docente_id"]:
            metricas["nrc_sin_docente"] += 1

        existente = nrc_map.get(nrc)
        if existente is None:
            nrc_map[nrc] = atributos
            nombres_por_curso.setdefault(codigo_curso, atributos["nombre_curso"])
        else:
            if existente["codigo_curso"] != codigo_curso:
                metricas["conflictos_nrc"] += 1
                continue
            # Mismo NRC con atributos distintos: se conserva la primera fila,
            # que es la que ya está en nrc_map; las sesiones sí se acumulan.
            # La jornada/modalidad se derivan al final con la sesión más
            # temprana, por lo que no se comparan aquí (un mismo NRC puede
            # tener sesiones en franjas distintas).
            for clave in ("cupo_max", "inscritos", "saldo", "docente_id",
                          "sede_id", "periodo"):
                if existente[clave] != atributos[clave]:
                    metricas["conflictos_nrc"] += 1
                    break
            hora_previa = existente.get("_hora_inicio_min")
            hora_nueva = atributos.get("_hora_inicio_min")
            if hora_nueva and (not hora_previa or hora_nueva < hora_previa):
                existente["_hora_inicio_min"] = hora_nueva
            if (nombres_por_curso.get(codigo_curso)
                    and nombres_por_curso[codigo_curso] != atributos["nombre_curso"]):
                metricas["conflictos_curso"] += 1

        hora_inicio = atributos["_hora_inicio"]
        hora_fin = atributos["_hora_fin"]
        if not hora_inicio or not hora_fin:
            if _texto(fila.get("hi")) and not hora_inicio:
                metricas["horas_invalidas"] += 1
            if _texto(fila.get("hf")) and not hora_fin:
                metricas["horas_invalidas"] += 1
            # HI/HF vacíos => sesión virtual/asincrónica: no se crea horario.
            continue

        if hora_inicio >= hora_fin:
            metricas["hora_inicio_mayor"] += 1

        dias = [dia for columna, dia in DIAS if _texto(fila.get(columna))]
        if not dias:
            metricas["filas_sin_dia"] += 1
            continue

        for dia in dias:
            sesiones.append({
                "id_nrc": nrc,
                "dia": dia,
                "hora_inicio": hora_inicio,
                "hora_fin": hora_fin,
                "salon": _texto(fila.get("salon")) or None,
                "edificio": _texto(fila.get("edificio")) or None,
            })

    if not nrc_map:
        raise IngestaError(
            "El Excel no produjo NRC activos válidos. Revise ESTADO_NRC, "
            "NRC, ALFA/NUM y que el archivo sea la oferta correcta."
        )

    # Deduplicar sesiones exactas antes de numerarlas.
    vistos = set()
    sesiones_unicas: List[Dict[str, Any]] = []
    for sesion in sesiones:
        clave = (
            sesion["id_nrc"], sesion["dia"], sesion["hora_inicio"],
            sesion["hora_fin"], sesion["salon"], sesion["edificio"],
        )
        if clave not in vistos:
            vistos.add(clave)
            sesiones_unicas.append(sesion)

    sesiones_unicas.sort(key=lambda s: (
        s["id_nrc"], s["dia"], s["hora_inicio"], s["hora_fin"],
        s["salon"] or "", s["edificio"] or "",
    ))
    contador: Dict[int, int] = {}
    for sesion in sesiones_unicas:
        contador[sesion["id_nrc"]] = contador.get(sesion["id_nrc"], 0) + 1
        sesion["numero_sesion"] = contador[sesion["id_nrc"]]

    # Jornada/modalidad definitivas: se usa la sesión más temprana del NRC.
    for nrc, atributos in nrc_map.items():
        hora_min = atributos.get("_hora_inicio_min")
        atributos["jornada"] = _jornada_desde_hora(hora_min)
        atributos["modalidad"] = _modalidad_desde_metodo(
            atributos.get("_metodo"), atributos.get("_tipo_horario"), hora_min
        )
        if contador.get(nrc, 0) == 0:
            metricas["nrc_sin_sesiones"] += 1

    oferta = _df_tipado(
        [nrc_map[nrc] for nrc in sorted(nrc_map)],
        COLS_OFERTA, COLS_OFERTA_ENTERAS, COLS_OFERTA_TEXTO,
    )
    horario = _df_tipado(
        sesiones_unicas, COLS_HORARIO, COLS_HORARIO_ENTERAS, COLS_HORARIO_TEXTO,
    )
    docentes = _docentes_desde_oferta(oferta)
    sedes = _sedes_desde_oferta(oferta)

    metricas["nrcs"] = int(len(oferta))
    metricas["cursos"] = int(oferta["codigo_curso"].nunique(dropna=True))
    metricas["sesiones"] = int(len(horario))
    return OfertaData(
        oferta=oferta, horario=horario, docentes=docentes, sedes=sedes,
        metricas=metricas, origen="excel", archivo=ruta,
    )


def _docentes_desde_oferta(oferta: Any) -> Any:
    if oferta.empty:
        return _df_vacio_docentes()
    registros: Dict[str, Dict[str, Any]] = {}
    for _, fila in oferta.iterrows():
        docente_id = _texto(fila.get("docente_id"))
        if not docente_id:
            continue
        nombre = _texto(fila.get("docente_nombre")) or None
        if docente_id not in registros or (
            not registros[docente_id]["nombre"] and nombre
        ):
            registros[docente_id] = {"id_docente": docente_id, "nombre": nombre}
    return _df_tipado(
        [registros[k] for k in sorted(registros)],
        COLS_DOCENTES, [], ["id_docente", "nombre"],
    )


def _sedes_desde_oferta(oferta: Any) -> Any:
    if oferta.empty:
        return _df_vacio_sedes()
    registros: Dict[str, Dict[str, Any]] = {}
    for _, fila in oferta.iterrows():
        codigo = _texto(fila.get("sede_id"))
        if not codigo:
            continue
        nombre = _texto(fila.get("sede_nombre")) or SEDES_CANONICAS.get(codigo, codigo)
        registros.setdefault(codigo, {"codigo": codigo, "nombre": nombre})
    return _df_tipado(
        [registros[k] for k in sorted(registros)],
        COLS_SEDES, [], ["codigo", "nombre"],
    )


# ---------------------------------------------------------------------------
# Oferta: CSV histórico (compatibilidad con el backend actual)
# ---------------------------------------------------------------------------
def procesar_oferta_csv(ruta: Path, periodo_override: Optional[str] = None) -> OfertaData:
    """Procesa el CSV histórico (una fila por sesión) al modelo actual."""
    df = _leer_csv_df(ruta, "el CSV de oferta")
    _validar_columnas(df, COLS_CSV_OFERTA, f"CSV '{ruta.name}'")

    metricas: Dict[str, int] = {
        "filas_csv": int(len(df)),
        "filas_activas": int(len(df)),
        "filas_inactivas": 0,
        "nrc_sin_id": 0,
        "nrc_sin_codigo": 0,
        "codigos_largos": 0,
        "nrc_sin_nombre": 0,
        "nrc_sin_creditos": 0,
        "nrc_sin_docente": 0,
        "filas_sin_dia": 0,
        "sesiones_sin_horas": 0,
        "horas_invalidas": 0,
        "hora_inicio_mayor": 0,
        "nrc_sin_sesiones": 0,
        "conflictos_nrc": 0,
        "conflictos_curso": 0,
    }

    nrc_map: Dict[int, Dict[str, Any]] = {}
    sesiones: List[Dict[str, Any]] = []

    for _, fila in df.iterrows():
        nrc = _entero(fila.get("id_nrc"))
        if nrc is None:
            metricas["nrc_sin_id"] += 1
            continue
        codigo_curso = _codigo(fila.get("codigo_curso"))
        if not codigo_curso:
            metricas["nrc_sin_codigo"] += 1
            continue
        if len(codigo_curso) > MAX_CODIGO:
            metricas["codigos_largos"] += 1
            continue

        hora_inicio = _hora_hhmmss(fila.get("hora_inicio"))
        hora_fin = _hora_hhmmss(fila.get("hora_fin"))
        cupo = _entero(fila.get("cupo_max")) or 0
        inscritos = _entero(fila.get("inscritos")) or 0
        saldo = _entero(fila.get("saldo"))
        if saldo is None:
            saldo = cupo - inscritos
        codigo_sede = _codigo(fila.get("sede_id") or fila.get("sede"))
        nombre_sede = _texto(fila.get("sede_nombre") or fila.get("desc_sede"))
        docente_id = _texto(fila.get("docente_id")) or None
        docente_nombre = _texto(fila.get("nombre_docente")) or None

        atributos = {
            "id_nrc": nrc,
            "periodo": (_texto(periodo_override) if periodo_override
                        else _texto(fila.get("periodo"))) or None,
            "codigo_curso": codigo_curso,
            "nombre_curso": _texto(fila.get("nombre_curso")) or codigo_curso,
            "creditos": _entero(fila.get("creditos")) or 0,
            "jornada": _texto(fila.get("jornada")) or _jornada_desde_hora(hora_inicio),
            "modalidad": (_texto(fila.get("modalidad"))
                          or _modalidad_desde_metodo("", "", hora_inicio)),
            "cupo_max": cupo,
            "inscritos": inscritos,
            "saldo": saldo,
            "estado": _texto(fila.get("estado")) or "Activo",
            "sede_id": codigo_sede or None,
            "sede_nombre": (_sede_nombre(codigo_sede, nombre_sede)
                            if codigo_sede else (nombre_sede or None)),
            "docente_id": docente_id,
            "docente_nombre": docente_nombre,
            "programa": _texto(fila.get("programa")) or None,
            "semestre": _entero(fila.get("semestre")),
            "horas": _entero(fila.get("horas")),
        }

        if not _texto(fila.get("nombre_curso")):
            metricas["nrc_sin_nombre"] += 1
        if atributos["creditos"] == 0:
            metricas["nrc_sin_creditos"] += 1
        if not docente_id:
            metricas["nrc_sin_docente"] += 1

        existente = nrc_map.get(nrc)
        if existente is None:
            nrc_map[nrc] = atributos
        elif existente["codigo_curso"] != codigo_curso:
            metricas["conflictos_nrc"] += 1
            continue

        if not hora_inicio or not hora_fin:
            metricas["sesiones_sin_horas"] += 1
            continue
        if hora_inicio >= hora_fin:
            metricas["hora_inicio_mayor"] += 1

        dia = _entero(fila.get("dia"))
        if dia is None or not (1 <= dia <= 7):
            metricas["filas_sin_dia"] += 1
            continue
        numero_sesion = _entero(fila.get("numero_sesion"))

        sesiones.append({
            "id_nrc": nrc,
            "numero_sesion": numero_sesion,
            "dia": dia,
            "hora_inicio": hora_inicio,
            "hora_fin": hora_fin,
            "salon": _texto(fila.get("salon")) or None,
            "edificio": _texto(fila.get("edificio")) or None,
        })

    if not nrc_map:
        raise IngestaError(
            f"El CSV '{ruta}' no produjo NRC válidos. Revise las columnas "
            f"obligatorias: {COLS_CSV_OFERTA}"
        )

    # Numerar sesiones cuando el CSV no trae numero_sesion o lo repite.
    sesiones.sort(key=lambda s: (
        s["id_nrc"], s["dia"], s["hora_inicio"], s["hora_fin"],
        s["salon"] or "", s["edificio"] or "",
    ))
    contador: Dict[int, int] = {}
    for sesion in sesiones:
        contador[sesion["id_nrc"]] = contador.get(sesion["id_nrc"], 0) + 1
        if sesion["numero_sesion"] is None:
            sesion["numero_sesion"] = contador[sesion["id_nrc"]]

    for nrc in nrc_map:
        if contador.get(nrc, 0) == 0:
            metricas["nrc_sin_sesiones"] += 1

    oferta = _df_tipado(
        [nrc_map[nrc] for nrc in sorted(nrc_map)],
        COLS_OFERTA, COLS_OFERTA_ENTERAS, COLS_OFERTA_TEXTO,
    )
    horario = _df_tipado(
        sesiones, COLS_HORARIO, COLS_HORARIO_ENTERAS, COLS_HORARIO_TEXTO,
    )
    docentes = _docentes_desde_oferta(oferta)
    sedes = _sedes_desde_oferta(oferta)
    metricas["nrcs"] = int(len(oferta))
    metricas["cursos"] = int(oferta["codigo_curso"].nunique(dropna=True))
    metricas["sesiones"] = int(len(horario))
    return OfertaData(
        oferta=oferta, horario=horario, docentes=docentes, sedes=sedes,
        metricas=metricas, origen="csv", archivo=ruta,
    )


# ---------------------------------------------------------------------------
# Malla: PDF -> texto/OCR -> JSON de respaldo
# ---------------------------------------------------------------------------
@dataclass
class MallaData:
    malla: Any
    programa: str
    sede: str
    origen: str
    archivo: Optional[Path] = None
    nota: str = ""
    texto_pdf: str = ""


def _texto_pdf(ruta: Path) -> str:
    if pdfplumber is None:
        _aviso(
            "pdfplumber no está instalado; se omite la extracción de texto "
            "del PDF y se intentará el fallback JSON."
        )
        return ""
    try:
        with pdfplumber.open(str(ruta)) as pdf:
            return "\n".join((pagina.extract_text() or "") for pagina in pdf.pages)
    except Exception as exc:  # noqa: BLE001 - PDF corrupto o cifrado
        _aviso(f"pdfplumber no pudo extraer texto de '{ruta}': {exc}")
        return ""


def _parsear_malla_texto(texto: str) -> List[Dict[str, Any]]:
    """
    Parser best-effort de una malla con capa de texto.

    Reconoce líneas con semestre, código, nombre y dos enteros finales
    (créditos y horas). Si no logra al menos 5 cursos, devuelve [] para que el
    llamador use el fallback JSON. El PDF real es un escaneo, así que este
    camino normalmente no se ejecuta.
    """
    cursos: List[Dict[str, Any]] = []
    patron = re.compile(
        r"^\s*(?:SEM(?:ESTRE)?\s*)?(\d{1,2})\s*[\-\.\)]?\s+"
        r"([A-Z]{2,6}\s?[A-Z0-9]{2,8})\s+(.+?)\s+"
        r"(\d{1,2})\s+(\d{1,2})\s*$",
        re.IGNORECASE,
    )
    for linea in texto.splitlines():
        coincidencia = patron.match(linea.strip())
        if not coincidencia:
            continue
        semestre = _entero(coincidencia.group(1))
        codigo = _codigo(coincidencia.group(2))
        nombre = re.sub(r"\s+", " ", coincidencia.group(3)).strip(" -|")
        creditos = _entero(coincidencia.group(4))
        horas = _entero(coincidencia.group(5))
        if (semestre is None or not codigo or len(nombre) < 3
                or creditos is None or horas is None):
            continue
        cursos.append({
            "semestre": semestre,
            "codigo": codigo,
            "nombre": nombre,
            "creditos": creditos,
            "horas": horas,
            "prerrequisitos": [],
        })
    if len(cursos) < 5:
        return []
    return cursos


def _ocr_pdf(ruta: Path) -> str:
    """OCR opcional. Devuelve '' si falta pytesseract, Pillow o tesseract."""
    if pytesseract is None or Image is None:
        _aviso(
            "OCR no disponible: faltan pytesseract/Pillow. "
            "Instálelos con 'pip install pytesseract pillow pypdfium2'."
        )
        return ""
    if shutil.which("tesseract") is None:
        _aviso(
            "OCR no disponible: el binario 'tesseract' no está en PATH. "
            "En Fedora: sudo dnf install tesseract tesseract-langpack-spa"
        )
        return ""
    if pdfplumber is None:
        _aviso("OCR no disponible: pdfplumber no está instalado para rasterizar.")
        return ""

    textos: List[str] = []
    try:
        with pdfplumber.open(str(ruta)) as pdf:
            for numero, pagina in enumerate(pdf.pages, start=1):
                imagen = pagina.to_image(resolution=300).original
                try:
                    textos.append(pytesseract.image_to_string(imagen, lang="spa"))
                except Exception:  # noqa: BLE001 - idioma 'spa' ausente
                    textos.append(pytesseract.image_to_string(imagen))
                _info(f"OCR página {numero}: {len(textos[-1])} caracteres")
    except Exception as exc:  # noqa: BLE001
        _aviso(f"El OCR del PDF falló: {exc}")
        return ""
    return "\n".join(textos)


def _cargar_malla_json(ruta: Path) -> Tuple[Any, str, str, str]:
    """Carga y valida el fallback JSON. Devuelve (df, programa, sede, nota)."""
    if not ruta.is_file():
        raise IngestaError(
            "El PDF no tiene capa de texto utilizable y no existe el fallback "
            f"JSON '{ruta}'. Cree ese archivo o instale OCR "
            "(pytesseract + binario tesseract). No se inventarán datos."
        )
    try:
        with ruta.open("r", encoding="utf-8") as manejador:
            datos = json.load(manejador)
    except Exception as exc:  # noqa: BLE001
        raise IngestaError(
            f"No se pudo leer el fallback JSON '{ruta}': {exc}"
        ) from exc

    if not isinstance(datos, dict) or not isinstance(datos.get("cursos"), list):
        raise IngestaError(
            f"El fallback JSON '{ruta}' debe ser un objeto con la clave "
            "'cursos' (lista)."
        )

    programa = _texto(datos.get("programa"))
    sede = _texto(datos.get("sede"))
    nota = _texto(datos.get("nota"))
    registros: List[Dict[str, Any]] = []
    vistos = set()
    for crudo in datos["cursos"]:
        if not isinstance(crudo, dict):
            continue
        codigo = _codigo(crudo.get("codigo"))
        if not codigo or codigo in vistos:
            continue
        vistos.add(codigo)
        registros.append({
            "programa": programa or None,
            "sede": sede or None,
            "semestre": _entero(crudo.get("semestre")),
            "codigo": codigo,
            "nombre": _texto(crudo.get("nombre")) or codigo,
            "creditos": _entero(crudo.get("creditos")) or 0,
            "horas": _entero(crudo.get("horas")) or 0,
            "prerrequisitos": _json_lista(
                _normalizar_prerrequisitos(crudo.get("prerrequisitos"))
            ),
        })

    if not registros:
        raise IngestaError(
            f"El fallback JSON '{ruta}' no contiene cursos válidos."
        )

    malla = _df_tipado(
        registros, COLS_MALLA, COLS_MALLA_ENTERAS, COLS_MALLA_TEXTO,
    )
    return malla, programa, sede, nota


def procesar_malla_pdf(
    ruta: Path,
    malla_json: Path = MALLA_JSON_DEFAULT,
) -> MallaData:
    """Intenta pdfplumber, luego OCR y por último el JSON de respaldo."""
    _requerir_pandas("la malla curricular")
    texto = _texto_pdf(ruta)
    origen = "pdfplumber"
    cursos: List[Dict[str, Any]] = []

    if texto.strip():
        _info(f"pdfplumber extrajo {len(texto):,} caracteres del PDF.")
        cursos = _parsear_malla_texto(texto)
        if not cursos:
            _aviso(
                "El PDF tiene texto pero no se pudo estructurar como malla "
                "(se esperaban líneas semestre/código/nombre/créditos/horas)."
            )
    else:
        _aviso(
            "El PDF no tiene capa de texto (escaneo). Se intentará OCR "
            "opcional; si no está disponible, se usará el fallback JSON."
        )

    if not cursos:
        texto_ocr = _ocr_pdf(ruta)
        if texto_ocr.strip():
            cursos = _parsear_malla_texto(texto_ocr)
            if cursos:
                origen = "ocr"
            else:
                _aviso("El OCR produjo texto pero no se pudo estructurar la malla.")

    programa = ""
    sede = ""
    nota = ""

    if cursos:
        registros = []
        for curso in cursos:
            registros.append({
                "programa": None,
                "sede": None,
                "semestre": curso["semestre"],
                "codigo": curso["codigo"],
                "nombre": curso["nombre"],
                "creditos": curso["creditos"],
                "horas": curso["horas"],
                "prerrequisitos": _json_lista(curso["prerrequisitos"]),
            })
        malla = _df_tipado(
            registros, COLS_MALLA, COLS_MALLA_ENTERAS, COLS_MALLA_TEXTO,
        )
    else:
        _info(f"Usando fallback JSON: {malla_json}")
        malla, programa, sede, nota = _cargar_malla_json(malla_json)
        origen = "json_fallback"
        if nota:
            _info(f"Nota del fallback: {nota}")

    if malla.empty:
        raise IngestaError(
            f"No se pudo obtener la malla desde '{ruta}' ni desde '{malla_json}'."
        )

    return MallaData(
        malla=malla, programa=programa, sede=sede, origen=origen,
        archivo=ruta, nota=nota, texto_pdf=texto,
    )


# ---------------------------------------------------------------------------
# Enriquecimiento y capas Gold
# ---------------------------------------------------------------------------
def _enriquecer_oferta_con_malla(oferta: Any, malla: Any) -> int:
    """Completa programa/semestre/horas/créditos de la oferta usando la malla."""
    if oferta.empty or malla.empty:
        return 0
    base = malla.drop_duplicates(subset=["codigo"], keep="first").set_index("codigo")
    codigos = set(base.index)
    encontrados = int(oferta["codigo_curso"].isin(codigos).sum())

    mapa_programa = base["programa"]
    mapa_semestre = base["semestre"]
    mapa_horas = base["horas"]
    mapa_creditos = base["creditos"]

    oferta["programa"] = oferta["programa"].where(
        oferta["programa"].notna(),
        oferta["codigo_curso"].map(mapa_programa),
    )
    oferta["semestre"] = oferta["semestre"].where(
        oferta["semestre"].notna(),
        oferta["codigo_curso"].map(mapa_semestre).astype("Int64"),
    )
    oferta["horas"] = oferta["horas"].where(
        oferta["horas"].notna() & (oferta["horas"] > 0),
        oferta["codigo_curso"].map(mapa_horas).astype("Int64"),
    )
    oferta["creditos"] = oferta["creditos"].where(
        oferta["creditos"] > 0,
        oferta["codigo_curso"].map(mapa_creditos).astype("Int64"),
    )
    oferta["creditos"] = oferta["creditos"].fillna(0).astype("Int64")
    return encontrados


def _gold_oferta(oferta: Any) -> Any:
    if oferta.empty:
        return _df_vacio_oferta_resumen()

    agrupado = (
        oferta.groupby(
            ["codigo_curso", "sede_id", "jornada"], dropna=False, sort=True
        )
        .agg(
            nombre_curso=("nombre_curso", "first"),
            sede_nombre=("sede_nombre", "first"),
            nrcs=("id_nrc", "nunique"),
            cupo_total=("cupo_max", "sum"),
            inscritos_total=("inscritos", "sum"),
            saldo_total=("saldo", "sum"),
            programa=("programa", "first"),
            semestre=("semestre", "first"),
        )
        .reset_index()
    )
    cupo = agrupado["cupo_total"].astype("float64")
    inscritos = agrupado["inscritos_total"].astype("float64")
    agrupado["ocupacion"] = (inscritos / cupo.where(cupo > 0)).fillna(0.0).round(4)

    for columna in ("nrcs", "cupo_total", "inscritos_total", "saldo_total"):
        agrupado[columna] = pd.to_numeric(
            agrupado[columna], errors="coerce"
        ).astype("Int64")
    agrupado["semestre"] = pd.to_numeric(
        agrupado["semestre"], errors="coerce"
    ).astype("Int64")
    for columna in ("codigo_curso", "nombre_curso", "sede_id", "sede_nombre",
                    "jornada", "programa"):
        agrupado[columna] = agrupado[columna].astype("string")
    agrupado["ocupacion"] = agrupado["ocupacion"].astype("float64")
    return agrupado[COLS_OFERTA_RESUMEN]


def _gold_malla(malla: Any) -> Any:
    if malla.empty:
        return _df_vacio_malla_resumen()

    def _lista_codigos(serie: Any) -> str:
        return _json_lista(sorted({str(x) for x in serie.dropna()}))

    agrupado = (
        malla.groupby(
            ["programa", "sede", "semestre"], dropna=False, sort=True
        )
        .agg(
            num_cursos=("codigo", "nunique"),
            creditos_total=("creditos", "sum"),
            horas_total=("horas", "sum"),
            cursos=("codigo", _lista_codigos),
        )
        .reset_index()
    )
    for columna in ("semestre", "num_cursos", "creditos_total", "horas_total"):
        agrupado[columna] = pd.to_numeric(
            agrupado[columna], errors="coerce"
        ).astype("Int64")
    for columna in ("programa", "sede", "cursos"):
        agrupado[columna] = agrupado[columna].astype("string")
    return agrupado[COLS_MALLA_RESUMEN]


# ---------------------------------------------------------------------------
# Escritura Parquet
# ---------------------------------------------------------------------------
def _guardar_parquet(df: Any, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(ruta, index=False, engine="pyarrow")
    except Exception as exc:  # noqa: BLE001
        raise IngestaError(
            f"No se pudo escribir '{ruta}': {exc}. Verifique pyarrow."
        ) from exc
    _log(f"   {ruta.name:<24} OK ({len(df):,} filas)")


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------
DDL_POSTGRES: Tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS curso (
        codigo_alfanumerico VARCHAR(10) PRIMARY KEY,
        nombre              TEXT NOT NULL,
        creditos            INTEGER NOT NULL,
        semestre            INTEGER,
        programa            TEXT,
        horas               INTEGER
    )
    """,
    "ALTER TABLE curso ADD COLUMN IF NOT EXISTS semestre INTEGER",
    "ALTER TABLE curso ADD COLUMN IF NOT EXISTS programa TEXT",
    "ALTER TABLE curso ADD COLUMN IF NOT EXISTS horas INTEGER",
    """
    CREATE TABLE IF NOT EXISTS docente (
        id_docente TEXT PRIMARY KEY,
        nombre     TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sede (
        codigo VARCHAR(10) PRIMARY KEY,
        nombre TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS nrc (
        id_nrc      INTEGER PRIMARY KEY,
        curso_id    VARCHAR(10) NOT NULL REFERENCES curso (codigo_alfanumerico),
        periodo     TEXT,
        docente_id  TEXT REFERENCES docente (id_docente),
        sede_id     VARCHAR(10) REFERENCES sede (codigo),
        jornada     TEXT NOT NULL,
        modalidad   TEXT NOT NULL,
        cupo_max    INTEGER NOT NULL,
        inscritos   INTEGER DEFAULT 0,
        saldo       INTEGER DEFAULT 0,
        estado      TEXT DEFAULT 'Activo'
    )
    """,
    "ALTER TABLE nrc ADD COLUMN IF NOT EXISTS periodo TEXT",
    "ALTER TABLE nrc ADD COLUMN IF NOT EXISTS docente_id TEXT",
    "ALTER TABLE nrc ADD COLUMN IF NOT EXISTS sede_id VARCHAR(10)",
    "ALTER TABLE nrc ADD COLUMN IF NOT EXISTS inscritos INTEGER DEFAULT 0",
    "ALTER TABLE nrc ADD COLUMN IF NOT EXISTS saldo INTEGER DEFAULT 0",
    "ALTER TABLE nrc ADD COLUMN IF NOT EXISTS estado TEXT DEFAULT 'Activo'",
    """
    CREATE TABLE IF NOT EXISTS horario (
        id            SERIAL PRIMARY KEY,
        nrc_id        INTEGER NOT NULL REFERENCES nrc (id_nrc),
        numero_sesion INTEGER NOT NULL,
        dia           INTEGER NOT NULL,
        hora_inicio   TIME NOT NULL,
        hora_fin      TIME NOT NULL,
        salon         TEXT,
        edificio      TEXT
    )
    """,
    "ALTER TABLE horario ADD COLUMN IF NOT EXISTS salon TEXT",
    "ALTER TABLE horario ADD COLUMN IF NOT EXISTS edificio TEXT",
    "CREATE INDEX IF NOT EXISTS idx_horario_nrc ON horario (nrc_id)",
)

SQL_UPSERT_CURSO = """
    INSERT INTO curso (
        codigo_alfanumerico, nombre, creditos, semestre, programa, horas
    ) VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (codigo_alfanumerico) DO UPDATE SET
        nombre   = EXCLUDED.nombre,
        creditos = COALESCE(NULLIF(EXCLUDED.creditos, 0), curso.creditos),
        semestre = COALESCE(EXCLUDED.semestre, curso.semestre),
        programa = COALESCE(EXCLUDED.programa, curso.programa),
        horas    = COALESCE(EXCLUDED.horas, curso.horas)
"""

SQL_UPSERT_DOCENTE = """
    INSERT INTO docente (id_docente, nombre)
    VALUES (%s, %s)
    ON CONFLICT (id_docente) DO UPDATE SET
        nombre = COALESCE(NULLIF(EXCLUDED.nombre, ''), docente.nombre)
"""

SQL_UPSERT_SEDE = """
    INSERT INTO sede (codigo, nombre)
    VALUES (%s, %s)
    ON CONFLICT (codigo) DO UPDATE SET
        nombre = COALESCE(NULLIF(EXCLUDED.nombre, ''), sede.nombre)
"""

SQL_UPSERT_NRC = """
    INSERT INTO nrc (
        id_nrc, curso_id, periodo, docente_id, sede_id, jornada, modalidad,
        cupo_max, inscritos, saldo, estado
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id_nrc) DO UPDATE SET
        curso_id   = EXCLUDED.curso_id,
        periodo    = EXCLUDED.periodo,
        docente_id = EXCLUDED.docente_id,
        sede_id    = EXCLUDED.sede_id,
        jornada    = EXCLUDED.jornada,
        modalidad  = EXCLUDED.modalidad,
        cupo_max   = EXCLUDED.cupo_max,
        inscritos  = EXCLUDED.inscritos,
        saldo      = EXCLUDED.saldo,
        estado     = EXCLUDED.estado
"""

SQL_INSERT_HORARIO = """
    INSERT INTO horario (
        nrc_id, numero_sesion, dia, hora_inicio, hora_fin, salon, edificio
    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
"""


def _conectar_postgres() -> Any:
    _requerir_psycopg2()
    try:
        return psycopg2.connect(**DB_CONFIG, connect_timeout=DB_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        raise IngestaError(
            "No se pudo conectar a PostgreSQL con "
            f"{DB_CONFIG['user']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/"
            f"{DB_CONFIG['dbname']}: {exc}. Revise DB_HOST/DB_PORT/DB_NAME/"
            "DB_USER/DB_PASSWORD o use --sin-bd."
        ) from exc


def _valor_db(valor: Any) -> Any:
    """Convierte valores pandas/numpy a tipos que psycopg2 entiende."""
    if _es_nulo(valor):
        return None
    if isinstance(valor, str):
        return valor
    if hasattr(valor, "item") and not isinstance(valor, (bytes, bytearray)):
        try:
            valor = valor.item()
        except (TypeError, ValueError):
            pass
    if isinstance(valor, float) and valor.is_integer():
        return int(valor)
    return valor


def _tuplas(df: Any, columnas: Sequence[str]) -> List[Tuple[Any, ...]]:
    if df.empty:
        return []
    return [
        tuple(_valor_db(valor) for valor in fila)
        for fila in df[list(columnas)].itertuples(index=False, name=None)
    ]


def cargar_postgres(datos: OfertaData) -> Dict[str, int]:
    """Carga idempotente de curso/docente/sede/nrc y reemplazo de horario."""
    _requerir_psycopg2()
    conn = _conectar_postgres()
    resultado: Dict[str, int] = {
        "cursos": 0, "docentes": 0, "sedes": 0, "nrcs": 0,
        "horarios": 0, "horarios_borrados": 0,
    }
    try:
        with conn.cursor() as cur:
            for sentencia in DDL_POSTGRES:
                cur.execute(sentencia)

            tuplas_curso = _tuplas(
                datos.oferta.drop_duplicates(subset=["codigo_curso"]),
                ["codigo_curso", "nombre_curso", "creditos", "semestre",
                 "programa", "horas"],
            )
            if tuplas_curso:
                cur.executemany(SQL_UPSERT_CURSO, tuplas_curso)
                resultado["cursos"] = len(tuplas_curso)

            tuplas_docente = _tuplas(datos.docentes, ["id_docente", "nombre"])
            if tuplas_docente:
                cur.executemany(SQL_UPSERT_DOCENTE, tuplas_docente)
                resultado["docentes"] = len(tuplas_docente)

            tuplas_sede = _tuplas(datos.sedes, ["codigo", "nombre"])
            if tuplas_sede:
                cur.executemany(SQL_UPSERT_SEDE, tuplas_sede)
                resultado["sedes"] = len(tuplas_sede)

            tuplas_nrc = _tuplas(
                datos.oferta,
                ["id_nrc", "codigo_curso", "periodo", "docente_id", "sede_id",
                 "jornada", "modalidad", "cupo_max", "inscritos", "saldo",
                 "estado"],
            )
            if tuplas_nrc:
                cur.executemany(SQL_UPSERT_NRC, tuplas_nrc)
                resultado["nrcs"] = len(tuplas_nrc)

            # Reemplazo de horario SOLO para los NRC de esta carga. No se toca
            # inscripcion ni el historial de estudiantes.
            if not datos.oferta.empty:
                nrcs = [int(x) for x in datos.oferta["id_nrc"].dropna().tolist()]
                if nrcs:
                    cur.execute(
                        "DELETE FROM horario WHERE nrc_id = ANY(%s)", (nrcs,)
                    )
                    resultado["horarios_borrados"] = max(cur.rowcount, 0)

            tuplas_horario = _tuplas(
                datos.horario,
                ["id_nrc", "numero_sesion", "dia", "hora_inicio", "hora_fin",
                 "salon", "edificio"],
            )
            if tuplas_horario:
                cur.executemany(SQL_INSERT_HORARIO, tuplas_horario)
                resultado["horarios"] = len(tuplas_horario)

        conn.commit()
    except IngestaError:
        conn.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise IngestaError(
            f"Error cargando PostgreSQL (se hizo ROLLBACK, no se borró "
            f"historial/inscripciones): {exc}"
        ) from exc
    finally:
        conn.close()

    _log(
        "   PostgreSQL: "
        f"{resultado['cursos']:,} cursos, {resultado['docentes']:,} docentes, "
        f"{resultado['sedes']:,} sedes, {resultado['nrcs']:,} NRC, "
        f"{resultado['horarios']:,} sesiones "
        f"({resultado['horarios_borrados']:,} sesiones previas reemplazadas)."
    )
    return resultado


# ---------------------------------------------------------------------------
# MongoDB
# ---------------------------------------------------------------------------
def _mongo_cliente() -> Any:
    _requerir_pymongo()
    try:
        cliente = MongoClient(
            MONGO_URI, serverSelectionTimeoutMS=MONGO_TIMEOUT_MS,
            tz_aware=True,
        )
        cliente.admin.command("ping")
        return cliente
    except Exception as exc:  # noqa: BLE001
        raise IngestaError(
            f"No se pudo conectar a MongoDB en '{MONGO_URI}': {exc}. "
            "Revise MONGO_URI/MONGO_DB o use --sin-bd."
        ) from exc


def _crear_indices_mongo(db: Any) -> None:
    malla_col = db["malla_curricular"]
    reglas_col = db["reglas_negocio"]

    try:
        info = malla_col.index_information()
        if "uq_malla_programa_codigo" in info and not info[
            "uq_malla_programa_codigo"
        ].get("unique"):
            malla_col.drop_index("uq_malla_programa_codigo")
        malla_col.create_index(
            [("programa", 1), ("codigo", 1)],
            unique=True, name="uq_malla_programa_codigo",
        )
    except PyMongoError as exc:
        raise IngestaError(
            "No se pudo crear el índice único "
            "{programa:1, codigo:1} en malla_curricular: "
            f"{exc}. Si hay duplicados previos, límpielos y reintente."
        ) from exc

    try:
        info = reglas_col.index_information()
        if "uq_reglas_materia" in info and not info[
            "uq_reglas_materia"
        ].get("unique"):
            reglas_col.drop_index("uq_reglas_materia")
        reglas_col.create_index(
            "materia", unique=True, name="uq_reglas_materia"
        )
    except PyMongoError as exc:
        raise IngestaError(
            "No se pudo crear el índice único {materia:1} en reglas_negocio: "
            f"{exc}. Si hay duplicados previos, límpielos y reintente."
        ) from exc


def cargar_mongo(malla: Any) -> Dict[str, int]:
    """Upsert de malla_curricular y reglas_negocio (sin pisar reglas del Admin)."""
    _requerir_pymongo()
    cliente = _mongo_cliente()
    db = cliente[MONGO_DB]
    resultado: Dict[str, int] = {
        "malla_insertados": 0,
        "malla_actualizados": 0,
        "reglas_insertados": 0,
        "reglas_existentes": 0,
    }
    try:
        _crear_indices_mongo(db)
        malla_col = db["malla_curricular"]
        reglas_col = db["reglas_negocio"]
        ahora = datetime.now(timezone.utc)

        operaciones_malla = []
        operaciones_reglas = []
        for _, fila in malla.iterrows():
            programa = _texto(fila.get("programa")) or None
            codigo = _codigo(fila.get("codigo"))
            if not codigo:
                continue
            prerequisitos = _normalizar_prerrequisitos(fila.get("prerrequisitos"))
            documento = {
                "programa": programa,
                "sede": _texto(fila.get("sede")) or None,
                "semestre": _valor_db(fila.get("semestre")),
                "codigo": codigo,
                "nombre": _texto(fila.get("nombre")) or codigo,
                "creditos": _valor_db(fila.get("creditos")) or 0,
                "horas": _valor_db(fila.get("horas")) or 0,
                "prerrequisitos": prerequisitos,
                "actualizado_en": ahora,
            }
            operaciones_malla.append(UpdateOne(
                {"programa": programa, "codigo": codigo},
                {
                    "$set": documento,
                    "$setOnInsert": {"creado_en": ahora},
                },
                upsert=True,
            ))
            operaciones_reglas.append(UpdateOne(
                {"materia": codigo},
                {
                    "$setOnInsert": {
                        "materia": codigo,
                        "prerrequisitos": prerequisitos,
                        "nombre": documento["nombre"],
                        "creditos": documento["creditos"],
                        "semestre": documento["semestre"],
                        "programa": programa,
                        "creado_en": ahora,
                    }
                },
                upsert=True,
            ))

        if operaciones_malla:
            lote = malla_col.bulk_write(operaciones_malla, ordered=False)
            resultado["malla_insertados"] = int(lote.upserted_count)
            resultado["malla_actualizados"] = int(lote.matched_count)
        if operaciones_reglas:
            lote = reglas_col.bulk_write(operaciones_reglas, ordered=False)
            resultado["reglas_insertados"] = int(lote.upserted_count)
            resultado["reglas_existentes"] = int(lote.matched_count)
    except IngestaError:
        raise
    except PyMongoError as exc:
        raise IngestaError(
            f"Error cargando MongoDB '{MONGO_DB}': {exc}"
        ) from exc
    finally:
        cliente.close()

    _log(
        "   MongoDB: "
        f"malla_curricular {resultado['malla_insertados']:,} nuevos / "
        f"{resultado['malla_actualizados']:,} actualizados; "
        f"reglas_negocio {resultado['reglas_insertados']:,} nuevas / "
        f"{resultado['reglas_existentes']:,} conservadas (Admin)."
    )
    return resultado


# ---------------------------------------------------------------------------
# Compatibilidad: matriz histórica de estudiantes
# ---------------------------------------------------------------------------
DDL_MATRIZ: Tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS estudiante (
        id      SERIAL PRIMARY KEY,
        nombre  TEXT NOT NULL,
        plan_id INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS historial (
        estudiante_id INTEGER NOT NULL,
        codigo_curso  VARCHAR(10) NOT NULL,
        PRIMARY KEY (estudiante_id, codigo_curso)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS estudiante_perfil (
        estudiante_id INTEGER PRIMARY KEY,
        carrera       TEXT,
        semestre      INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS matriz_por_ver (
        estudiante_id INTEGER NOT NULL,
        codigo_curso  VARCHAR(10) NOT NULL,
        PRIMARY KEY (estudiante_id, codigo_curso)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_historial_curso ON historial (codigo_curso)",
)


def _normalizar_estado(valor: Any) -> Optional[str]:
    clave = _normalizar_columna(_texto(valor))
    if clave in ESTADOS_APROBADA:
        return "aprobada"
    if clave in ESTADOS_POR_VER:
        return "por_ver"
    return None


def _primero_no_nulo(serie: Any) -> Any:
    validos = serie.dropna()
    return validos.iloc[0] if len(validos) else None


def procesar_matriz_csv(ruta: Path) -> Dict[str, Any]:
    """Modo legado --matriz/--solo-matriz: historial, por ver y perfil."""
    df = _leer_csv_df(ruta, "la matriz de estudiantes")
    _validar_columnas(df, COLS_CSV_MATRIZ, f"CSV '{ruta.name}'")

    enriquecida = bool(set(df.columns) & {"estado", "carrera", "semestre"})
    df["estudiante_id"] = df["estudiante_id"].map(_entero)
    df["codigo_curso"] = df["codigo_curso"].map(_codigo)
    df = df.dropna(subset=["estudiante_id", "codigo_curso"]).reset_index(drop=True)
    df = df[df["estudiante_id"] > 0].reset_index(drop=True)
    df = df[df["codigo_curso"].str.len() <= MAX_CODIGO].reset_index(drop=True)

    if "estado" in df.columns:
        df["estado"] = df["estado"].map(_normalizar_estado)
        df = df.dropna(subset=["estado"]).reset_index(drop=True)
    else:
        df["estado"] = "aprobada"

    if "carrera" in df.columns:
        df["carrera"] = df["carrera"].map(lambda v: _texto(v) or None)
    else:
        df["carrera"] = None
    if "semestre" in df.columns:
        df["semestre"] = df["semestre"].map(_entero)
    else:
        df["semestre"] = None

    if not df.empty:
        prioridad = {"aprobada": 0, "por_ver": 1}
        df["_prioridad"] = df["estado"].map(prioridad)
        df = (
            df.sort_values(
                ["estudiante_id", "codigo_curso", "_prioridad"], kind="stable"
            )
            .drop_duplicates(subset=["estudiante_id", "codigo_curso"], keep="first")
            .drop(columns="_prioridad")
            .reset_index(drop=True)
        )

    historial = _df_tipado(
        df.loc[df["estado"] == "aprobada", ["estudiante_id", "codigo_curso"]]
        .to_dict("records"),
        ["estudiante_id", "codigo_curso"], ["estudiante_id"], ["codigo_curso"],
    )
    por_ver = _df_tipado(
        df.loc[df["estado"] == "por_ver", ["estudiante_id", "codigo_curso"]]
        .to_dict("records"),
        ["estudiante_id", "codigo_curso"], ["estudiante_id"], ["codigo_curso"],
    )
    if df.empty:
        perfil = _df_tipado(
            [], ["estudiante_id", "carrera", "semestre"],
            ["estudiante_id", "semestre"], ["carrera"],
        )
        estudiantes: List[int] = []
    else:
        perfil = (
            df.groupby("estudiante_id", as_index=False)
            .agg(
                carrera=("carrera", _primero_no_nulo),
                semestre=("semestre", _primero_no_nulo),
            )
            .reset_index(drop=True)
        )
        perfil = _df_tipado(
            perfil.to_dict("records"), ["estudiante_id", "carrera", "semestre"],
            ["estudiante_id", "semestre"], ["carrera"],
        )
        estudiantes = sorted(int(x) for x in df["estudiante_id"].unique())

    return {
        "historial": historial,
        "matriz_por_ver": por_ver,
        "perfil": perfil,
        "estudiantes": estudiantes,
        "formato": "enriquecida" if enriquecida else "antigua",
        "aprobadas": int(len(historial)),
        "por_ver_count": int(len(por_ver)),
    }


def cargar_postgres_matriz(datos: Dict[str, Any]) -> Dict[str, int]:
    _requerir_psycopg2()
    conn = _conectar_postgres()
    resultado = {
        "estudiantes": 0, "aprobadas": 0, "por_ver": 0,
    }
    try:
        with conn.cursor() as cur:
            for sentencia in DDL_MATRIZ:
                cur.execute(sentencia)
            for estudiante_id in datos["estudiantes"]:
                cur.execute(
                    """
                    INSERT INTO estudiante (id, nombre, plan_id)
                    VALUES (%s, %s, 0)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (estudiante_id, f"Estudiante {estudiante_id}"),
                )
                resultado["estudiantes"] += 1

            tuplas_perfil = _tuplas(
                datos["perfil"], ["estudiante_id", "carrera", "semestre"]
            )
            if tuplas_perfil:
                cur.executemany(
                    """
                    INSERT INTO estudiante_perfil
                        (estudiante_id, carrera, semestre)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (estudiante_id) DO UPDATE SET
                        carrera = COALESCE(EXCLUDED.carrera,
                                           estudiante_perfil.carrera),
                        semestre = COALESCE(EXCLUDED.semestre,
                                            estudiante_perfil.semestre)
                    """,
                    tuplas_perfil,
                )

            ids = list(datos["estudiantes"])
            if ids:
                cur.execute(
                    "DELETE FROM historial WHERE estudiante_id = ANY(%s)", (ids,)
                )
                cur.execute(
                    "DELETE FROM matriz_por_ver WHERE estudiante_id = ANY(%s)",
                    (ids,),
                )

            tuplas_historial = _tuplas(
                datos["historial"], ["estudiante_id", "codigo_curso"]
            )
            if tuplas_historial:
                cur.executemany(
                    """
                    INSERT INTO historial (estudiante_id, codigo_curso)
                    VALUES (%s, %s) ON CONFLICT DO NOTHING
                    """,
                    tuplas_historial,
                )
                resultado["aprobadas"] = len(tuplas_historial)

            tuplas_por_ver = _tuplas(
                datos["matriz_por_ver"], ["estudiante_id", "codigo_curso"]
            )
            if tuplas_por_ver:
                cur.executemany(
                    """
                    INSERT INTO matriz_por_ver (estudiante_id, codigo_curso)
                    VALUES (%s, %s) ON CONFLICT DO NOTHING
                    """,
                    tuplas_por_ver,
                )
                resultado["por_ver"] = len(tuplas_por_ver)
        conn.commit()
    except IngestaError:
        conn.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise IngestaError(
            f"Error cargando la matriz en PostgreSQL (ROLLBACK): {exc}"
        ) from exc
    finally:
        conn.close()
    _log(
        f"   PostgreSQL matriz: {resultado['estudiantes']:,} estudiantes, "
        f"{resultado['aprobadas']:,} aprobadas, "
        f"{resultado['por_ver']:,} por ver."
    )
    return resultado


# ---------------------------------------------------------------------------
# Resolución de entradas
# ---------------------------------------------------------------------------
def _buscar_excel_default() -> Optional[Path]:
    if EXCEL_DEFAULT.is_file():
        return EXCEL_DEFAULT
    if not ENTRADA.is_dir():
        return None
    candidatos = sorted(
        p for p in ENTRADA.glob("*.xlsx")
        if p.is_file() and not p.name.startswith("~$")
    )
    return candidatos[0] if candidatos else None


def _buscar_pdf_default() -> Optional[Path]:
    if PDF_DEFAULT.is_file():
        return PDF_DEFAULT
    if not ENTRADA.is_dir():
        return None
    candidatos = sorted(p for p in ENTRADA.glob("*.pdf") if p.is_file())
    return candidatos[0] if candidatos else None


def _resolver_fuentes(args: argparse.Namespace) -> Tuple[Optional[Path], Optional[Path], bool]:
    """
    Devuelve (excel, pdf, es_modo_matriz) y valida combinaciones de flags.
    """
    if args.matriz or args.solo_matriz:
        if args.excel or args.oferta or args.pdf:
            raise IngestaError(
                "--matriz/--solo-matriz no se puede combinar con --excel, "
                "--oferta ni --pdf."
            )
        matriz = Path(args.matriz).expanduser() if args.matriz else MATRIZ_DEFAULT
        if not matriz.is_file():
            raise IngestaError(
                f"Modo matriz: no se encontró el CSV '{matriz}'. "
                "Pase --matriz <ruta> o deje el archivo en datalake/."
            )
        return None, None, True

    if args.oferta and args.excel:
        raise IngestaError("Use --excel o --oferta, no ambos.")

    excel_arg = args.excel or args.oferta
    pdf_arg = args.pdf

    if args.solo_oferta:
        if pdf_arg:
            raise IngestaError(
                "--solo-oferta procesa solo el Excel/CSV; no acepta --pdf."
            )
        ruta = Path(excel_arg).expanduser() if excel_arg else _buscar_excel_default()
        if ruta is None or not ruta.is_file():
            raise IngestaError(
                "Modo --solo-oferta: no se encontró el Excel de oferta. "
                f"Pase --excel <ruta> (buscado por defecto: {EXCEL_DEFAULT})."
            )
        return ruta, None, False

    if args.solo_malla:
        if excel_arg:
            raise IngestaError(
                "--solo-malla procesa solo el PDF; no acepta --excel/--oferta."
            )
        ruta = Path(pdf_arg).expanduser() if pdf_arg else _buscar_pdf_default()
        if ruta is None or not ruta.is_file():
            raise IngestaError(
                "Modo --solo-malla: no se encontró el PDF de malla. "
                f"Pase --pdf <ruta> (buscado por defecto: {PDF_DEFAULT})."
            )
        return None, ruta, False

    # Sin --solo-*: si se indicó una fuente explícita, se procesa solo esa;
    # si no se indicó ninguna, se buscan ambas en datalake/entrada/.
    excel: Optional[Path]
    pdf: Optional[Path]
    if excel_arg:
        excel = Path(excel_arg).expanduser()
        if not excel.is_file():
            raise IngestaError(f"No se encontró el Excel de oferta: {excel}")
    else:
        excel = _buscar_excel_default() if not pdf_arg else None

    if pdf_arg:
        pdf = Path(pdf_arg).expanduser()
        if not pdf.is_file():
            raise IngestaError(f"No se encontró el PDF de malla: {pdf}")
    else:
        pdf = _buscar_pdf_default() if not excel_arg else None

    if not excel_arg and not pdf_arg:
        if excel is None and pdf is None:
            raise IngestaError(
                "No se encontraron archivos en datalake/entrada/. Deje allí "
                f"'{EXCEL_DEFAULT.name}' y/o '{PDF_DEFAULT.name}', o pase "
                "--excel/--pdf explícitos."
            )
        if excel is None:
            _aviso("No se encontró Excel de oferta; se procesará solo la malla.")
        if pdf is None:
            _aviso("No se encontró PDF de malla; se procesará solo la oferta.")

    if excel is None and pdf is None:
        raise IngestaError("No hay archivos para procesar.")
    return excel, pdf, False


# ---------------------------------------------------------------------------
# Bronze
# ---------------------------------------------------------------------------
def _copiar_bronze(origen: Path, prefijo: str, run_id: str) -> Path:
    origen = Path(origen)
    if not origen.is_file():
        raise IngestaError(f"No existe el archivo de entrada: {origen}")
    BRONZE.mkdir(parents=True, exist_ok=True)
    destino = BRONZE / f"{prefijo}_{run_id}{origen.suffix.lower()}"
    contador = 1
    while destino.exists():
        destino = BRONZE / f"{prefijo}_{run_id}_{contador}{origen.suffix.lower()}"
        contador += 1
    try:
        shutil.copy2(origen, destino)
    except Exception as exc:  # noqa: BLE001
        raise IngestaError(
            f"No se pudo copiar '{origen}' a Bronze: {exc}"
        ) from exc
    return destino


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Ingesta Data Lake (Medallón) para Inscripción de Materias: "
            "Excel/PDF -> Bronze/Silver/Gold -> PostgreSQL/MongoDB."
        ),
        epilog=(
            "Ejemplos:\n"
            "  %(prog)s --excel datalake/entrada/chorarios_26082026.xlsx "
            "--solo-oferta\n"
            "  %(prog)s --pdf datalake/entrada/ruta_nueva_isum_v2.pdf "
            "--solo-malla\n"
            "  %(prog)s --sin-bd\n"
            "  %(prog)s\n"
            "\n"
            "El modo sin flags procesa lo que exista en datalake/entrada/.\n"
            "Los modos --solo-oferta/--solo-malla son mutuamente excluyentes."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--excel",
        metavar="RUTA",
        help="Excel de oferta (.xlsx). Con --solo-oferta procesa solo oferta.",
    )
    parser.add_argument(
        "--pdf",
        metavar="RUTA",
        help="PDF de malla. Con --solo-malla procesa solo malla.",
    )
    parser.add_argument(
        "--malla-json",
        metavar="RUTA",
        default=str(MALLA_JSON_DEFAULT),
        help=(
            "Fallback JSON de la malla si el PDF no tiene texto "
            f"(default: {MALLA_JSON_DEFAULT})."
        ),
    )
    parser.add_argument(
        "--solo-oferta",
        action="store_true",
        help="Procesa SOLO la oferta: Bronze/Silver/Gold + PostgreSQL.",
    )
    parser.add_argument(
        "--solo-malla",
        action="store_true",
        help="Procesa SOLO la malla: Bronze/Silver malla + MongoDB.",
    )
    parser.add_argument(
        "--sin-bd",
        action="store_true",
        help="Solo Bronze/Silver/Gold; no conecta a PostgreSQL ni MongoDB.",
    )
    parser.add_argument(
        "--oferta",
        metavar="RUTA",
        help=(
            "Compatibilidad: CSV/Excel de oferta. Si es CSV usa el formato "
            "histórico (una fila por sesión)."
        ),
    )
    parser.add_argument(
        "--matriz",
        metavar="RUTA",
        help="Compatibilidad: CSV de matriz de estudiantes (aprobadas/por ver).",
    )
    parser.add_argument(
        "--solo-matriz",
        action="store_true",
        help="Compatibilidad: procesa solo --matriz.",
    )
    parser.add_argument(
        "--periodo",
        metavar="PERIODO",
        help="Sobrescribe el periodo leído del Excel/CSV (ej. 202660).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Muestra la traza completa si ocurre un error inesperado.",
    )
    args = parser.parse_args(argv)
    if args.solo_oferta and args.solo_malla:
        parser.error("--solo-oferta y --solo-malla son mutuamente excluyentes.")
    if args.solo_matriz and (args.solo_oferta or args.solo_malla):
        parser.error("--solo-matriz no se combina con --solo-oferta/--solo-malla.")
    if args.matriz and (args.solo_oferta or args.solo_malla):
        parser.error("--matriz no se combina con --solo-oferta/--solo-malla.")
    return args


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------
def _resumen_json(
    modo: str,
    oferta_data: Optional[OfertaData],
    malla_data: Optional[MallaData],
    matriz: Optional[Dict[str, Any]],
    archivos: Dict[str, Optional[str]],
    bronze: Dict[str, str],
    insertados: Dict[str, Any],
    duracion: float,
) -> Dict[str, Any]:
    cursos = int(oferta_data.oferta["codigo_curso"].nunique()) if oferta_data else 0
    nrcs = int(len(oferta_data.oferta)) if oferta_data else 0
    sesiones = int(len(oferta_data.horario)) if oferta_data else 0
    docentes = int(len(oferta_data.docentes)) if oferta_data else 0
    sedes = int(len(oferta_data.sedes)) if oferta_data else 0
    malla_cursos = int(len(malla_data.malla)) if malla_data else 0
    malla_semestres = (
        int(malla_data.malla["semestre"].nunique(dropna=True))
        if malla_data else 0
    )
    periodo = None
    if oferta_data and not oferta_data.oferta.empty:
        valores = [
            _texto(x) for x in oferta_data.oferta["periodo"].dropna().tolist()
            if _texto(x)
        ]
        periodo = valores[0] if valores else None

    resumen: Dict[str, Any] = {
        "modo": modo,
        "archivos": archivos,
        "bronze": bronze,
        "cursos": cursos,
        "nrcs": nrcs,
        "sesiones": sesiones,
        "docentes": docentes,
        "sedes": sedes,
        "malla_cursos": malla_cursos,
        "malla_semestres": malla_semestres,
        "periodo": periodo,
        "insertados": insertados,
        "duracion_seg": round(duracion, 2),
    }
    if oferta_data:
        resumen["fuente_oferta"] = oferta_data.origen
        resumen["metricas_oferta"] = oferta_data.metricas
    if malla_data:
        resumen["fuente_malla"] = malla_data.origen
        resumen["programa_malla"] = malla_data.programa or None
        resumen["sede_malla"] = malla_data.sede or None
    if matriz is not None:
        resumen["estudiantes"] = len(matriz["estudiantes"])
        resumen["aprobadas"] = int(matriz["aprobadas"])
        resumen["por_ver"] = int(matriz["por_ver_count"])
        resumen["formato_matriz"] = matriz["formato"]
    return resumen


def ejecutar(args: argparse.Namespace) -> int:
    inicio = time.perf_counter()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    _banner()
    _log(f"Inicio: {datetime.now().isoformat(timespec='seconds')}")
    _log(f"Data Lake: {RAIZ}")
    _asegurar_dirs()

    excel_path, pdf_path, es_matriz = _resolver_fuentes(args)
    malla_json_path = Path(args.malla_json).expanduser()

    # ------------------------------------------------------------------
    # Modo legado: matriz de estudiantes
    # ------------------------------------------------------------------
    if es_matriz:
        # Validar dependencias antes de copiar nada a Bronze: así una corrida
        # sin pandas no deja artefactos parciales en el Data Lake.
        _requerir_pandas("la matriz de estudiantes")
        matriz_path = Path(args.matriz).expanduser() if args.matriz else MATRIZ_DEFAULT
        _log("\n[1/4] BRONZE: matriz de estudiantes")
        bronze_matriz = _copiar_bronze(matriz_path, "matriz", run_id)
        _log(f"   {bronze_matriz}")

        _log("\n[2/4] SILVER: matriz de estudiantes")
        matriz = procesar_matriz_csv(matriz_path)
        _guardar_parquet(matriz["historial"], SILVER / "historial.parquet")
        _guardar_parquet(matriz["matriz_por_ver"], SILVER / "matriz_por_ver.parquet")
        _guardar_parquet(matriz["perfil"], SILVER / "estudiante_perfil.parquet")

        insertados: Dict[str, Any] = {}
        if args.sin_bd:
            _log("\n[3/4] --sin-bd: no se conectó a PostgreSQL.")
        else:
            _log("\n[3/4] POSTGRESQL: cargando matriz...")
            insertados["postgres"] = cargar_postgres_matriz(matriz)

        resumen = _resumen_json(
            "solo_matriz", None, None, matriz,
            {"matriz": str(matriz_path), "excel": None, "pdf": None,
             "malla_json": None},
            {"matriz": str(bronze_matriz)}, insertados,
            time.perf_counter() - inicio,
        )
        _log("\n[4/4] RESUMEN")
        _log("RESUMEN_JSON: " + json.dumps(resumen, ensure_ascii=False, default=str))
        _log(f"\nIngesta finalizada en {time.perf_counter() - inicio:.2f}s.")
        return 0

    # ------------------------------------------------------------------
    # Modos oferta / malla / completo
    # ------------------------------------------------------------------
    hacer_oferta = excel_path is not None
    hacer_malla = pdf_path is not None
    if not hacer_oferta and not hacer_malla:
        raise IngestaError("No hay archivos para procesar.")

    # Validar dependencias antes de tocar Bronze/Silver: si falta pandas,
    # pyarrow u openpyxl, la corrida falla limpia y no deja artefactos a medias.
    if hacer_oferta:
        _requerir_pandas("la oferta")
        if excel_path is not None and excel_path.suffix.lower() != ".csv":
            _requerir_openpyxl()
    if hacer_malla:
        _requerir_pandas("la malla curricular")

    oferta_data: Optional[OfertaData] = None
    malla_data: Optional[MallaData] = None
    bronze: Dict[str, str] = {}
    archivos: Dict[str, Optional[str]] = {
        "excel": str(excel_path) if excel_path else None,
        "pdf": str(pdf_path) if pdf_path else None,
        "malla_json": str(malla_json_path) if malla_json_path.is_file() else None,
    }

    # Numeración simple y estable: siempre [1/5] Bronze, [2/5] Silver,
    # [3/5] Gold, [4/5] BD, [5/5] Resumen.

    _log("\n[1/5] BRONZE: copiando archivos crudos...")
    if hacer_oferta and excel_path is not None:
        bronze_oferta = _copiar_bronze(excel_path, "oferta", run_id)
        bronze["oferta"] = str(bronze_oferta)
        _log(f"   oferta  <- {excel_path}")
        _log(f"   bronze  -> {bronze_oferta.name}")
    else:
        _log("   oferta  -> no se procesa en este modo")
    if hacer_malla and pdf_path is not None:
        bronze_pdf = _copiar_bronze(pdf_path, "malla", run_id)
        bronze["malla"] = str(bronze_pdf)
        _log(f"   malla   <- {pdf_path}")
        _log(f"   bronze  -> {bronze_pdf.name}")
    else:
        _log("   malla   -> no se procesa en este modo")

    _log("\n[2/5] SILVER: leyendo y normalizando...")
    if hacer_oferta and excel_path is not None:
        if excel_path.suffix.lower() == ".csv":
            oferta_data = procesar_oferta_csv(excel_path, args.periodo)
            _log("   fuente  -> CSV histórico de oferta")
        else:
            oferta_data = procesar_oferta_excel(excel_path, args.periodo)
            _log("   fuente  -> Excel de oferta")
        _log(
            f"   oferta  -> {len(oferta_data.oferta):,} NRC, "
            f"{len(oferta_data.horario):,} sesiones, "
            f"{len(oferta_data.docentes):,} docentes, "
            f"{len(oferta_data.sedes):,} sedes"
        )
    if hacer_malla and pdf_path is not None:
        malla_data = procesar_malla_pdf(pdf_path, malla_json_path)
        if malla_data.origen == "json_fallback":
            bronze_json = _copiar_bronze(malla_json_path, "malla_curricular", run_id)
            bronze["malla_json"] = str(bronze_json)
            archivos["malla_json"] = str(malla_json_path)
            _log(f"   bronze  -> {bronze_json.name} (fallback JSON)")
        _log(
            f"   malla   -> {len(malla_data.malla):,} cursos "
            f"(origen: {malla_data.origen})"
        )

    _log("\n[3/5] GOLD: agregando y escribiendo Parquet...")
    if oferta_data and malla_data:
        encontrados = _enriquecer_oferta_con_malla(oferta_data.oferta, malla_data.malla)
        if encontrados:
            _info(
                f"{encontrados:,} NRC de la oferta cruzados con la malla por "
                "código de curso (programa/semestre/horas)."
            )

    if oferta_data:
        _guardar_parquet(oferta_data.oferta, SILVER / "oferta.parquet")
        _guardar_parquet(oferta_data.horario, SILVER / "horario.parquet")
        _guardar_parquet(oferta_data.docentes, SILVER / "docentes.parquet")
        _guardar_parquet(oferta_data.sedes, SILVER / "sedes.parquet")
        gold_oferta = _gold_oferta(oferta_data.oferta)
        _guardar_parquet(gold_oferta, GOLD / "oferta_resumen.parquet")
    else:
        _log("   oferta  -> no se escribe (modo malla)")

    if malla_data:
        _guardar_parquet(malla_data.malla, SILVER / "malla.parquet")
        gold_malla = _gold_malla(malla_data.malla)
        _guardar_parquet(gold_malla, GOLD / "malla_resumen.parquet")
    else:
        _log("   malla   -> no se escribe (modo oferta)")

    insertados = {}
    if args.sin_bd:
        _log("\n[4/5] --sin-bd: no se conectó a PostgreSQL ni MongoDB.")
    else:
        _log("\n[4/5] BASES DE DATOS")
        if oferta_data:
            _log("   PostgreSQL:")
            insertados["postgres"] = cargar_postgres(oferta_data)
        if malla_data:
            _log("   MongoDB:")
            insertados["mongo"] = cargar_mongo(malla_data.malla)

    if hacer_oferta and hacer_malla:
        modo = "completo"
    elif hacer_oferta:
        modo = "solo_oferta"
    else:
        modo = "solo_malla"

    resumen = _resumen_json(
        modo, oferta_data, malla_data, None, archivos, bronze, insertados,
        time.perf_counter() - inicio,
    )
    _log("\n[5/5] RESUMEN")
    _log("RESUMEN_JSON: " + json.dumps(resumen, ensure_ascii=False, default=str))
    _log(f"\nIngesta finalizada en {time.perf_counter() - inicio:.2f}s.")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return ejecutar(args)
    except DependenciaFaltante as exc:
        _log(f"\n[ERROR] {exc}")
        _log("No se completó la ingesta; no se imprimió RESUMEN_JSON.")
        return 2
    except IngestaError as exc:
        _log(f"\n[ERROR] {exc}")
        _log("No se completó la ingesta; no se imprimió RESUMEN_JSON.")
        return 2
    except KeyboardInterrupt:
        _log("\n[ERROR] Ingesta cancelada por el usuario.")
        return 130
    except Exception as exc:  # noqa: BLE001
        if args.debug:
            traceback.print_exc()
        _log(f"\n[ERROR] Error inesperado: {exc}")
        _log("Use --debug para ver la traza completa.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
