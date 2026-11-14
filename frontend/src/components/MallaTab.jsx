import { useEffect, useState } from 'react'
import Banner from './Banner.jsx'
import Spinner from './Spinner.jsx'
import { apiGet } from '../api.js'

// ---------------------------------------------------------------------------
// Pestaña «Ver Malla Curricular».
//
// Carga GET /api/admin/malla y muestra la malla agrupada por semestre en un
// acordeón (el primer semestre queda abierto). El contrato principal es:
//
//   {ok, programa, programas, total,
//    semestres: [{semestre, cursos: [{codigo, nombre, creditos, horas,
//                                    prerrequisitos}]}]}
//
// Como respaldo también se acepta una lista plana de cursos (el formato que
// usaba el dashboard), agrupándola por semestre en el cliente.
// ---------------------------------------------------------------------------

function esObjeto(valor) {
  return Boolean(valor) && typeof valor === 'object' && !Array.isArray(valor)
}

function texto(valor) {
  if (valor == null) return ''
  return String(valor).trim()
}

function textoCampo(valor) {
  if (esObjeto(valor)) {
    return texto(
      valor.nombre ??
        valor.nombre_programa ??
        valor.descripcion ??
        valor.codigo ??
        valor.id,
    )
  }
  return texto(valor)
}

function mostrarNumero(valor) {
  if (valor == null || valor === '') return '—'
  return texto(valor) || '—'
}

/** Normaliza una lista de textos (programas, prerrequisitos, etc.). */
function listaTextos(valor) {
  const salida = []
  const vistos = new Set()

  function agregar(item) {
    const cadena = textoCampo(item)
    if (!cadena) return

    const clave = cadena.toLowerCase()
    if (vistos.has(clave)) return

    vistos.add(clave)
    salida.push(cadena)
  }

  if (Array.isArray(valor)) {
    valor.forEach(agregar)
  } else if (typeof valor === 'string') {
    valor
      .split(/[,;|]/)
      .map((item) => item.trim())
      .filter(Boolean)
      .forEach(agregar)
  } else if (esObjeto(valor)) {
    agregar(valor)
  }

  return salida
}

function normalizarPrerrequisitos(valor) {
  if (Array.isArray(valor)) {
    return valor.flatMap((item) => {
      if (typeof item === 'string' && /[,;|]/.test(item)) {
        return normalizarPrerrequisitos(item)
      }
      return listaTextos(item)
    })
  }

  if (typeof valor === 'string') return listaTextos(valor)
  if (esObjeto(valor)) return listaTextos(valor)
  return []
}

function normalizarCurso(curso, indice) {
  const codigo = texto(
    curso.codigo ?? curso.codigo_materia ?? curso.codigo_alfanumerico,
  )
  const nombre = texto(
    curso.nombre ?? curso.materia ?? curso.nombre_curso ?? curso.nombre_materia,
  )

  return {
    _key: `${codigo || 'curso'}-${indice}`,
    codigo: codigo || '—',
    nombre: nombre || 'Curso sin nombre',
    creditos: mostrarNumero(curso.creditos),
    horas: mostrarNumero(curso.horas),
    prerrequisitos: normalizarPrerrequisitos(
      curso.prerrequisitos ?? curso.prerequisitos ?? curso.requisitos,
    ),
    programa: texto(curso.programa),
  }
}

function compararSemestres(a, b) {
  const cadenaA = texto(a)
  const cadenaB = texto(b)

  // Los cursos sin semestre quedan al final, como hace el backend.
  if (!cadenaA && !cadenaB) return 0
  if (!cadenaA) return 1
  if (!cadenaB) return -1

  const numeroA = Number(cadenaA)
  const numeroB = Number(cadenaB)

  if (Number.isFinite(numeroA) && Number.isFinite(numeroB)) {
    return numeroA - numeroB
  }
  if (Number.isFinite(numeroA)) return -1
  if (Number.isFinite(numeroB)) return 1

  return cadenaA.localeCompare(cadenaB, 'es')
}

function nombreSemestre(valor) {
  const cadena = texto(valor)
  if (!cadena) return 'Sin semestre'
  if (/^semestre\b/i.test(cadena)) {
    return cadena.charAt(0).toUpperCase() + cadena.slice(1)
  }
  return `Semestre ${cadena}`
}

