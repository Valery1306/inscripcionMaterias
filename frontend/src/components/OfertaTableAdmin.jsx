import { useEffect, useMemo, useState } from 'react'
import Banner from './Banner.jsx'
import Spinner from './Spinner.jsx'

// Tabla editable de la oferta académica para el administrador (GET/PUT
// /api/admin/oferta). El estado de las filas vive en AdminDashboard; aquí solo
// se editan las celdas, se busca, se pagina y se recarga.
//
// `soloLectura` permite reutilizar la tabla como vista de consulta: en ese
// modo no se renderizan inputs de edición (los cambios se siguen guardando
// desde el dashboard cuando soloLectura=false).

const FILAS_POR_PAGINA = 10

const CLASE_INPUT =
  'w-full rounded-md border border-transparent bg-transparent px-2 py-1.5 text-sm text-gray-800 transition-colors placeholder:text-gray-300 hover:border-gray-200 focus:border-indigo-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-200 disabled:cursor-not-allowed disabled:opacity-60'

const CLASE_TH =
  'sticky top-0 z-10 border-b border-gray-200 bg-gray-50/95 px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-gray-500 backdrop-blur'

const CLASE_BOTON_PAGINACION =
  'inline-flex items-center justify-center gap-1 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300 disabled:cursor-not-allowed disabled:opacity-40'

// Campos que participan en la búsqueda. Se incluyen variantes opcionales
// (curso, modalidad y facultad) por si el backend las envía; si no existen,
// simplemente no aportan coincidencias y nunca se inventa una columna.
const CAMPOS_BUSQUEDA = [
  'id_nrc',
  'nrc',
  'curso_id',
  'curso',
  'materia',
  'docente_nombre',
  'sede_nombre',
  'jornada',
  'horarioTexto',
  'modalidad',
  'facultad',
  'facultad_nombre',
  'nombre_facultad',
]

function texto(valor) {
  return valor == null ? '' : String(valor)
}

function valorNumerico(valor) {
  if (valor == null || valor === '') return null
  const numero = Number(valor)
  return Number.isFinite(numero) ? numero : null
}

function claseSaldo(valor) {
  const numero = valorNumerico(valor)
  if (numero == null) return 'text-gray-700'
  return numero < 0 ? 'text-red-600' : 'text-emerald-700'
}

// Celda de solo lectura con el mismo ritmo visual que los inputs.
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

