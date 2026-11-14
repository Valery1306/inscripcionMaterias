import { useEffect, useMemo, useState } from 'react'
import Banner from './Banner.jsx'
import Spinner from './Spinner.jsx'

// Tabla editable de la malla curricular. El estado de las filas vive en
// AdminDashboard; aquí solo se editan las celdas, se busca, se pagina y se
// recarga. `soloLectura` permite usarla como vista de consulta sin inputs.

const FILAS_POR_PAGINA = 10

const CLASE_INPUT =
  'w-full rounded-md border border-transparent bg-transparent px-2 py-1.5 text-sm text-gray-800 transition-colors placeholder:text-gray-300 hover:border-gray-200 focus:border-indigo-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-200 disabled:cursor-not-allowed disabled:opacity-60'

const CLASE_TH =
  'sticky top-0 z-10 border-b border-gray-200 bg-gray-50/95 px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-gray-500 backdrop-blur'

const CLASE_BOTON_PAGINACION =
  'inline-flex items-center justify-center gap-1 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300 disabled:cursor-not-allowed disabled:opacity-40'

const CAMPOS_BUSQUEDA = [
  'semestre',
  'codigo',
  'nombre',
  'prerrequisitosTexto',
  'programa',
  'sede',
]

function texto(valor) {
  return valor == null ? '' : String(valor)
}

function CeldaLectura({ valor, className = '', title }) {
  const contenido = texto(valor)
  return (
    <span
      className={`block px-2 py-1.5 text-sm leading-snug text-gray-700 ${className}`}
      title={title || contenido || undefined}
    >
      {contenido || <span className="text-gray-300">—</span>}
    </span>
  )
}