/** Agrupa una lista plana de cursos por semestre. */
function agruparCursos(cursos) {
  const grupos = new Map()

  cursos.forEach((item, indice) => {
    if (!esObjeto(item)) return

    const curso = normalizarCurso(item, indice)
    const semestre = texto(item.semestre)
    const clave = semestre || 'sin-semestre'

    if (!grupos.has(clave)) {
      grupos.set(clave, { semestre, cursos: [] })
    }
    grupos.get(clave).cursos.push(curso)
  })

  const semestres = [...grupos.entries()]
    .map(([clave, grupo]) => ({ clave, ...grupo }))
    .sort((a, b) => compararSemestres(a.semestre, b.semestre))

  semestres.forEach((grupo, indice) => {
    grupo.clave = `${grupo.clave || 'sin-semestre'}-${indice}`
    grupo.cursos.sort((a, b) => a.codigo.localeCompare(b.codigo, 'es'))
  })

  return semestres
}

function extraerCursosPlanos(datos) {
  if (Array.isArray(datos)) return datos.filter(esObjeto)
  if (!esObjeto(datos)) return []

  const claves = ['cursos', 'malla', 'malla_curricular']
  for (const clave of claves) {
    if (Array.isArray(datos[clave])) return datos[clave].filter(esObjeto)
  }

  if (esObjeto(datos.datos)) return extraerCursosPlanos(datos.datos)

  return []
}

function normalizarMalla(datos) {
  const base = esObjeto(datos) ? datos : {}
  const programas = listaTextos(base.programas)
  const programa = textoCampo(base.programa)

  if (programa && !programas.some((item) => item.toLowerCase() === programa.toLowerCase())) {
    programas.unshift(programa)
  }

  let semestres = []

  if (Array.isArray(base.semestres)) {
    semestres = base.semestres
      .filter(esObjeto)
      .map((grupo, indice) => {
        const cursos = Array.isArray(grupo.cursos)
          ? grupo.cursos.filter(esObjeto).map(normalizarCurso)
          : []

        return {
          clave: `${texto(grupo.semestre) || 'sin-semestre'}-${indice}`,
          semestre: grupo.semestre,
          cursos,
        }
      })
      .filter((grupo) => grupo.cursos.length > 0)
      .sort((a, b) => compararSemestres(a.semestre, b.semestre))
  } else {
    semestres = agruparCursos(
      extraerCursosPlanos(Array.isArray(datos) ? datos : base),
    )
  }

  const totalCursos = semestres.reduce(
    (acumulado, grupo) => acumulado + grupo.cursos.length,
    0,
  )
  const total =
    Number.isFinite(Number(base.total)) && base.total !== null && base.total !== ''
      ? Number(base.total)
      : totalCursos

  // El formato de lista plana guarda ``programa`` en cada curso; si la
  // respuesta no trae un arreglo de programas, se deducen de los cursos.
  const programasFinal = listaTextos([
    ...programas,
    ...semestres.flatMap((grupo) =>
      grupo.cursos.map((curso) => curso.programa),
    ),
  ])

  return { programa, programas: programasFinal, total, semestres }
}

