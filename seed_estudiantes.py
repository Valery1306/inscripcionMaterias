#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
seed_estudiantes.py — Siembra idempotente de 10 estudiantes de ejemplo.

Inserta (o actualiza) los estudiantes con id 1..10, nombres realistas en
español y ``plan_id='IS-2024'``. Se puede ejecutar tantas veces como se quiera:
el ``INSERT ... ON CONFLICT (id) DO UPDATE`` no duplica filas y deja los
nombres/planes alineados con este archivo. Al final ajusta la secuencia de
``estudiante.id`` con ``setval`` para que los próximos altas no choquen con los
IDs sembrados.

Uso local (PostgreSQL accesible en localhost:5432)::

    python seed_estudiantes.py

Uso dentro del contenedor del backend (la red de docker-compose resuelve
``postgres``)::

    docker compose exec backend python seed_estudiantes.py

Conexión por variables de entorno (defaults entre paréntesis):
``DB_HOST`` (localhost), ``DB_PORT`` (5432), ``DB_NAME`` (universidad),
``DB_USER`` (postgres) y ``DB_PASSWORD`` (postgres).

Requiere psycopg2 (``pip install psycopg2-binary``); si falta, se muestra un
mensaje claro. La tabla ``estudiante`` la crea/migra ``backend/init_db.py`` al
arrancar el backend. Este script también convierte ``plan_id`` a TEXT si
encuentra una base antigua con la columna INTEGER (misma migración idempotente
que init_db.py), para poder ejecutarse de forma autónoma.

Códigos de salida: 0 si todo fue bien, 1 ante cualquier error.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
DB_CONFIG_POR_DEFECTO: Dict[str, object] = {
    "host": "localhost",
    "port": 5432,
    "dbname": "universidad",
    "user": "postgres",
    "password": "postgres",
}

# (id, nombre). El plan es el mismo para todos: 'IS-2024'.
ESTUDIANTES: List[Tuple[int, str]] = [
    (1, "Ana María Rodríguez Gómez"),
    (2, "Carlos Andrés Martínez López"),
    (3, "Laura Sofía Hernández Ramírez"),
    (4, "Juan José Torres Mendoza"),
    (5, "Valentina García Castro"),
    (6, "Santiago Ramírez Ortiz"),
    (7, "Isabella Moreno Vargas"),
    (8, "Daniel Esteban Cárdenas Rojas"),
    (9, "Camila Fernanda López Herrera"),
    (10, "Andrés Felipe Muñoz Silva"),
]

PLAN_ID = "IS-2024"
INTENTOS_CONEXION = 5
ESPERA_ENTRE_INTENTOS = 1.0
TIMEOUT_CONEXION = 3


def _entero_desde_entorno(nombre: str, por_defecto: int) -> int:
    """Lee una variable de entorno entera; usa el default si no es válida."""
    crudo = os.getenv(nombre)
    if crudo is None or str(crudo).strip() == "":
        return por_defecto
    try:
        return int(str(crudo).strip())
    except (TypeError, ValueError):
        print(
            f"[seed_estudiantes] AVISO: {nombre}={crudo!r} no es un entero "
            f"válido; se usa {por_defecto}.",
            file=sys.stderr,
        )
        return por_defecto


def config_bd() -> Dict[str, object]:
    """Configuración psycopg2 leída de DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD."""
    return {
        "host": os.getenv("DB_HOST", str(DB_CONFIG_POR_DEFECTO["host"])),
        "port": _entero_desde_entorno(
            "DB_PORT", int(DB_CONFIG_POR_DEFECTO["port"])
        ),
        "dbname": os.getenv("DB_NAME", str(DB_CONFIG_POR_DEFECTO["dbname"])),
        "user": os.getenv("DB_USER", str(DB_CONFIG_POR_DEFECTO["user"])),
        "password": os.getenv(
            "DB_PASSWORD", str(DB_CONFIG_POR_DEFECTO["password"])
        ),
    }


def _destino(config: Dict[str, object]) -> str:
    """Representación sin contraseña para los mensajes."""
    return (
        f"{config.get('user', '?')}@{config.get('host', '?')}:"
        f"{config.get('port', '?')}/{config.get('dbname', '?')}"
    )


def esperar_conexion(config: Dict[str, object]):
    """
    Abre la conexión con reintentos cortos (5 intentos, 1 s entre cada uno).

    Lanza RuntimeError con el último error si PostgreSQL no responde.
    """
    import psycopg2

    ultimo_error: Optional[Exception] = None
    for intento in range(1, INTENTOS_CONEXION + 1):
        try:
            conexion = psycopg2.connect(
                **config, connect_timeout=TIMEOUT_CONEXION
            )
            print(
                f"[seed_estudiantes] Conectado a {_destino(config)} "
                f"(intento {intento}/{INTENTOS_CONEXION})."
            )
            return conexion
        except psycopg2.Error as exc:
            ultimo_error = exc
            if intento < INTENTOS_CONEXION:
                print(
                    f"[seed_estudiantes] PostgreSQL aún no responde "
                    f"(intento {intento}/{INTENTOS_CONEXION}): {exc}. "
                    f"Reintentando en {ESPERA_ENTRE_INTENTOS:g}s...",
                    file=sys.stderr,
                )
                time.sleep(ESPERA_ENTRE_INTENTOS)
    raise RuntimeError(
        f"No se pudo conectar a PostgreSQL ({_destino(config)}) tras "
        f"{INTENTOS_CONEXION} intento(s): {ultimo_error}"
    )


