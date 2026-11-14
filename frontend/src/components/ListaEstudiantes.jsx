import { useEffect, useMemo, useState } from 'react'
import Banner from './Banner.jsx'
import Spinner from './Spinner.jsx'
import { apiGet, listaDeObjetos } from '../api.js'

// ---------------------------------------------------------------------------
// Pestaña «Lista de Estudiantes» (solo lectura).
//
// GET /api/admin/estudiantes -> tabla informativa con el estado académico de
// cada estudiante. No hay edición ni acciones por fila: cualquier cambio de
// perfil se hace desde el portal del estudiante.
// ---------------------------------------------------------------------------

const CLASE_TH =
  'sticky top-0 z-10 border-b border-gray-200 bg-gray-50 px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-gray-500'

function esObjeto(valor) {
  return Boolean(valor) && typeof valor === 'object' && !Array.isArray(valor)
}

function texto(valor) {
  if (valor == null) return ''
  return String(valor).trim()
}

function sinAcentos(valor) {
  return texto(valor)
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
}

function aEntero(valor) {
  if (typeof valor === 'number' && Number.isFinite(valor)) return Math.trunc(valor)
  if (typeof valor === 'string' && valor.trim() !== '') {
    const numero = Number(valor)
    if (Number.isFinite(numero)) return Math.trunc(numero)
  }
  return null
}

function extraerLista(datos) {
  if (Array.isArray(datos)) return listaDeObjetos(datos)

  if (esObjeto(datos)) {
    const claves = ['estudiantes', 'items', 'datos', 'filas', 'rows']
    for (const clave of claves) {
      if (Array.isArray(datos[clave])) return listaDeObjetos(datos[clave])
    }
  }

  return []
}

function normalizarEstudiantes(datos) {
  return extraerLista(datos).map((item, indice) => ({
    ...item,
    _key: `estudiante-${texto(item.id) || String(indice)}-${String(indice)}`,
    id: item.id ?? '',
    nombre: texto(item.nombre),
    plan_id: item.plan_id ?? '',
    programa: texto(item.programa),
    jornada: texto(item.jornada),
    sede: texto(item.sede),
    semestre: item.semestre ?? '',
    total_aprobadas: aEntero(
      item.total_aprobadas ?? item.totalAprobadas ?? item.aprobadas,
    ),
    total_por_ver: aEntero(
      item.total_por_ver ?? item.totalPorVer ?? item.por_ver,
    ),
    tiene_perfil:
      typeof item.tiene_perfil === 'boolean' ? item.tiene_perfil : null,
  }))
}

function valorVisible(valor) {
  const cadena = texto(valor)
  return cadena || '—'
}

function ChipNumero({ valor, tono }) {
  const estilos =
    tono === 'emerald'
      ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
      : 'border-amber-200 bg-amber-50 text-amber-700'

  return (
    <span
      className={`inline-flex min-w-[2.25rem] items-center justify-center rounded-full border px-2 py-0.5 text-xs font-bold ${estilos}`}
    >
      {valor == null ? '—' : valor}
    </span>
  )
}