export default function MallaTab() {
  const [datos, setDatos] = useState({
    programa: '',
    programas: [],
    total: 0,
    semestres: [],
  })
  const [abiertos, setAbiertos] = useState({})
  const [cargando, setCargando] = useState(true)
  const [error, setError] = useState(null)
  const [recarga, setRecarga] = useState(0)

  useEffect(() => {
    let vigente = true

    setCargando(true)
    setError(null)

    apiGet('/api/admin/malla')
      .then((respuesta) => {
        if (!vigente) return

        const normalizada = normalizarMalla(respuesta)
        setDatos(normalizada)

        // El primer semestre queda expandido por defecto.
        const primero = normalizada.semestres[0]
        setAbiertos(primero ? { [primero.clave]: true } : {})
      })
      .catch((err) => {
        if (!vigente) return

        const mensaje =
          err && typeof err.message === 'string' ? err.message.trim() : ''
        setError(
          mensaje ||
            'No se pudo cargar la malla curricular. Revisa tu conexión e inténtalo de nuevo.',
        )
      })
      .finally(() => {
        if (vigente) setCargando(false)
      })

    return () => {
      vigente = false
    }
  }, [recarga])

  function recargar() {
    if (cargando) return
    setRecarga((actual) => actual + 1)
  }

  function alternar(clave) {
    setAbiertos((anteriores) => ({
      ...anteriores,
      [clave]: !anteriores[clave],
    }))
  }

  function expandirTodo() {
    const todos = {}
    datos.semestres.forEach((grupo) => {
      todos[grupo.clave] = true
    })
    setAbiertos(todos)
  }

  function contraerTodo() {
    setAbiertos({})
  }

  const haySemestres = datos.semestres.length > 0
  const todosAbiertos =
    haySemestres && datos.semestres.every((grupo) => abiertos[grupo.clave])

  return (
    <section className="space-y-4" aria-busy={cargando}>
      <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <span
              aria-hidden="true"
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-indigo-50 text-xl ring-1 ring-inset ring-indigo-100"
            >
              🎓
            </span>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-lg font-semibold text-gray-800">
                  Malla curricular
                </h2>
                {datos.total > 0 && (
                  <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-semibold text-indigo-700">
                    {datos.total}{' '}
                    {datos.total === 1 ? 'curso' : 'cursos'}
                  </span>
                )}
                {haySemestres && (
                  <span className="rounded-full bg-sky-50 px-2.5 py-0.5 text-xs font-semibold text-sky-700">
                    {datos.semestres.length}{' '}
                    {datos.semestres.length === 1 ? 'semestre' : 'semestres'}
                  </span>
                )}
                {cargando && haySemestres && (
                  <Spinner
                    label={null}
                    className="h-3.5 w-3.5 text-indigo-500"
                  />
                )}
              </div>
              <p className="mt-1 max-w-2xl text-sm text-gray-500">
                Consulta los cursos por semestre, con sus créditos, horas y
                prerrequisitos. Pulsa un semestre para expandirlo o contraerlo.
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {haySemestres && (
              <>
                <button
                  type="button"
                  onClick={todosAbiertos ? contraerTodo : expandirTodo}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-200 bg-white px-3 py-2 text-sm font-medium text-indigo-700 transition-colors hover:bg-indigo-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300"
                >
                  <span aria-hidden="true">{todosAbiertos ? '▴' : '▾'}</span>
                  {todosAbiertos ? 'Contraer todo' : 'Expandir todo'}
                </button>
                <button
                  type="button"
                  onClick={recargar}
                  disabled={cargando}
                  className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 transition-colors hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {cargando && <Spinner label={null} className="h-3.5 w-3.5" />}
                  {cargando ? 'Recargando…' : 'Recargar'}
                </button>
              </>
            )}
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-gray-100 pt-4">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">
            {datos.programas.length === 1 ? 'Programa' : 'Programas'}
          </span>
          {datos.programas.length > 0 ? (
            datos.programas.map((programa) => (
              <span
                key={programa}
                className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-violet-200 bg-violet-50 px-3 py-1 text-xs font-semibold text-violet-700"
                title={programa}
              >
                <span aria-hidden="true">🏛️</span>
                <span className="truncate">{programa}</span>
              </span>
            ))
          ) : (
            <span className="text-xs text-gray-400">
              El servidor no especificó el programa.
            </span>
          )}
        </div>
      </div>

      {error && (
        <Banner
          tipo="error"
          titulo="No se pudo cargar la malla curricular"
          accion={
            <button
              type="button"
              onClick={recargar}
              disabled={cargando}
              className="rounded-md border border-red-300 bg-white px-3 py-1 text-xs font-semibold text-red-700 transition-colors hover:bg-red-50 disabled:opacity-50"
            >
              Reintentar
            </button>
          }
        >
          {error}
        </Banner>
      )}

      {cargando && !haySemestres && !error ? (
        <div className="rounded-2xl border border-gray-200 bg-white p-10 shadow-sm">
          <Spinner
            label="Cargando la malla curricular…"
            className="h-8 w-8"
            centrado
          />
        </div>
      ) : !haySemestres && !error ? (
        <div className="rounded-2xl border border-dashed border-gray-300 bg-white px-4 py-12 text-center shadow-sm">
          <p className="text-3xl" aria-hidden="true">
            📚
          </p>
          <p className="mt-2 font-medium text-gray-700">
            Todavía no hay cursos en la malla.
          </p>
          <p className="mt-1 text-sm text-gray-500">
            Sube el PDF de la malla curricular en «Carga de Archivos» y vuelve a
            consultar esta pestaña.
          </p>
          <button
            type="button"
            onClick={recargar}
            disabled={cargando}
            className="mt-4 inline-flex items-center gap-2 rounded-lg border border-indigo-200 bg-white px-3.5 py-2 text-sm font-semibold text-indigo-700 transition-colors hover:bg-indigo-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300 disabled:opacity-50"
          >
            {cargando && <Spinner label={null} className="h-3.5 w-3.5" />}
            Reintentar
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {datos.semestres.map((grupo, indice) => {
            const abierto = Boolean(abiertos[grupo.clave])
            const idPanel = `malla-semestre-${indice}`

            return (
              <div
                key={grupo.clave}
                className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm"
              >
                <h3>
                  <button
                    type="button"
                    onClick={() => alternar(grupo.clave)}
                    aria-expanded={abierto}
                    aria-controls={idPanel}
                    className={`flex w-full items-center gap-3 px-4 py-4 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-300 sm:px-5 ${
                      abierto
                        ? 'bg-indigo-50/60 hover:bg-indigo-50'
                        : 'bg-white hover:bg-gray-50'
                    }`}
                  >
                    <span
                      aria-hidden="true"
                      className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-bold ${
                        abierto
                          ? 'bg-indigo-600 text-white'
                          : 'bg-gray-100 text-gray-500'
                      }`}
                    >
                      {indice + 1}
                    </span>

                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-semibold text-gray-800 sm:text-base">
                        {nombreSemestre(grupo.semestre)}
                      </span>
                      <span className="mt-0.5 block text-xs text-gray-500">
                        {grupo.cursos.length}{' '}
                        {grupo.cursos.length === 1 ? 'curso' : 'cursos'}
                      </span>
                    </span>

                    <span className="flex shrink-0 items-center gap-2">
                      <span className="hidden rounded-full bg-white px-2.5 py-0.5 text-xs font-semibold text-indigo-700 ring-1 ring-inset ring-indigo-200 sm:inline-flex">
                        {grupo.cursos.length}
                      </span>
                      <span
                        aria-hidden="true"
                        className={`text-sm text-gray-400 transition-transform ${
                          abierto ? 'rotate-180' : ''
                        }`}
                      >
                        ▾
                      </span>
                    </span>
                  </button>
                </h3>

                {abierto && (
                  <div
                    id={idPanel}
                    className="border-t border-gray-100 bg-slate-50/60 p-4 sm:p-5"
                  >
                    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                      {grupo.cursos.map((curso) => (
                        <article
                          key={curso._key}
                          className="flex h-full flex-col rounded-xl border border-gray-200 bg-white p-4 shadow-sm transition-colors hover:border-indigo-200"
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="inline-flex rounded-md bg-indigo-50 px-2 py-0.5 font-mono text-[11px] font-semibold uppercase tracking-wide text-indigo-700 ring-1 ring-inset ring-indigo-100">
                              {curso.codigo}
                            </span>
                            <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-semibold">
                              <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-emerald-700 ring-1 ring-inset ring-emerald-100">
                                Créditos: {curso.creditos}
                              </span>
                              <span className="rounded-full bg-sky-50 px-2 py-0.5 text-sky-700 ring-1 ring-inset ring-sky-100">
                                Horas: {curso.horas}
                              </span>
                            </div>
                          </div>

                          <h4 className="mt-2 text-sm font-semibold leading-snug text-gray-800">
                            {curso.nombre}
                          </h4>

                          <div className="mt-auto pt-3">
                            <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                              Prerrequisitos
                            </p>
                            {curso.prerrequisitos.length > 0 ? (
                              <ul className="mt-1.5 flex flex-wrap gap-1.5">
                                {curso.prerrequisitos.map(
                                  (prerrequisito, posicion) => (
                                    <li
                                      key={`${prerrequisito}-${posicion}`}
                                      className="inline-flex max-w-full rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-800"
                                      title={prerrequisito}
                                    >
                                      <span className="truncate">
                                        {prerrequisito}
                                      </span>
                                    </li>
                                  ),
                                )}
                              </ul>
                            ) : (
                              <p className="mt-1 text-xs text-gray-400">
                                Ninguno
                              </p>
                            )}
                          </div>
                        </article>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