def _asegurar_plan_id_text(cur) -> None:
    """
    Comprueba que ``estudiante.plan_id`` exista y sea TEXT.

    Si la base es antigua (plan_id INTEGER), aplica la misma migración
    idempotente que ``backend/init_db.py``. Lanza RuntimeError con una
    instrucción accionable si falta la tabla/columna.
    """
    cur.execute(
        """
        SELECT data_type
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'estudiante'
          AND column_name = 'plan_id'
        """
    )
    fila = cur.fetchone()
    if fila is None:
        raise RuntimeError(
            "No existe la tabla/columna 'estudiante.plan_id'. Ejecuta "
            "'python init_db.py' (o reinicia el contenedor del backend) para "
            "crear el esquema antes de sembrar los estudiantes."
        )
    if str(fila[0]).lower() != "text":
        print(
            "[seed_estudiantes] Migrando estudiante.plan_id "
            f"({fila[0]}) a TEXT..."
        )
        cur.execute(
            "ALTER TABLE estudiante ALTER COLUMN plan_id TYPE TEXT "
            "USING plan_id::text"
        )


def sembrar(conexion) -> Tuple[int, int, Optional[str]]:
    """
    Aplica el upsert de los 10 estudiantes y ajusta la secuencia.

    Devuelve ``(total, ultimo_id, nombre_secuencia)``. La transacción la
    controla esta función con ``with conexion:`` (COMMIT/ROLLBACK).
    """
    with conexion:  # COMMIT al salir; ROLLBACK si algo falla.
        with conexion.cursor() as cur:
            _asegurar_plan_id_text(cur)

            cur.executemany(
                """
                INSERT INTO estudiante (id, nombre, plan_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    nombre  = EXCLUDED.nombre,
                    plan_id = EXCLUDED.plan_id
                """,
                [(sid, nombre, PLAN_ID) for sid, nombre in ESTUDIANTES],
            )

            # La secuencia debe continuar después del mayor id existente (no
            # solo después del 10: puede haber estudiantes reales con id > 10).
            cur.execute(
                "SELECT pg_get_serial_sequence('estudiante', 'id') AS secuencia"
            )
            fila_secuencia = cur.fetchone()
            secuencia = fila_secuencia[0] if fila_secuencia else None

            if secuencia:
                cur.execute(
                    "SELECT setval(%s::regclass, "
                    "(SELECT GREATEST(COALESCE(MAX(id), 0), 1) "
                    "FROM estudiante), true)",
                    (secuencia,),
                )

            cur.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM estudiante")
            fila_max = cur.fetchone()
            ultimo_id = int(fila_max[0]) if fila_max else 0

    return len(ESTUDIANTES), ultimo_id, secuencia


def main() -> int:
    """Entry point de ``python seed_estudiantes.py``. Devuelve 0/1."""
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        print(
            "[seed_estudiantes] ERROR: psycopg2 no está instalado. "
            "Instálalo con 'pip install psycopg2-binary' o ejecuta este "
            "script dentro del contenedor del backend.",
            file=sys.stderr,
        )
        return 1

    config = config_bd()
    print(f"[seed_estudiantes] PostgreSQL: {_destino(config)}")

    conexion = None
    try:
        conexion = esperar_conexion(config)
        total, ultimo_id, secuencia = sembrar(conexion)
    except RuntimeError as exc:
        print(f"[seed_estudiantes] ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - se informa y se sale con 1
        if getattr(exc, "pgcode", None) == "42P01":
            print(
                "[seed_estudiantes] ERROR: falta la tabla 'estudiante'. "
                "Ejecuta 'python init_db.py' (o reinicia el backend) antes de "
                "sembrar.",
                file=sys.stderr,
            )
        else:
            print(
                f"[seed_estudiantes] ERROR al sembrar estudiantes: {exc}",
                file=sys.stderr,
            )
        return 1
    finally:
        if conexion is not None:
            try:
                conexion.close()
            except Exception:  # noqa: BLE001 - cerrar no debe enmascarar el error
                pass

    print(f"[seed_estudiantes] Estudiantes insertados/actualizados: {total}")
    for sid, nombre in ESTUDIANTES:
        print(f"[seed_estudiantes]   {sid:>2}  {nombre}  ({PLAN_ID})")
    if secuencia:
        print(
            f"[seed_estudiantes] Secuencia '{secuencia}' ajustada; "
            f"último id disponible: {ultimo_id}."
        )
    else:
        print(
            "[seed_estudiantes] AVISO: la tabla 'estudiante' no tiene "
            "secuencia asociada a id; no se pudo aplicar setval."
        )
    print("[seed_estudiantes] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
