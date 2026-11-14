import { useEffect, useId, useRef, useState } from 'react'
import Banner from './Banner.jsx'
import Spinner from './Spinner.jsx'

const CLASE_SELECT =
  'mt-1 block w-full rounded-lg border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-800 shadow-sm transition focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200 disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-400'

const CLASE_INPUT_ARCHIVO =
  'mt-2 block w-full cursor-pointer rounded-lg border border-dashed border-indigo-300 bg-white px-3 py-2 text-sm text-gray-600 transition file:mr-3 file:cursor-pointer file:rounded-md file:border-0 file:bg-indigo-50 file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-indigo-700 hover:file:bg-indigo-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200 disabled:cursor-not-allowed disabled:opacity-60'

const TAMANO_MAXIMO_MATRIZ = 10 * 1024 * 1024 // 10 MB

function texto(valor) {
  return valor == null ? '' : String(valor)
}

function formatearPeso(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 KB'
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}

function placeholder(cargando, opciones) {
  if (cargando) return 'Cargando…'
  return opciones.length > 0 ? 'Selecciona una opción' : 'Sin opciones disponibles'
}

/**
 * Paso 1: datos académicos básicos y matriz de estudio opcional.
 *
 * Es un formulario controlado: el estado y la llamada al backend viven en
 * EstudiantePanel. Aquí solo se valida el archivo (extensión y tamaño) para
 * dar retroalimentación inmediata.
 */
