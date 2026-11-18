#!/usr/bin/env bash
# =============================================================================
# iniciar.sh — Arranque rápido del proyecto (después de la primera vez)
#
# Los datos (PostgreSQL, MongoDB, Neo4j y el Data Lake) se conservan en los
# volúmenes de Docker, así que aquí NO se vuelve a cargar nada: solo se
# levantan los contenedores y se espera a que estén listos.
#
# Uso (desde Git Bash, en la raíz del proyecto):
#   bash iniciar.sh            # arranca con las imágenes ya construidas
#   bash iniciar.sh --build    # reconstruye antes (si cambiaste código/estilos)
#   bash iniciar.sh --grafo    # además resincroniza Neo4j con las reglas
#
# Las opciones se pueden combinar:  bash iniciar.sh --build --grafo
# Primera vez en un equipo nuevo:   bash iniciar_primera_vez.sh
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")"

paso()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m  ✔ %s\033[0m\n' "$*"; }
aviso() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
fallo() { printf '\033[1;31m  ✘ %s\033[0m\n' "$*" >&2; exit 1; }

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

# ----------------------------------------------------------------- opciones
CONSTRUIR=0
GRAFO=0
for arg in "$@"; do
  case "$arg" in
    --build) CONSTRUIR=1 ;;
    --grafo) GRAFO=1 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) fallo "Opción desconocida: $arg (usa --build, --grafo o --help)" ;;
  esac
done

# Ver iniciar_primera_vez.sh: quita la comilla suelta del PATH de Windows.
PATH="${PATH//\"/}"
export PATH
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8

# ------------------------------------------------------------ requisitos
paso "Verificando Docker"
command -v docker >/dev/null 2>&1 || fallo "Docker no está instalado."
docker info >/dev/null 2>&1 || fallo "Docker Desktop no está corriendo. Ábrelo y vuelve a intentar."
ok "Docker disponible"

# ---------------------------------------------------------------- arranque
if [ "$CONSTRUIR" -eq 1 ]; then
  paso "Reconstruyendo y levantando los contenedores"
  docker compose up -d --build
else
  paso "Levantando los contenedores"
  docker compose up -d
fi

esperar_url "http://localhost:8000/" "Backend (API)"
esperar_url "http://localhost/"      "Frontend"

# ------------------------------------------------------ grafo (opcional)
if [ "$GRAFO" -eq 1 ]; then
  esperar_url "http://localhost:7474/" "Neo4j"
  PY=""
  for candidato in python python3 py; do
    if command -v "$candidato" >/dev/null 2>&1 &&
       "$candidato" -c 'import sys' >/dev/null 2>&1; then
      PY="$candidato"
      break
    fi
  done
  [ -n "$PY" ] || fallo "No se encontró Python para sincronizar el grafo."

  paso "Sincronizando el grafo de prerrequisitos en Neo4j"
  for intento in 1 2 3 4 5; do
    if "$PY" actualizar_grafo.py; then
      ok "Grafo sincronizado"
      break
    fi
    [ "$intento" -lt 5 ] || fallo "No se pudo sincronizar el grafo. Revisa: docker compose logs neo4j"
    aviso "Neo4j aún no acepta conexiones; reintentando en 10 s ($intento/5)"
    sleep 10
  done
fi

docker compose ps --format 'table {{.Name}}\t{{.Status}}'

cat <<'EOF'

==================================================================
  ✅ Proyecto en marcha

  Aplicación web ....... http://localhost
  Documentación API .... http://localhost:8000/docs
  Consola Neo4j ........ http://localhost:7474  (neo4j / password)

  Detener todo:  docker compose stop
==================================================================
EOF