export default function MallaTable({
  filas = [],
  cargando = false,
  error = null,
  deshabilitado = false,
  soloLectura = false,
  onEditar,
  onRecargar,
}) {
  const [busqueda, setBusqueda] = useState('')
  const [pagina, setPagina] = useState(1)

  const filtradas = useMemo(() => {
    const consulta = busqueda.trim().toLowerCase()

    return filas
      .map((fila, indice) => ({ fila, indice }))
      .filter(({ fila }) => {
        if (!consulta) return true

        return CAMPOS_BUSQUEDA.some((campo) =>
          texto(fila[campo]).toLowerCase().includes(consulta),
        )
      })
  }, [filas, busqueda])

  const total = filas.length
  const totalFiltrados = filtradas.length
  const totalPaginas = Math.max(
    1,
    Math.ceil(totalFiltrados / FILAS_POR_PAGINA),
  )
  const paginaActual = Math.min(Math.max(1, pagina), totalPaginas)

  useEffect(() => {
    setPagina(1)
  }, [busqueda])

  useEffect(() => {
    setPagina((actual) => (actual > totalPaginas ? totalPaginas : actual))
  }, [totalPaginas])

  const visibles = useMemo(
    () =>
      filtradas.slice(
        (paginaActual - 1) * FILAS_POR_PAGINA,
        paginaActual * FILAS_POR_PAGINA,
      ),
    [filtradas, paginaActual],
  )

  const inicio =
    totalFiltrados === 0
      ? 0
      : (paginaActual - 1) * FILAS_POR_PAGINA + 1
  const fin = Math.min(paginaActual * FILAS_POR_PAGINA, totalFiltrados)

  function irAPagina(destino) {
    setPagina(Math.min(Math.max(1, destino), totalPaginas))
  }

  return (
    <section
      className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm sm:p-6"
      aria-busy={cargando}
    >
      <div className="mb-4 flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span
            aria-hidden="true"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-indigo-50 text-xl ring-1 ring-inset ring-indigo-100"
          >
            📚
          </span>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-semibold text-gray-800">
                Malla curricular
              </h2>
              <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700">
                {total} {total === 1 ? 'curso' : 'cursos'}
              </span>
              {soloLectura && (
                <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-gray-600 ring-1 ring-inset ring-gray-200">
                  Solo lectura
                </span>
              )}
              {cargando && (
                <Spinner
                  label={null}
                  className="h-3.5 w-3.5 text-indigo-500"
                />
              )}
            </div>
            <p className="mt-1 max-w-2xl text-sm text-gray-500">
              {soloLectura
                ? 'Consulta la malla cargada: semestre, código, nombre, créditos, horas y prerrequisitos.'
                : 'Edita el semestre, el código, el nombre, los créditos, las horas y los prerrequisitos (separados por comas).'}
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label className="relative w-full sm:w-72">
            <span className="sr-only">Buscar en la malla curricular</span>
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
              placeholder="Buscar curso, código, programa…"
              className={`w-full rounded-lg border border-gray-300 py-2 pl-9 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200 ${
                busqueda ? 'pr-9' : 'pr-3'
              }`}
            />
            {busqueda && (
              <button
                type="button"
                onClick={() => setBusqueda('')}
                aria-label="Limpiar búsqueda"
                className="absolute right-1.5 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-full text-base leading-none text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300"
              >
                ×
              </button>
            )}
          </label>

          <button
            type="button"
            onClick={() => onRecargar?.()}
            disabled={cargando || deshabilitado}
            className="inline-flex items-center gap-2 rounded-lg border border-indigo-200 bg-white px-3 py-2 text-sm font-medium text-indigo-700 transition-colors hover:bg-indigo-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300 disabled:opacity-50"
          >
            {cargando && <Spinner label={null} className="h-3.5 w-3.5" />}
            {cargando ? 'Recargando…' : 'Recargar'}
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-3">
          <Banner
            tipo="error"
            titulo="No se pudo cargar la malla curricular"
            accion={
              <button
                type="button"
                onClick={() => onRecargar?.()}
                disabled={cargando || deshabilitado}
                className="rounded-md border border-red-300 bg-white px-3 py-1 text-xs font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50"
              >
                Reintentar
              </button>
            }
          >
            {error}
          </Banner>
        </div>
      )}

      {cargando && total === 0 ? (
        <Spinner
          label="Cargando la malla curricular…"
          className="h-8 w-8"
          centrado
        />
      ) : total === 0 ? (
        error ? null : (
          <div className="rounded-xl border border-dashed border-gray-300 bg-gray-50 px-4 py-10 text-center">
            <p className="text-3xl" aria-hidden="true">
              📚
            </p>
            <p className="mt-2 font-medium text-gray-700">
              Todavía no hay cursos en la malla.
            </p>
            <p className="mt-1 text-sm text-gray-500">
              Sube el PDF de la malla curricular y pulsa «Recargar».
            </p>
          </div>
        )
      ) : totalFiltrados === 0 ? (
        <div className="rounded-xl border border-dashed border-gray-300 bg-gray-50 px-4 py-8 text-center text-sm text-gray-500">
          Ningún curso coincide con «{busqueda.trim()}».
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-gray-200">
          <div className="max-h-[65vh] overflow-auto">
            <table className="w-full min-w-[980px] border-collapse text-sm">
              <caption className="sr-only">
                Malla curricular: semestre, código, nombre, créditos, horas y
                prerrequisitos por curso.
              </caption>
              <thead>
                <tr>
                  <th scope="col" className={`${CLASE_TH} w-20 text-center`}>
                    Semestre
                  </th>
                  <th scope="col" className={`${CLASE_TH} w-32`}>
                    Código
                  </th>
                  <th scope="col" className={`${CLASE_TH} min-w-[220px]`}>
                    Nombre
                  </th>
                  <th scope="col" className={`${CLASE_TH} w-24 text-center`}>
                    Créditos
                  </th>
                  <th scope="col" className={`${CLASE_TH} w-20 text-center`}>
                    Horas
                  </th>
                  <th scope="col" className={`${CLASE_TH} min-w-[240px]`}>
                    Prerrequisitos
                  </th>
                </tr>
              </thead>
              <tbody>
                {visibles.map(({ fila, indice }) => (
                  <tr
                    key={fila._key ?? `fila-${indice}`}
                    className="border-b border-gray-100 align-top last:border-0 hover:bg-indigo-50/20"
                  >
                    {soloLectura ? (
                      <>
                        <td className="px-1.5 py-1.5">
                          <CeldaLectura
                            valor={fila.semestre}
                            className="text-right tabular-nums"
                          />
                        </td>
                        <td className="px-1.5 py-1.5">
                          <CeldaLectura
                            valor={fila.codigo}
                            className="font-mono uppercase"
                          />
                        </td>
                        <td className="px-1.5 py-1.5">
                          <CeldaLectura valor={fila.nombre} />
                          {(fila.programa || fila.sede) && (
                            <p
                              className="truncate px-2 text-[11px] text-gray-400"
                              title={[fila.programa, fila.sede]
                                .filter(Boolean)
                                .join(' · ')}
                            >
                              {[fila.programa, fila.sede]
                                .filter(Boolean)
                                .join(' · ')}
                            </p>
                          )}
                        </td>
                        <td className="px-1.5 py-1.5">
                          <CeldaLectura
                            valor={fila.creditos}
                            className="text-right tabular-nums"
                          />
                        </td>
                        <td className="px-1.5 py-1.5">
                          <CeldaLectura
                            valor={fila.horas}
                            className="text-right tabular-nums"
                          />
                        </td>
                        <td className="px-1.5 py-1.5">
                          <CeldaLectura valor={fila.prerrequisitosTexto} />
                        </td>
                      </>
                    ) : (
                      <>
                        <td className="px-2 py-1.5">
                          <input
                            type="number"
                            min="1"
                            step="1"
                            value={fila.semestre ?? ''}
                            onChange={(evento) =>
                              onEditar?.(
                                indice,
                                'semestre',
                                evento.target.value,
                              )
                            }
                            disabled={deshabilitado}
                            aria-label={`Semestre del curso ${fila.codigo || indice + 1}`}
                            className={`${CLASE_INPUT} text-right tabular-nums`}
                          />
                        </td>

                        <td className="px-2 py-1.5">
                          <input
                            type="text"
                            value={fila.codigo ?? ''}
                            onChange={(evento) =>
                              onEditar?.(
                                indice,
                                'codigo',
                                evento.target.value,
                              )
                            }
                            disabled={deshabilitado}
                            aria-label={`Código del curso ${indice + 1}`}
                            className={`${CLASE_INPUT} font-mono uppercase`}
                          />
                        </td>

                        <td className="px-2 py-1.5">
                          <input
                            type="text"
                            value={fila.nombre ?? ''}
                            onChange={(evento) =>
                              onEditar?.(
                                indice,
                                'nombre',
                                evento.target.value,
                              )
                            }
                            disabled={deshabilitado}
                            aria-label={`Nombre del curso ${fila.codigo || indice + 1}`}
                            className={CLASE_INPUT}
                          />
                          {(fila.programa || fila.sede) && (
                            <p
                              className="truncate px-2 text-[11px] text-gray-400"
                              title={[fila.programa, fila.sede]
                                .filter(Boolean)
                                .join(' · ')}
                            >
                              {[fila.programa, fila.sede]
                                .filter(Boolean)
                                .join(' · ')}
                            </p>
                          )}
                        </td>

                        <td className="px-2 py-1.5">
                          <input
                            type="number"
                            min="0"
                            step="1"
                            value={fila.creditos ?? ''}
                            onChange={(evento) =>
                              onEditar?.(
                                indice,
                                'creditos',
                                evento.target.value,
                              )
                            }
                            disabled={deshabilitado}
                            aria-label={`Créditos del curso ${fila.codigo || indice + 1}`}
                            className={`${CLASE_INPUT} text-right tabular-nums`}
                          />
                        </td>

                        <td className="px-2 py-1.5">
                          <input
                            type="number"
                            min="0"
                            step="1"
                            value={fila.horas ?? ''}
                            onChange={(evento) =>
                              onEditar?.(
                                indice,
                                'horas',
                                evento.target.value,
                              )
                            }
                            disabled={deshabilitado}
                            aria-label={`Horas del curso ${fila.codigo || indice + 1}`}
                            className={`${CLASE_INPUT} text-right tabular-nums`}
                          />
                        </td>

                        <td className="px-2 py-1.5">
                          <input
                            type="text"
                            value={fila.prerrequisitosTexto ?? ''}
                            onChange={(evento) =>
                              onEditar?.(
                                indice,
                                'prerrequisitosTexto',
                                evento.target.value,
                              )
                            }
                            disabled={deshabilitado}
                            placeholder="CAL-101, CAL-102"
                            title="Separa los códigos con comas. Déjalo vacío si no tiene prerrequisitos."
                            aria-label={`Prerrequisitos del curso ${fila.codigo || indice + 1}`}
                            className={`${CLASE_INPUT} placeholder:normal-case`}
                          />
                        </td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {totalFiltrados > 0 && (
        <div className="mt-4 flex flex-col gap-3 border-t border-gray-100 pt-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-gray-500">
            Mostrando{' '}
            <span className="font-semibold text-gray-700">
              {inicio}–{fin}
            </span>{' '}
            de{' '}
            <span className="font-semibold text-gray-700">
              {totalFiltrados}
            </span>{' '}
            {totalFiltrados === 1 ? 'curso' : 'cursos'}
            {totalFiltrados !== total && ` (filtrados de ${total})`}
            {!soloLectura && ' · Los cambios se guardan con «Guardar Cambios».'}
          </p>

          <nav
            aria-label="Paginación de la malla curricular"
            className="flex items-center gap-2 self-start sm:self-auto"
          >
            <button
              type="button"
              onClick={() => irAPagina(paginaActual - 1)}
              disabled={paginaActual <= 1}
              className={CLASE_BOTON_PAGINACION}
            >
              ← Anterior
            </button>
            <span
              aria-live="polite"
              className="min-w-[7.5rem] text-center text-sm font-medium text-gray-600"
            >
              Página {paginaActual} de {totalPaginas}
            </span>
            <button
              type="button"
              onClick={() => irAPagina(paginaActual + 1)}
              disabled={paginaActual >= totalPaginas}
              className={CLASE_BOTON_PAGINACION}
            >
              Siguiente →
            </button>
          </nav>
        </div>
      )}
    </section>
  )
}
