# -*- coding: utf-8 -*-
"""Pruebas de integración del backend contra bases de datos de prueba."""
import io
import json
import os
import shutil
import sys
import traceback

sys.path.insert(0, os.path.abspath("backend"))

os.environ["DB_HOST"] = "localhost"
os.environ["DB_PORT"] = "5432"
os.environ["DB_NAME"] = "universidad_test_inscripcion"
os.environ["DB_USER"] = "postgres"
os.environ["DB_PASSWORD"] = "postgres"
os.environ["MONGO_URI"] = "mongodb://localhost:27017"
os.environ["MONGO_DB"] = "inscripcion_test_inscripcion"
os.environ["UPLOADS_DIR"] = os.path.abspath(".tmp_deps/uploads")
os.environ["INGESTA_TIMEOUT"] = "30"

import psycopg2
import pymongo
from psycopg2.extras import RealDictCursor

from fastapi.testclient import TestClient

DB = os.environ["DB_NAME"]
MDB = os.environ["MONGO_DB"]

CURSOS = [
    ("CAL-101", "Cálculo I", 4, 1, "Ingeniería de Sistemas", 4),
    ("CAL-201", "Cálculo II", 4, 2, "Ingeniería de Sistemas", 4),
    ("FIS-101", "Física I", 3, 2, "Ingeniería de Sistemas", 4),
    ("PROG-101", "Programación", 3, 1, "Ingeniería de Sistemas", 4),
    ("IND-101", "Procesos", 3, 1, "Ingeniería Industrial", 4),
]
NRCS = [
    (1001, "CAL-101", "2025-2", "D1", "BOG", "Diurna", "Presencial", 30, 5, 25, "Abierto"),
    (1002, "CAL-201", "2025-2", "D2", "BOG", "Diurna", "Presencial", 20, 0, 20, "Abierto"),
    (1003, "FIS-101", "2025-2", "D1", "BOG", "Nocturna", "Presencial", 25, 2, 23, "Abierto"),
    (1004, "PROG-101", "2025-2", "D2", "MED", "Diurna", "Presencial", 40, 0, 40, "Abierto"),
    (1005, "PROG-101", "2025-2", "D1", "BOG", "Diurna", "Presencial", 1, 0, 1, "Abierto"),
    (1006, "IND-101", "2025-2", "D2", "BOG", "Diurna", "Presencial", 30, 0, 30, "Abierto"),
]
SESIONES = [
    (1001, 1, 1, "07:00", "09:00", "A101", "Bloque A"),
    (1001, 2, 3, "07:00", "09:00", "A101", "Bloque A"),
    (1002, 1, 2, "09:00", "11:00", "B202", "Bloque B"),
    (1003, 1, 1, "18:00", "20:00", "C303", "Bloque C"),
    (1004, 1, 1, "07:00", "09:00", "D404", "Bloque D"),
    (1005, 1, 4, "10:00", "12:00", "E505", "Bloque E"),
    (1006, 1, 5, "08:00", "10:00", "F606", "Bloque F"),
]
MALLA = [
    {
        "programa": "Ingeniería de Sistemas",
        "sede": "Bogotá",
        "cursos": [
            {"semestre": 1, "codigo": "CAL-101", "nombre": "Cálculo I", "creditos": 4, "horas": 4, "prerrequisitos": []},
            {"semestre": 1, "codigo": "PROG-101", "nombre": "Programación", "creditos": 3, "horas": 4, "prerrequisitos": []},
            {"semestre": 2, "codigo": "CAL-201", "nombre": "Cálculo II", "creditos": 4, "horas": 4, "prerrequisitos": ["CAL-101"]},
            {"semestre": 2, "codigo": "FIS-101", "nombre": "Física I", "creditos": 3, "horas": 4, "prerrequisitos": []},
        ],
    },
    {
        "programa": "Ingeniería Industrial",
        "sede": "Medellín",
        "cursos": [
            {"semestre": 1, "codigo": "IND-101", "nombre": "Procesos", "creditos": 3, "horas": 4, "prerrequisitos": []},
        ],
    },
]
PROGRAMAS = ["Ingeniería de Sistemas", "Ingeniería Industrial"]


def conectar():
    return psycopg2.connect(host="localhost", port=5432, dbname=DB, user="postgres", password="postgres")