export default function PerfilForm({
  catalogo = { programas: [], jornadas: [], sedes: [] },
  catalogoCargando = false,
  catalogoError = null,
  onReintentarCatalogo,
  valores = { programa: '', jornada: '', sede: '' },
  onCambio,
  archivo = null,
  onArchivo,
  enviando = false,
  error = null,
  onCerrarError,
  estudianteId = null,
  editando = false,
  onCancelarEdicion,
  onNuevoPerfil,
  onSubmit,
}) {
  const idBase = useId()
  const inputRef = useRef(null)
  const [errorArchivo, setErrorArchivo] = useState(null)

  const bloqueado = enviando || catalogoCargando

  // Si el padre limpia el archivo (p. ej. tras guardar), se limpia también el
  // input nativo, cuyo valor no se puede controlar desde React.
  useEffect(() => {
    if (!archivo && inputRef.current) inputRef.current.value = ''
  }, [archivo])

  function cambiar(campo, valor) {
    if (typeof onCambio === 'function') onCambio(campo, valor)
  }

  function seleccionarArchivo(evento) {
    const seleccionado =
      evento.target.files && evento.target.files[0]
        ? evento.target.files[0]
        : null

    if (!seleccionado) {
      setErrorArchivo(null)
      if (typeof onArchivo === 'function') onArchivo(null)
      return
    }

    if (!/\.(csv|xlsx|xls)$/i.test(seleccionado.name)) {
      setErrorArchivo(
        'Selecciona un archivo con extensión .csv, .xlsx o .xls.',
      )
      if (typeof onArchivo === 'function') onArchivo(null)
      evento.target.value = ''
      return
    }

    if (seleccionado.size === 0) {
      setErrorArchivo('El archivo está vacío. Selecciona otro archivo.')
      if (typeof onArchivo === 'function') onArchivo(null)
      evento.target.value = ''
      return
    }

    if (seleccionado.size > TAMANO_MAXIMO_MATRIZ) {
      setErrorArchivo(
        'El archivo supera el tamaño máximo permitido (10 MB). Elige uno más pequeño.',
      )
      if (typeof onArchivo === 'function') onArchivo(null)
      evento.target.value = ''
      return
    }

    setErrorArchivo(null)
    if (typeof onArchivo === 'function') onArchivo(seleccionado)
  }

  function quitarArchivo() {
    setErrorArchivo(null)
    if (typeof onArchivo === 'function') onArchivo(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  const botonReintentar = (
    <button
      type="button"
      onClick={onReintentarCatalogo}
      disabled={catalogoCargando}
      className="rounded-md border border-red-300 bg-white px-3 py-1 text-xs font-semibold text-red-700 transition-colors hover:bg-red-50 disabled:opacity-50"
    >
      Reintentar
    </button>
  )

  return (
    <section
      aria-labelledby={`${idBase}-titulo`}
      className="rounded-2xl border-2 border-indigo-200 bg-white p-5 shadow-sm sm:p-6"
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden="true"
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-indigo-600 text-base font-bold text-white"
        >
          1
        </span>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2
              id={`${idBase}-titulo`}
              className="text-lg font-semibold text-gray-800"
            >
              Tus datos académicos
            </h2>
            <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-indigo-700">
              Paso 1
            </span>
            {catalogoCargando && (
              <Spinner label="Cargando catálogo…" className="h-4 w-4" />
            )}
          </div>
          <p className="mt-0.5 text-sm text-gray-500">
            Selecciona tu programa, jornada y sede. Opcionalmente adjunta tu
            matriz de estudio para cargar tus materias aprobadas y por ver.
          </p>
        </div>
      </div>

      {catalogoError && (
        <div className="mt-4">
          <Banner
            tipo="error"
            titulo="No se pudo cargar el catálogo"
            accion={onReintentarCatalogo ? botonReintentar : null}
          >
            {catalogoError}
          </Banner>
        </div>
      )}

      {editando && (
        <div className="mt-4">
          <Banner
            tipo="info"
            titulo={`Actualizando el perfil del estudiante #${estudianteId}`}
            accion={
              <div className="flex flex-wrap gap-2">
                {onCancelarEdicion && (
                  <button
                    type="button"
                    onClick={onCancelarEdicion}
                    className="rounded-md border border-sky-300 bg-white px-3 py-1 text-xs font-semibold text-sky-700 transition-colors hover:bg-sky-50"
                  >
                    Cancelar
                  </button>
                )}
                {onNuevoPerfil && (
                  <button
                    type="button"
                    onClick={onNuevoPerfil}
                    title="Empezar un perfil sin el ID actual"
                    className="rounded-md border border-gray-300 bg-white px-3 py-1 text-xs font-semibold text-gray-600 transition-colors hover:bg-gray-50"
                  >
                    Perfil nuevo
                  </button>
                )}
              </div>
            }
          >
            Si guardas los cambios se reemplazará el resumen actual. Si adjuntas
            una matriz, también se actualizarán tus materias.
          </Banner>
        </div>
      )}

      {error && (
        <div className="mt-4">
          <Banner
            tipo="error"
            titulo="No se pudo guardar tu perfil"
            onCerrar={onCerrarError}
          >
            {error}
          </Banner>
        </div>
      )}

      <form
        onSubmit={onSubmit}
        noValidate
        aria-busy={enviando}
        className="mt-5 space-y-5"
      >
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <label htmlFor={`${idBase}-programa`} className="block">
            <span className="text-sm font-medium text-gray-700">
              Programa{' '}
              <span aria-hidden="true" className="text-red-500">
                *
              </span>
            </span>
            <select
              id={`${idBase}-programa`}
              value={valores.programa}
              onChange={(evento) => cambiar('programa', evento.target.value)}
              disabled={bloqueado}
              required
              className={CLASE_SELECT}
            >
              <option value="">
                {placeholder(catalogoCargando, catalogo.programas)}
              </option>
              {catalogo.programas.map((opcion) => (
                <option key={opcion.valor} value={opcion.valor}>
                  {opcion.etiqueta}
                </option>
              ))}
            </select>
          </label>

          <label htmlFor={`${idBase}-jornada`} className="block">
            <span className="text-sm font-medium text-gray-700">
              Jornada{' '}
              <span aria-hidden="true" className="text-red-500">
                *
              </span>
            </span>
            <select
              id={`${idBase}-jornada`}
              value={valores.jornada}
              onChange={(evento) => cambiar('jornada', evento.target.value)}
              disabled={bloqueado}
              required
              className={CLASE_SELECT}
            >
              <option value="">
                {placeholder(catalogoCargando, catalogo.jornadas)}
              </option>
              {catalogo.jornadas.map((opcion) => (
                <option key={opcion.valor} value={opcion.valor}>
                  {opcion.etiqueta}
                </option>
              ))}
            </select>
          </label>

          <label htmlFor={`${idBase}-sede`} className="block">
            <span className="text-sm font-medium text-gray-700">
              Sede{' '}
              <span aria-hidden="true" className="text-red-500">
                *
              </span>
            </span>
            <select
              id={`${idBase}-sede`}
              value={valores.sede}
              onChange={(evento) => cambiar('sede', evento.target.value)}
              disabled={bloqueado}
              required
              className={CLASE_SELECT}
            >
              <option value="">
                {placeholder(catalogoCargando, catalogo.sedes)}
              </option>
              {catalogo.sedes.map((opcion) => (
                <option key={opcion.valor} value={opcion.valor}>
                  {opcion.etiqueta}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="rounded-xl border border-dashed border-indigo-200 bg-indigo-50/40 p-4">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <p className="text-sm font-semibold text-gray-800">
                Matriz de Estudio{' '}
                <span className="font-normal text-gray-500">(opcional)</span>
              </p>
              <p className="mt-0.5 max-w-xl text-xs text-gray-500">
                Archivo CSV o Excel con tus materias aprobadas y por ver. Si no
                lo adjuntas, se conservarán las materias que ya tengas
                registradas.
              </p>
            </div>
            <span className="rounded-full border border-indigo-200 bg-white px-2.5 py-0.5 text-[11px] font-semibold text-indigo-700">
              .csv · .xlsx · .xls
            </span>
          </div>

          <input
            ref={inputRef}
            id={`${idBase}-matriz`}
            type="file"
            accept=".csv,.xlsx,.xls"
            onChange={seleccionarArchivo}
            disabled={enviando}
            className={CLASE_INPUT_ARCHIVO}
            aria-label="Matriz de Estudio (opcional)"
            aria-describedby={`${idBase}-matriz-ayuda`}
          />

          <p id={`${idBase}-matriz-ayuda`} className="mt-1.5 text-xs text-gray-400">
            Tamaño máximo: 10 MB.
          </p>

          {archivo && (
            <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-indigo-100 bg-white px-3 py-2">
              <span aria-hidden="true">📄</span>
              <span className="min-w-0 flex-1 truncate text-xs font-medium text-gray-700">
                {texto(archivo.name)}
              </span>
              <span className="text-xs text-gray-400">
                {formatearPeso(archivo.size)}
              </span>
              <button
                type="button"
                onClick={quitarArchivo}
                disabled={enviando}
                className="rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-semibold text-gray-600 transition-colors hover:bg-gray-50 disabled:opacity-50"
              >
                Quitar
              </button>
            </div>
          )}

          {errorArchivo && (
            <p
              role="alert"
              className="mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-medium text-red-700"
            >
              {errorArchivo}
            </p>
          )}
        </div>

        <div className="flex flex-col gap-3 border-t border-gray-100 pt-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-gray-500">
            {estudianteId != null
              ? `Se actualizará el perfil del estudiante #${estudianteId}.`
              : 'Se creará un nuevo perfil de estudiante.'}
          </p>

          <button
            type="submit"
            disabled={enviando || catalogoCargando}
            className="inline-flex items-center justify-center gap-2 rounded-xl bg-indigo-600 px-7 py-3.5 text-base font-semibold text-white shadow-lg transition-colors hover:bg-indigo-500 focus:outline-none focus-visible:ring-4 focus-visible:ring-indigo-200 active:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none"
          >
            {enviando && <Spinner label={null} className="h-4 w-4" />}
            {enviando ? 'Guardando perfil…' : 'Continuar'}
            {!enviando && (
              <span aria-hidden="true" className="text-lg leading-none">
                →
              </span>
            )}
          </button>
        </div>
      </form>
    </section>
  )
}
