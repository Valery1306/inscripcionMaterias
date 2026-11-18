#!/usr/bin/env bash
# =============================================================================
# iniciar_primera_vez.sh — Puesta en marcha INICIAL del proyecto
#
# Úsalo una sola vez (o cuando quieras recargar todos los datos desde cero):
#   1. Construye y levanta la pila Docker (PostgreSQL, MongoDB, Neo4j,
#      backend y frontend).
#   2. Instala las dependencias de Python de los scripts de datos.
#   3. Carga la malla (PDF) y la oferta (Excel) de datalake/entrada/ en el
#      Data Lake (Bronze/Silver/Gold), PostgreSQL y MongoDB.
#   4. Siembra estudiantes de ejemplo (idempotente).
#   5. Sincroniza el grafo de prerrequisitos en Neo4j con las reglas.
#
# Uso (desde Git Bash, en la raíz del proyecto):
#   bash iniciar_primera_vez.sh
#
# Para los arranques siguientes usa:  bash iniciar.sh
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")"

# ---------------------------------------------------------------- utilidades
paso()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m  ✔ %s\033[0m\n' "$*"; }
aviso() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
fallo() { printf '\033[1;31m  ✘ %s\033[0m\n' "$*" >&2; exit 1; }

# Espera a que una URL responda (máx. ~3 min).
esperar_url() {
  local url="$1" nombre="$2" intentos=60
  printf '  Esperando a %s' "$nombre"
  until curl -fs -o /dev/null "$url"; do
    intentos=$((intentos - 1))
    if [ "$intentos" -le 0 ]; then
      echo
      fallo "$nombre no respondió en $url. Revisa: docker compose logs"
    fi
    printf '.'
    sleep 3
  done
  echo
  ok "$nombre listo ($url)"
}

# Una comilla suelta en el PATH de Windows (p. ej. la de PowerToys) impide que
# Docker encuentre docker-credential-desktop. Se elimina solo en esta sesión.
PATH="${PATH//\"/}"
export PATH

# Los scripts de Python imprimen tildes y símbolos: forzar UTF-8 en Windows.
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8

# ------------------------------------------------------- 0. requisitos previos
paso "Verificando requisitos"

command -v docker >/dev/null 2>&1 || fallo "Docker no está instalado."
docker info >/dev/null 2>&1 || fallo "Docker Desktop no está corriendo. Ábrelo y vuelve a intentar."
ok "Docker disponible"

command -v curl >/dev/null 2>&1 || fallo "curl no está disponible (viene con Git Bash)."

PY=""
for candidato in python python3 py; do
  if command -v "$candidato" >/dev/null 2>&1 &&
     "$candidato" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PY="$candidato"
    break
  fi
done
[ -n "$PY" ] || fallo "No se encontró Python 3.9+ en el PATH."
ok "Python: $("$PY" --version 2>&1)"

[ -f datalake/entrada/chorarios_26082026.xlsx ] ||
  aviso "No está datalake/entrada/chorarios_26082026.xlsx (oferta). La ingesta usará lo que haya en datalake/entrada/."
[ -f datalake/entrada/ruta_nueva_isum_v2.pdf ] ||
  aviso "No está datalake/entrada/ruta_nueva_isum_v2.pdf (malla). Se usará datalake/malla_curricular.json como respaldo."

# ------------------------------------------------------- 1. levantar la pila
paso "1/5 Construyendo y levantando los contenedores (puede tardar varios minutos)"
docker compose up -d --build

esperar_url "http://localhost:8000/" "Backend (API)"
esperar_url "http://localhost/"      "Frontend"
esperar_url "http://localhost:7474/" "Neo4j"

# --------------------------------------------- 2. dependencias de Python
paso "2/5 Instalando dependencias de Python para los scripts de datos"
"$PY" -m pip install --disable-pip-version-check -q \
  pandas openpyxl pyarrow pdfplumber psycopg2-binary pymongo neo4j
ok "Dependencias instaladas"

# --------------------------------------------------- 3. ingesta del Data Lake
paso "3/5 Cargando malla y oferta (Data Lake -> PostgreSQL y MongoDB)"
"$PY" datalake/ingesta_datalake.py
ok "Ingesta completada"

# ------------------------------------------------- 4. estudiantes de ejemplo
paso "4/5 Sembrando estudiantes de ejemplo (IDs 1 a 10)"
if docker compose exec -T backend python seed_estudiantes.py; then
  ok "Estudiantes de ejemplo listos"
else
  aviso "No se pudieron sembrar los estudiantes (opcional; el login los registra igualmente)."
fi

# ---------------------------------------------- 5. grafo de prerrequisitos
paso "5/5 Sincronizando el grafo de prerrequisitos en Neo4j"
# Neo4j puede tardar unos segundos más en aceptar conexiones bolt.
for intento in 1 2 3 4 5; do
  if "$PY" actualizar_grafo.py; then
    ok "Grafo sincronizado"
    break
  fi
  [ "$intento" -lt 5 ] || fallo "No se pudo sincronizar el grafo. Revisa: docker compose logs neo4j"
  aviso "Neo4j aún no acepta conexiones; reintentando en 10 s ($intento/5)"
  sleep 10
done

# ------------------------------------------------------------------ resumen
cat <<'EOF'

==================================================================
  ✅ Proyecto listo

  Aplicación web ....... http://localhost
  Documentación API .... http://localhost:8000/docs
  Consola Neo4j ........ http://localhost:7474  (neo4j / password)

  Ingreso administrador: usuario "admin" / contraseña "Hola12345"
  Ingreso estudiante ..: cualquier ID numérico (p. ej. 1)

  Próximas veces:  bash iniciar.sh
  Detener todo:    docker compose stop
==================================================================
EOF