export default function ListaEstudiantes() {
  const [estudiantes, setEstudiantes] = useState([])
  const [cargando, setCargando] = useState(true)
  const [error, setError] = useState(null)
  const [recarga, setRecarga] = useState(0)
  const [busqueda, setBusqueda] = useState('')

  useEffect(() => {
    let vigente = true

    setCargando(true)
    setError(null)

    apiGet('/api/admin/estudiantes')
      .then((datos) => {
        if (!vigente) return
        setEstudiantes(normalizarEstudiantes(datos))
      })
      .catch((err) => {
        if (!vigente) return
        setError(
          texto(err && err.message) ||
            'No se pudo cargar la lista de estudiantes. Inténtalo de nuevo.',
        )
      })
      .finally(() => {
        if (vigente) setCargando(false)
      })

    return () => {
      vigente = false
    }
  }, [recarga])

  const visibles = useMemo(() => {
    const consulta = sinAcentos(busqueda).toLowerCase().trim()
    if (!consulta) return estudiantes

    return estudiantes.filter((item) =>
      [
        item.id,
        item.nombre,
        item.plan_id,
        item.programa,
        item.jornada,
        item.sede,
        item.semestre,
      ].some((valor) => sinAcentos(valor).toLowerCase().includes(consulta)),
    )
  }, [estudiantes, busqueda])

  function recargar() {
    if (cargando) return
    setRecarga((actual) => actual + 1)
  }

  const total = estudiantes.length
  const hayDatos = total > 0

  return (
    <section
      aria-labelledby="lista-estudiantes-titulo"
      className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm sm:p-5"
    >
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2
              id="lista-estudiantes-titulo"
              className="text-lg font-semibold text-gray-800"
            >
              Lista de estudiantes
            </h2>
            <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-semibold text-indigo-700">
              {total} {total === 1 ? 'estudiante' : 'estudiantes'}
            </span>
            <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-gray-500">
              Solo lectura
            </span>
            {cargando && hayDatos && (
              <Spinner label={null} className="h-3.5 w-3.5 text-indigo-500" />
            )}
          </div>
          <p className="mt-1 max-w-2xl text-sm text-gray-500">
            Consulta el plan, el programa y el avance académico de los
            estudiantes registrados. Esta vista no permite editar datos.
          </p>
        </div>

        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
          <label className="relative w-full sm:w-72">
            <span className="sr-only">Buscar estudiantes</span>
            <span
              aria-hidden="true"
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-gray-400"
            >
              🔍
            </span>
            <input
              type="search"
              value={busqueda}
              onChange={(evento) => setBusqueda(evento.target.value)}
              placeholder="Buscar por ID, nombre, programa…"
              className="w-full rounded-lg border border-gray-300 py-2 pl-9 pr-9 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200"
            />
            {busqueda && (
              <button
                type="button"
                onClick={() => setBusqueda('')}
                aria-label="Limpiar la búsqueda"
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded px-1.5 text-base leading-none text-gray-400 hover:text-gray-600"
              >
                ×
              </button>
            )}
          </label>

          <button
            type="button"
            onClick={recargar}
            disabled={cargando}
            className="inline-flex items-center gap-2 rounded-md border border-indigo-200 bg-white px-3 py-1.5 text-sm font-medium text-indigo-700 transition-colors hover:bg-indigo-50 disabled:opacity-50"
          >
            {cargando ? (
              <Spinner label={null} className="h-3.5 w-3.5" />
            ) : (
              <span aria-hidden="true">↻</span>
            )}
            {cargando ? 'Recargando…' : 'Recargar'}
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-3">
          <Banner
            tipo="error"
            titulo="No se pudo cargar la lista de estudiantes"
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
        </div>
      )}

      {cargando && !hayDatos ? (
        <Spinner
          label="Cargando la lista de estudiantes…"
          className="h-8 w-8"
          centrado
        />
      ) : !hayDatos ? (
        error ? null : (
          <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-10 text-center">
            <p className="text-3xl" aria-hidden="true">
              👥
            </p>
            <p className="mt-2 font-medium text-gray-700">
              Todavía no hay estudiantes registrados.
            </p>
            <p className="mt-1 text-sm text-gray-500">
              Los perfiles aparecen aquí cuando un estudiante completa sus
              datos.
            </p>
          </div>
        )
      ) : visibles.length === 0 ? (
        <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-8 text-center text-sm text-gray-500">
          Ningún estudiante coincide con «{busqueda.trim()}». Prueba con otro
          nombre, ID o programa.
        </div>
      ) : (
        <div className="max-h-[65vh] overflow-auto rounded-lg border border-gray-200">
          <table className="w-full min-w-[1050px] border-collapse text-sm">
            <caption className="sr-only">
              Lista de estudiantes: ID, nombre, plan, programa, jornada, sede,
              semestre, materias aprobadas y materias por ver. Solo lectura.
            </caption>
            <thead>
              <tr>
                <th scope="col" className={`${CLASE_TH} w-20`}>
                  ID
                </th>
                <th scope="col" className={`${CLASE_TH} min-w-[190px]`}>
                  Nombre
                </th>
                <th scope="col" className={`${CLASE_TH} w-24`}>
                  Plan
                </th>
                <th scope="col" className={`${CLASE_TH} min-w-[210px]`}>
                  Programa
                </th>
                <th scope="col" className={`${CLASE_TH} min-w-[110px]`}>
                  Jornada
                </th>
                <th scope="col" className={`${CLASE_TH} min-w-[120px]`}>
                  Sede
                </th>
                <th scope="col" className={`${CLASE_TH} w-24 text-center`}>
                  Semestre
                </th>
                <th scope="col" className={`${CLASE_TH} w-28 text-center`}>
                  Aprobadas
                </th>
                <th scope="col" className={`${CLASE_TH} w-24 text-center`}>
                  Por ver
                </th>
              </tr>
            </thead>
            <tbody>
              {visibles.map((item) => (
                <tr
                  key={item._key}
                  className="border-b border-gray-100 align-middle last:border-0 hover:bg-indigo-50/30"
                >
                  <td className="px-3 py-2.5 font-mono text-xs font-semibold text-gray-700">
                    {valorVisible(item.id)}
                  </td>

                  <td className="px-3 py-2.5">
                    <p className="font-medium text-gray-800">
                      {valorVisible(item.nombre)}
                    </p>
                    {item.tiene_perfil === false && (
                      <span className="mt-0.5 inline-flex rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-700">
                        Perfil pendiente
                      </span>
                    )}
                  </td>

                  <td className="px-3 py-2.5 font-mono text-xs text-gray-600">
                    {valorVisible(item.plan_id)}
                  </td>

                  <td className="px-3 py-2.5 text-gray-700">
                    {valorVisible(item.programa)}
                  </td>

                  <td className="px-3 py-2.5 text-gray-600">
                    {valorVisible(item.jornada)}
                  </td>

                  <td className="px-3 py-2.5 text-gray-600">
                    {valorVisible(item.sede)}
                  </td>

                  <td className="px-3 py-2.5 text-center text-gray-700">
                    {valorVisible(item.semestre)}
                  </td>

                  <td className="px-3 py-2.5 text-center">
                    <ChipNumero valor={item.total_aprobadas} tono="emerald" />
                  </td>

                  <td className="px-3 py-2.5 text-center">
                    <ChipNumero valor={item.total_por_ver} tono="amber" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {hayDatos && (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-gray-400">
          <p>
            {visibles.length !== total
              ? `Mostrando ${visibles.length} de ${total} estudiantes. `
              : ''}
            Esta vista es informativa y de solo lectura.
          </p>
          <p>Los datos se actualizan al pulsar «Recargar».</p>
        </div>
      )}
    </section>
  )
}