def reset_bd():
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE curso, docente, sede, nrc, horario, estudiante, "
                "inscripcion, historial, estudiante_perfil, matriz_por_ver "
                "RESTART IDENTITY CASCADE"
            )
            cur.executemany(
                "INSERT INTO curso (codigo_alfanumerico,nombre,creditos,semestre,programa,horas) "
                "VALUES (%s,%s,%s,%s,%s,%s)", CURSOS,
            )
            cur.executemany(
                "INSERT INTO docente (id_docente,nombre) VALUES (%s,%s)",
                [("D1", "Juan Pérez"), ("D2", "Ana Gómez")],
            )
            cur.executemany(
                "INSERT INTO sede (codigo,nombre) VALUES (%s,%s)",
                [("BOG", "Bogotá"), ("MED", "Medellín")],
            )
            cur.executemany(
                "INSERT INTO nrc (id_nrc,curso_id,periodo,docente_id,sede_id,jornada,"
                "modalidad,cupo_max,inscritos,saldo,estado) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", NRCS,
            )
            cur.executemany(
                "INSERT INTO horario (nrc_id,numero_sesion,dia,hora_inicio,hora_fin,salon,edificio) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)", SESIONES,
            )
            cur.execute(
                "INSERT INTO estudiante (id,nombre,plan_id) VALUES (1,'Estudiante Uno',0)"
            )
            cur.execute("SELECT setval(pg_get_serial_sequence('estudiante','id'), 1, true)")
        conn.commit()
    finally:
        conn.close()


def reset_mongo():
    cli = pymongo.MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=3000)
    cli.drop_database(MDB)
    db = cli[MDB]
    db["malla_curricular"].insert_many(MALLA)
    db["reglas_negocio"].insert_many(
        [
            {"materia": "CAL-101", "prerrequisitos": []},
            {"materia": "PROG-101", "prerrequisitos": []},
            {"materia": "CAL-201", "prerrequisitos": ["CAL-101"]},
            {"materia": "FIS-101", "prerrequisitos": []},
            {"materia": "IND-101", "prerrequisitos": []},
        ]
    )


def make_xlsx(filas, encabezado):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(encabezado)
    for fila in filas:
        ws.append(fila)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  OK:", msg)