export default function OfertaTableAdmin({
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

  // Al cambiar la búsqueda se vuelve a la primera página y, si la lista se
  // reduce, la página actual se ajusta a la última página válida.
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
            📋
          </span>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-semibold text-gray-800">
                Oferta académica
              </h2>
              <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700">
                {total} NRC
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
                ? 'Consulta la oferta cargada: NRC, curso, docente, sede, jornada, cupos y horario.'
                : 'Edita los datos del NRC, el cupo, los inscritos, el saldo y el horario. El saldo se recalcula al cambiar cupo o inscritos.'}
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label className="relative w-full sm:w-80">
            <span className="sr-only">Buscar en la oferta académica</span>
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
              placeholder="Buscar NRC, materia, docente, sede o jornada…"
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
            titulo="No se pudo cargar la oferta académica"
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
          label="Cargando la oferta académica…"
          className="h-8 w-8"
          centrado
        />
      ) : total === 0 ? (
        error ? null : (
          <div className="rounded-xl border border-dashed border-gray-300 bg-gray-50 px-4 py-10 text-center">
            <p className="text-3xl" aria-hidden="true">
              📋
            </p>
            <p className="mt-2 font-medium text-gray-700">
              Todavía no hay NRC en la oferta.
            </p>
            <p className="mt-1 text-sm text-gray-500">
              Sube el Excel/CSV de la oferta y pulsa «Recargar».
            </p>
          </div>
        )
      ) : totalFiltrados === 0 ? (
        <div className="rounded-xl border border-dashed border-gray-300 bg-gray-50 px-4 py-8 text-center text-sm text-gray-500">
          Ningún NRC coincide con «{busqueda.trim()}».
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-gray-200">
          <div className="max-h-[65vh] overflow-auto">
            <table className="w-full min-w-[1400px] border-collapse text-sm">
              <caption className="sr-only">
                Oferta académica: NRC, curso, materia, docente, sede, jornada,
                cupos, inscritos, saldo y horario.
              </caption>
              <thead>
                <tr>
                  <th scope="col" className={`${CLASE_TH} w-24`}>
                    NRC
                  </th>
                  <th scope="col" className={`${CLASE_TH} w-28`}>
                    Curso
                  </th>
                  <th scope="col" className={`${CLASE_TH} min-w-[200px]`}>
                    Materia
                  </th>
                  <th scope="col" className={`${CLASE_TH} min-w-[170px]`}>
                    Docente
                  </th>
                  <th scope="col" className={`${CLASE_TH} min-w-[130px]`}>
                    Sede
                  </th>
                  <th scope="col" className={`${CLASE_TH} w-32`}>
                    Jornada
                  </th>
                  <th scope="col" className={`${CLASE_TH} w-20 text-center`}>
                    Cupo
                  </th>
                  <th scope="col" className={`${CLASE_TH} w-24 text-center`}>
                    Inscritos
                  </th>
                  <th scope="col" className={`${CLASE_TH} w-20 text-center`}>
                    Saldo
                  </th>
                  <th scope="col" className={`${CLASE_TH} min-w-[250px]`}>
                    Día/Horas
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
                            valor={fila.id_nrc}
                            className="font-mono font-semibold text-gray-800"
                          />
                        </td>
                        <td className="px-1.5 py-1.5">
                          <CeldaLectura
                            valor={fila.curso_id}
                            className="font-mono uppercase"
                          />
                        </td>
                        <td className="px-1.5 py-1.5">
                          <CeldaLectura valor={fila.materia} />
                        </td>
                        <td className="px-1.5 py-1.5">
                          <CeldaLectura valor={fila.docente_nombre} />
                        </td>
                        <td className="px-1.5 py-1.5">
                          <CeldaLectura valor={fila.sede_nombre} />
                          {(fila.facultad ||
                            fila.facultad_nombre ||
                            fila.nombre_facultad) && (
                            <p
                              className="truncate px-2 text-[11px] text-gray-400"
                              title={
                                fila.facultad ||
                                fila.facultad_nombre ||
                                fila.nombre_facultad
                              }
                            >
                              {fila.facultad ||
                                fila.facultad_nombre ||
                                fila.nombre_facultad}
                            </p>
                          )}
                        </td>
                        <td className="px-1.5 py-1.5">
                          <CeldaLectura valor={fila.jornada} />
                          {fila.modalidad && (
                            <p className="truncate px-2 text-[11px] text-gray-400">
                              {fila.modalidad}
                            </p>
                          )}
                        </td>
                        <td className="px-1.5 py-1.5">
                          <CeldaLectura
                            valor={fila.cupo_max}
                            className="text-right tabular-nums"
                          />
                        </td>
                        <td className="px-1.5 py-1.5">
                          <CeldaLectura
                            valor={fila.inscritos}
                            className="text-right tabular-nums"
                          />
                        </td>
                        <td className="px-1.5 py-1.5">
                          <CeldaLectura
                            valor={fila.saldo}
                            className={`text-right font-semibold tabular-nums ${claseSaldo(
                              fila.saldo,
                            )}`}
                          />
                        </td>
                        <td className="px-1.5 py-1.5">
                          <CeldaLectura valor={fila.horarioTexto} />
                        </td>
                      </>
                    ) : (
                      <>
                        <td className="px-2 py-1.5">
                          <input
                            type="text"
                            inputMode="numeric"
                            value={fila.id_nrc ?? ''}
                            onChange={(evento) =>
                              onEditar?.(
                                indice,
                                'id_nrc',
                                evento.target.value,
                              )
                            }
                            disabled={deshabilitado}
                            aria-label={`NRC de la fila ${indice + 1}`}
                            className={`${CLASE_INPUT} font-mono`}
                          />
                        </td>

                        <td className="px-2 py-1.5">
                          <input
                            type="text"
                            value={fila.curso_id ?? ''}
                            onChange={(evento) =>
                              onEditar?.(
                                indice,
                                'curso_id',
                                evento.target.value,
                              )
                            }
                            disabled={deshabilitado}
                            aria-label={`Código del curso del NRC ${fila.id_nrc || indice + 1}`}
                            className={`${CLASE_INPUT} font-mono uppercase`}
                          />
                        </td>

                        <td className="px-2 py-1.5">
                          <input
                            type="text"
                            value={fila.materia ?? ''}
                            onChange={(evento) =>
                              onEditar?.(
                                indice,
                                'materia',
                                evento.target.value,
                              )
                            }
                            disabled={deshabilitado}
                            aria-label={`Materia del NRC ${fila.id_nrc || indice + 1}`}
                            className={CLASE_INPUT}
                          />
                        </td>

                        <td className="px-2 py-1.5">
                          <input
                            type="text"
                            value={fila.docente_nombre ?? ''}
                            onChange={(evento) =>
                              onEditar?.(
                                indice,
                                'docente_nombre',
                                evento.target.value,
                              )
                            }
                            disabled={deshabilitado}
                            aria-label={`Docente del NRC ${fila.id_nrc || indice + 1}`}
                            className={CLASE_INPUT}
                          />
                        </td>

                        <td className="px-2 py-1.5">
                          <input
                            type="text"
                            value={fila.sede_nombre ?? ''}
                            onChange={(evento) =>
                              onEditar?.(
                                indice,
                                'sede_nombre',
                                evento.target.value,
                              )
                            }
                            disabled={deshabilitado}
                            aria-label={`Sede del NRC ${fila.id_nrc || indice + 1}`}
                            className={CLASE_INPUT}
                          />
                          {(fila.facultad ||
                            fila.facultad_nombre ||
                            fila.nombre_facultad) && (
                            <p className="truncate px-2 text-[11px] text-gray-400">
                              {fila.facultad ||
                                fila.facultad_nombre ||
                                fila.nombre_facultad}
                            </p>
                          )}
                        </td>

                        <td className="px-2 py-1.5">
                          <input
                            type="text"
                            value={fila.jornada ?? ''}
                            onChange={(evento) =>
                              onEditar?.(
                                indice,
                                'jornada',
                                evento.target.value,
                              )
                            }
                            disabled={deshabilitado}
                            aria-label={`Jornada del NRC ${fila.id_nrc || indice + 1}`}
                            className={CLASE_INPUT}
                          />
                          {fila.modalidad && (
                            <p className="px-2 text-[11px] text-gray-400">
                              {fila.modalidad}
                            </p>
                          )}
                        </td>

                        <td className="px-2 py-1.5">
                          <input
                            type="number"
                            min="0"
                            step="1"
                            value={fila.cupo_max ?? ''}
                            onChange={(evento) =>
                              onEditar?.(
                                indice,
                                'cupo_max',
                                evento.target.value,
                              )
                            }
                            disabled={deshabilitado}
                            aria-label={`Cupo máximo del NRC ${fila.id_nrc || indice + 1}`}
                            className={`${CLASE_INPUT} text-right tabular-nums`}
                          />
                        </td>

                        <td className="px-2 py-1.5">
                          <input
                            type="number"
                            min="0"
                            step="1"
                            value={fila.inscritos ?? ''}
                            onChange={(evento) =>
                              onEditar?.(
                                indice,
                                'inscritos',
                                evento.target.value,
                              )
                            }
                            disabled={deshabilitado}
                            aria-label={`Inscritos del NRC ${fila.id_nrc || indice + 1}`}
                            className={`${CLASE_INPUT} text-right tabular-nums`}
                          />
                        </td>

                        <td className="px-2 py-1.5">
                          <input
                            type="number"
                            step="1"
                            value={fila.saldo ?? ''}
                            onChange={(evento) =>
                              onEditar?.(
                                indice,
                                'saldo',
                                evento.target.value,
                              )
                            }
                            disabled={deshabilitado}
                            title="Se recalcula automáticamente al cambiar el cupo o los inscritos. Puede ser negativo si hay sobrecupo."
                            aria-label={`Saldo del NRC ${fila.id_nrc || indice + 1}`}
                            className={`${CLASE_INPUT} text-right tabular-nums`}
                          />
                        </td>

                        <td className="px-2 py-1.5">
                          <input
                            type="text"
                            value={fila.horarioTexto ?? ''}
                            onChange={(evento) =>
                              onEditar?.(
                                indice,
                                'horarioTexto',
                                evento.target.value,
                              )
                            }
                            disabled={deshabilitado}
                            placeholder="Lun 07:00-09:00, Mié 09:00-11:00"
                            title="Formato: Lun 07:00-09:00, Mié 09:00-11:00. Déjalo vacío si no hay horario."
                            aria-label={`Día y horas del NRC ${fila.id_nrc || indice + 1}`}
                            className={CLASE_INPUT}
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
            NRC
            {totalFiltrados !== total && ` (filtrados de ${total})`}
            {!soloLectura && ' · Los cambios se guardan con «Guardar Cambios».'}
          </p>

          <nav
            aria-label="Paginación de la oferta académica"
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

      {!soloLectura && totalFiltrados > 0 && (
        <p className="mt-3 text-xs text-gray-400">
          Día/Horas: usa «Lun 07:00-09:00, Mié 09:00-11:00». Los salones y
          edificios se conservan al guardar.
        </p>
      )}
    </section>
  )
}