def main():
    print("== reset bases ==")
    reset_mongo()
    reset_bd()
    shutil.rmtree(os.environ["UPLOADS_DIR"], ignore_errors=True)

    import main as api

    with TestClient(api.app) as cliente:
        # ------------------------------------------------------------
        print("== GET /api/catalogos ==")
        r = cliente.get("/api/catalogos")
        check(r.status_code == 200, f"status {r.status_code}")
        cat = r.json()
        check(set(PROGRAMAS) <= set(cat["programas"]), f"programas {cat['programas']}")
        check(cat["jornadas"] == ["Diurna", "Nocturna", "Mixta", "Virtual"], "jornadas")
        check([s["codigo"] for s in cat["sedes"]] == ["BOG", "MED"], f"sedes {cat['sedes']}")
        check(cat["sedes_nombres"] == ["Bogotá", "Medellín"], f"sedes_nombres {cat['sedes_nombres']}")

        # ------------------------------------------------------------
        print("== GET /api/admin/malla ==")
        r = cliente.get("/api/admin/malla")
        check(r.status_code == 200, "status")
        malla = r.json()
        check(len(malla) == 5, f"5 cursos ({len(malla)})")
        check(all(set(x) == {"programa", "sede", "semestre", "codigo", "nombre", "creditos", "horas", "prerrequisitos"} for x in malla), "contrato de curso")

        print("== PUT /api/admin/malla ==")
        r = cliente.put(
            "/api/admin/malla",
            json=[
                {"programa": "Ingeniería de Sistemas", "sede": "Bogotá", "semestre": 1, "codigo": "CAL-101", "nombre": "Cálculo I", "creditos": 4, "horas": 4, "prerrequisitos": []},
                {"programa": "Ingeniería de Sistemas", "sede": "Bogotá", "semestre": 3, "codigo": "BD-301", "nombre": "Bases de Datos", "creditos": 3, "horas": 4, "prerrequisitos": ["PROG-101"]},
            ],
        )
        check(r.status_code == 200, f"status {r.status_code}: {r.text}")
        res = r.json()
        check(res["total"] == 2 and res["insertados"] == 1 and res["actualizados"] == 1, f"conteos {res}")
        malla = cliente.get("/api/admin/malla").json()
        check(any(x["codigo"] == "BD-301" for x in malla), "nuevo curso en malla")
        reglas = {x["materia"]: x["prerrequisitos"] for x in cliente.get("/api/admin/reglas").json()}
        check(reglas.get("BD-301") == ["PROG-101"], "regla BD-301 actualizada")

        print("== PUT /api/admin/malla (envoltorio) ==")
        r = cliente.put("/api/admin/malla", json={"cursos": [{"programa": "Ingeniería de Sistemas", "codigo": "BD-301", "nombre": "Bases de Datos II", "creditos": 3, "semestre": 3, "prerrequisitos": ["PROG-101"]}]})
        check(r.status_code == 200 and r.json()["actualizados"] == 1, f"envoltorio {r.status_code} {r.text}")

        # ------------------------------------------------------------
        print("== GET /api/admin/oferta ==")
        r = cliente.get("/api/admin/oferta")
        check(r.status_code == 200, "status")
        oferta = r.json()
        check(len(oferta) == 6, f"6 NRC ({len(oferta)})")
        n1001 = next(x for x in oferta if x["id_nrc"] == 1001)
        check(n1001["docente_nombre"] == "Juan Pérez", "docente_nombre")
        check(n1001["sede_nombre"] == "Bogotá", "sede_nombre")
        check(n1001["materia"] == "Cálculo I" and n1001["creditos"] == 4, "materia/creditos")
        check(len(n1001["horario"]) == 2 and n1001["horario"][0]["salon"] == "A101", "horario con salón")

        print("== PUT /api/admin/oferta ==")
        r = cliente.put(
            "/api/admin/oferta",
            json=[{
                "id_nrc": 1001,
                "cupo_max": 35,
                "estado": "Cerrado",
                "docente_nombre": "Juan Pérez Actualizado",
                "sede_nombre": "Bogotá Principal",
                "horario": [{"dia": 1, "hora_inicio": "08:00", "hora_fin": "10:00", "salon": "Z1", "edificio": "Nuevo"}],
            }],
        )
        check(r.status_code == 200, f"status {r.status_code}: {r.text}")
        check(r.json()["actualizados"] == 1 and r.json()["sesiones"] == 1, f"conteos {r.json()}")
        conn = conectar()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT cupo_max, estado, saldo FROM nrc WHERE id_nrc=1001")
            fila = cur.fetchone()
            check(fila["cupo_max"] == 35 and fila["estado"] == "Cerrado", f"nrc actualizado {fila}")
            cur.execute("SELECT count(*) AS n FROM horario WHERE nrc_id=1001")
            check(cur.fetchone()["n"] == 1, "horario reemplazado")
            cur.execute("SELECT nombre FROM docente WHERE id_docente='D1'")
            check(cur.fetchone()["nombre"] == "Juan Pérez Actualizado", "docente_nombre actualizado")
            cur.execute("SELECT nombre FROM sede WHERE codigo='BOG'")
            check(cur.fetchone()["nombre"] == "Bogotá Principal", "sede_nombre actualizada")
        conn.close()

        # Restaura los nombres para el resto de pruebas (el perfil usa "Bogotá").
        r = cliente.put(
            "/api/admin/oferta",
            json=[{"id_nrc": 1001, "docente_nombre": "Juan Pérez", "sede_nombre": "Bogotá"}],
        )
        check(r.status_code == 200 and r.json()["actualizados"] == 1, "nombres restaurados")

        print("== PUT /api/admin/oferta rollback ==")
        r = cliente.put("/api/admin/oferta", json=[
            {"id_nrc": 1002, "cupo_max": 22},
            {"id_nrc": 999999, "cupo_max": 10},
        ])
        check(r.status_code == 404, f"404 ({r.status_code})")
        conn = conectar()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT cupo_max FROM nrc WHERE id_nrc=1002")
            check(cur.fetchone()["cupo_max"] == 20, "rollback: 1002 sin cambios")
        conn.close()

        print("== PUT /api/admin/oferta validaciones ==")
        r = cliente.put("/api/admin/oferta", json=[{"id_nrc": 1002, "horario": [{"dia": 2, "hora_inicio": "11:00", "hora_fin": "09:00"}]}])
        check(r.status_code == 400, f"hora_fin<=inicio -> 400 ({r.status_code})")
        r = cliente.put("/api/admin/oferta", json=[{"id_nrc": 1002, "curso_id": "NOEXISTE"}])
        check(r.status_code == 400, f"curso inexistente -> 400 ({r.status_code})")
        r = cliente.put("/api/admin/oferta", json=[{"id_nrc": 1002, "horario": [{"dia": 9, "hora_inicio": "08:00", "hora_fin": "09:00"}]}])
        check(r.status_code == 400, f"dia inválido -> 400 ({r.status_code})")

        # ------------------------------------------------------------
        print("== POST /api/estudiante/perfil sin matriz ==")
        r = cliente.post(
            "/api/estudiante/perfil",
            data={"programa": "Ingeniería de Sistemas", "jornada": "Diurna", "sede": "Bogotá"},
        )
        check(r.status_code == 200, f"status {r.status_code}: {r.text}")
        perfil = r.json()
        sid = perfil["estudiante_id"]
        check(sid == 2, f"nuevo estudiante max+1 = {sid}")
        check(perfil["semestre"] == 1, f"semestre {perfil['semestre']}")
        check(perfil["carrera"] == "Ingeniería de Sistemas", "carrera=programa")
        check(sorted(perfil["por_ver"]) == ["CAL-101", "PROG-101"], f"primer semestre {perfil['por_ver']}")
        check(perfil["aprobadas"] == [], "historial vacío")

        print("== GET /api/estudiante/{id}/perfil ==")
        r = cliente.get(f"/api/estudiante/{sid}/perfil")
        check(r.status_code == 200, f"status {r.status_code}")
        check(r.json()["perfil"]["jornada"] == "Diurna", "perfil embebido")
        check(cliente.get("/api/estudiante/99999/perfil").status_code == 404, "404 inexistente")

        # ------------------------------------------------------------
        print("== POST /api/estudiante/perfil con matriz CSV ==")
        csv = (
            "estudiante_id,estado,codigo_curso\n"
            f"{sid},aprobada,CAL-101\n"
            f"{sid},por_ver,CAL-201\n"
        ).encode("utf-8")
        r = cliente.post(
            "/api/estudiante/perfil",
            data={"programa": "Ingeniería de Sistemas", "jornada": "Diurna", "sede": "BOG"},
            files={"matriz": ("matriz.csv", csv, "text/csv")},
        )
        check(r.status_code == 200, f"status {r.status_code}: {r.text}")
        perfil = r.json()
        check(perfil["aprobadas"] == ["CAL-101"], f"aprobadas {perfil['aprobadas']}")
        check(perfil["por_ver"] == ["CAL-201"], f"por_ver {perfil['por_ver']}")

        print("== POST /api/estudiante/perfil con matriz XLSX (lista) ==")
        xlsx = make_xlsx([["CAL-101"], ["FIS-101"]], ["materias"])
        r = cliente.post(
            "/api/estudiante/perfil",
            data={"programa": "Ingeniería de Sistemas", "jornada": "Diurna", "sede": "BOG"},
            files={"matriz": ("matriz.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        check(r.status_code == 200, f"status {r.status_code}: {r.text}")
        check(r.json()["por_ver"] == ["CAL-101", "FIS-101"], f"lista -> por_ver {r.json()['por_ver']}")

        print("== POST /api/estudiante/perfil con XLSX aprobadas ==")
        xlsx = make_xlsx([["CAL-101"]], ["materias_aprobadas"])
        r = cliente.post(
            "/api/estudiante/perfil",
            data={"programa": "Ingeniería de Sistemas"},
            files={"matriz": ("aprobadas.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        check(r.status_code == 200, f"status {r.status_code}: {r.text}")
        check(r.json()["aprobadas"] == ["CAL-101"], f"aprobadas {r.json()['aprobadas']}")

        # ------------------------------------------------------------
        print("== POST /api/horario/automatico ==")
        # El estudiante 2 tiene CAL-101 aprobada y CAL-201 por ver.
        r = cliente.post("/api/horario/automatico", json={"estudiante_id": sid})
        check(r.status_code == 200, f"status {r.status_code}: {r.text}")
        horario = r.json()
        codigos = {h["codigo"] for h in horario["horario"]}
        check("CAL-201" in codigos, f"CAL-201 asignada {codigos}")
        check("PROG-101" in codigos, f"PROG-101 asignada {codigos}")
        check("FIS-101" not in codigos, "FIS-101 (Nocturna) filtrada por jornada")
        for item in horario["horario"]:
            check(item["sede_id"] == "BOG", f'{item["codigo"]} sede BOG')
            check(item.get("docente_nombre"), f'{item["codigo"]} docente_nombre')
            check(item.get("sede_nombre"), f'{item["codigo"]} sede_nombre')
        conn = conectar()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT count(*) AS n FROM inscripcion WHERE estudiante_id=%s", (sid,))
            check(cur.fetchone()["n"] == len(horario["horario"]), "inscripciones persistidas")
        conn.close()

        print("== horario automatico sin perfil ==")
        r = cliente.post("/api/horario/automatico", json={"estudiante_id": 1})
        check(r.status_code == 200, f"status {r.status_code}: {r.text}")

        # ------------------------------------------------------------
        print("== uploads (subprocess mockeado) ==")
        from unittest.mock import patch

        def fake_run(comando, **kwargs):
            return type("P", (), {"returncode": 0, "stdout": 'RESUMEN_JSON: {"cursos": 3, "nrcs": 4, "sesiones": 5, "docentes": 2, "sedes": 1, "malla_cursos": 7, "programas": ["Ingeniería de Sistemas"]}', "stderr": ""})()

        with patch.object(api.subprocess, "run", side_effect=fake_run) as mocked:
            r = cliente.post("/api/admin/upload-oferta", files={"archivo": ("oferta.csv", b"a,b\n1,2\n", "text/csv")})
            check(r.status_code == 200, f"upload csv {r.status_code}: {r.text}")
            check(mocked.call_args.args[0][2] == "--oferta", f"csv usa --oferta {mocked.call_args.args[0]}")
            check(r.json()["docentes"] == 2 and r.json()["sedes"] == 1, "conteos docentes/sedes")

            xlsx = make_xlsx([["CAL-101", "Cálculo I", 4, 1001]], ["codigo_curso", "nombre_curso", "creditos", "id_nrc"])
            r = cliente.post("/api/admin/upload-oferta", files={"archivo": ("oferta.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
            check(r.status_code == 200, f"upload xlsx {r.status_code}: {r.text}")
            check(mocked.call_args.args[0][2] == "--excel", f"xlsx usa --excel {mocked.call_args.args[0]}")
            check(r.json()["cursos"] == 3 and r.json()["nrcs"] == 4 and r.json()["sesiones"] == 5, "conteos oferta")

            r = cliente.post("/api/admin/upload-malla", files={"archivo": ("malla.pdf", b"%PDF-1.4 dummy", "application/pdf")})
            check(r.status_code == 200, f"upload pdf {r.status_code}: {r.text}")
            check(mocked.call_args.args[0][2] == "--pdf" and mocked.call_args.args[0][3].endswith(".pdf"), f"pdf usa --pdf {mocked.call_args.args[0]}")
            check(r.json()["malla_cursos"] == 7, f"malla_cursos {r.json()}")

        def fake_fail(comando, **kwargs):
            return type("P", (), {"returncode": 2, "stdout": "", "stderr": "boom"})()

        with patch.object(api.subprocess, "run", side_effect=fake_fail):
            r = cliente.post("/api/admin/upload-oferta", files={"archivo": ("oferta.csv", b"a,b\n1,2\n", "text/csv")})
            check(r.status_code == 400, f"fallo ingesta -> 400 ({r.status_code})")
            check(isinstance(r.json()["detail"], dict) and "salida" in r.json()["detail"], "detalle con salida")

        r = cliente.post("/api/admin/upload-malla", files={"archivo": ("malla.txt", b"x", "text/plain")})
        check(r.status_code == 400, f"extensión no permitida -> 400 ({r.status_code})")

    print("\nTODAS LAS PRUEBAS PASARON")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
