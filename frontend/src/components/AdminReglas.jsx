import { useCallback, useEffect, useMemo, useState } from 'react'
import Banner from './Banner.jsx'
import Spinner from './Spinner.jsx'
import { apiGet, apiPut } from '../api.js'

// Reglas de prerrequisitos (MongoDB) desde el panel de administrador.
// Se editan SIEMPRE sobre materias que ya existen en las reglas y se envía el
// arreglo completo por PUT /api/admin/reglas; nunca se crean materias nuevas.
//
// Los cambios (agregar/quitar chips) se acumulan en un borrador local y no se
// envían hasta pulsar «Guardar Cambios». Así el botón queda visible y el
// administrador puede revisar antes de reemplazar el arreglo completo.

const CLASE_SELECT =
  'w-full rounded-xl border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-800 shadow-sm transition-colors focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200 disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-400'

function texto(valor) {
  if (valor == null) return ''
  return String(valor).trim()
}

// Etiqueta legible para los <select>: el valor sigue siendo el código, pero se
// muestra también el nombre cuando las reglas lo traen del catálogo.
function etiquetaMateria(regla) {
  if (!regla) return ''
  return regla.nombre ? `${regla.materia} — ${regla.nombre}` : regla.materia
}

// Normaliza GET /api/admin/reglas conservando los metadatos opcionales
// (nombre, creditos, semestre) que usa el catálogo para mostrar cada tarjeta.
function normalizarReglas(datos) {
  if (!Array.isArray(datos)) return []

  const porMateria = new Map()

  datos.forEach((regla) => {
    if (!regla || typeof regla !== 'object' || Array.isArray(regla)) return

    const materia = texto(regla.materia).toUpperCase()
    if (!materia) return

    const previa = porMateria.get(materia)
    const prerrequisitos = Array.isArray(regla.prerrequisitos)
      ? regla.prerrequisitos
          .map((prerequisito) => texto(prerequisito).toUpperCase())
          .filter(Boolean)
      : []

    porMateria.set(materia, {
      materia,
      prerrequisitos: [
        ...new Set([...(previa ? previa.prerrequisitos : []), ...prerrequisitos]),
      ],
      nombre: texto(regla.nombre) || (previa ? previa.nombre : ''),
      creditos: regla.creditos ?? (previa ? previa.creditos : null),
      semestre: regla.semestre ?? (previa ? previa.semestre : null),
    })
  })

  return [...porMateria.values()].sort((a, b) =>
    a.materia.localeCompare(b.materia),
  )
}

// El contrato del PUT solo necesita {materia, prerrequisitos}; los metadatos
// del catálogo los conserva el backend.
function reglasParaEnviar(reglas) {
  return reglas.map((regla) => ({
    materia: regla.materia,
    prerrequisitos: [...regla.prerrequisitos],
  }))
}

function mismasReglas(a, b) {
  if (a.length !== b.length) return false

  return a.every((regla, indice) => {
    const referencia = b[indice]
    if (!referencia || regla.materia !== referencia.materia) return false
    if (regla.prerrequisitos.length !== referencia.prerrequisitos.length) {
      return false
    }
    return regla.prerrequisitos.every(
      (codigo, posicion) => codigo === referencia.prerrequisitos[posicion],
    )
  })
}

export default function AdminReglas() {
  // Última versión confirmada por el servidor y copia editable local.
  const [reglas, setReglas] = useState([])
  const [borrador, setBorrador] = useState([])

  const [cargando, setCargando] = useState(true)
  const [guardando, setGuardando] = useState(false)
  const [errorCarga, setErrorCarga] = useState(null)
  const [mensaje, setMensaje] = useState(null)
  const [filtro, setFiltro] = useState('')

  // Formulario para agregar un prerrequisito al borrador.
  const [materia, setMateria] = useState('')
  const [prerequisito, setPrerequisito] = useState('')
  const [errorFormulario, setErrorFormulario] = useState(null)

  const cargar = useCallback(
    async ({ silencioso = false, reportarError = true } = {}) => {
      if (!silencioso) setCargando(true)
      if (reportarError) setErrorCarga(null)

      try {
        const datos = await apiGet('/api/admin/reglas')
        const normalizadas = normalizarReglas(datos)
        setReglas(normalizadas)
        setBorrador(normalizadas)
        setErrorCarga(null)
        return true
      } catch (error) {
        if (reportarError) {
          setErrorCarga(
            error.message ||
              'No se pudieron cargar las reglas de prerrequisitos.',
          )
        }
        return false
      } finally {
        if (!silencioso) setCargando(false)
      }
    },
    [],
  )

  useEffect(() => {
    cargar()
  }, [cargar])

  const hayCambios = useMemo(
    () => !mismasReglas(borrador, reglas),
    [borrador, reglas],
  )

  async function guardarCambios() {
    if (guardando || cargando || errorCarga || !hayCambios) return

    const instantanea = borrador
    setGuardando(true)
    setMensaje(null)
    setErrorFormulario(null)

    try {
      await apiPut('/api/admin/reglas', reglasParaEnviar(instantanea))

      // Refleja el cambio de inmediato aunque la recarga posterior falle.
      setReglas(instantanea)
      setBorrador(instantanea)
      setMensaje({
        tipo: 'ok',
        texto:
          'Cambios guardados correctamente. Se envió el arreglo completo de reglas.',
      })
      setPrerequisito('')

      // Recarga silenciosa para reflejar exactamente lo que guardó el backend.
      const recargado = await cargar({
        silencioso: true,
        reportarError: false,
      })
      if (!recargado) {
        setMensaje({
          tipo: 'aviso',
          texto:
            'Los cambios se guardaron, pero no se pudo refrescar la lista. Pulsa «Recargar».',
        })
      }
    } catch (error) {
      setMensaje({
        tipo: 'error',
        texto:
          error.message ||
          'No se pudieron guardar las reglas de prerrequisitos.',
      })
    } finally {
      setGuardando(false)
    }
  }

  function descartarCambios() {
    if (guardando || !hayCambios) return

    setBorrador(reglas)
    setPrerequisito('')
    setErrorFormulario(null)
    setMensaje({
      tipo: 'info',
      texto: 'Se descartaron los cambios sin guardar.',
    })
  }

  function recargar() {
    if (guardando) return

    if (
      hayCambios &&
      typeof window !== 'undefined' &&
      !window.confirm(
        'Hay cambios sin guardar. ¿Recargar las reglas y descartar esos cambios?',
      )
    ) {
      return
    }

    setMensaje(null)
    setErrorFormulario(null)
    cargar()
  }

  function agregarPrerequisito(evento) {
    evento.preventDefault()

    // Nunca enviar el arreglo completo si aún no se cargó (o falló la carga):
    // se sobrescribirían las reglas del servidor con una lista incompleta.
    if (cargando || guardando || errorCarga || borrador.length === 0) return

    setMensaje(null)
    setErrorFormulario(null)

    const materiaNormalizada = materia.trim().toUpperCase()
    const prerequisitoNormalizado = prerequisito.trim().toUpperCase()

    if (!materiaNormalizada) {
      setErrorFormulario('Selecciona una materia existente de la lista.')
      return
    }

    if (!prerequisitoNormalizado) {
      setErrorFormulario('Selecciona el prerrequisito que quieres agregar.')
      return
    }

    if (materiaNormalizada === prerequisitoNormalizado) {
      setErrorFormulario('Una materia no puede ser prerrequisito de sí misma.')
      return
    }

    const existente = borrador.find(
      (regla) => regla.materia === materiaNormalizada,
    )

    // El administrador solo edita materias que ya vienen precargadas: nunca
    // se crea una materia nueva desde aquí.
    if (!existente) {
      setErrorFormulario(
        `La materia ${materiaNormalizada} ya no está en la lista. Recarga las reglas.`,
      )
      return
    }

    if (existente.prerrequisitos.includes(prerequisitoNormalizado)) {
      setErrorFormulario(
        `${prerequisitoNormalizado} ya es prerrequisito de ${materiaNormalizada}.`,
      )
      return
    }

    setBorrador((actual) =>
      actual.map((regla) =>
        regla.materia === materiaNormalizada
          ? {
              ...regla,
              prerrequisitos: [
                ...regla.prerrequisitos,
                prerequisitoNormalizado,
              ],
            }
          : regla,
      ),
    )
    setFiltro('')
    setPrerequisito('')
  }

  function quitarPrerequisito(materiaObjetivo, prerequisitoObjetivo) {
    if (guardando || cargando || errorCarga) return

    setMensaje(null)
    setBorrador((actual) =>
      actual.map((regla) =>
        regla.materia === materiaObjetivo
          ? {
              ...regla,
              prerrequisitos: regla.prerrequisitos.filter(
                (prerequisitoActual) =>
                  prerequisitoActual !== prerequisitoObjetivo,
              ),
            }
          : regla,
      ),
    )
  }

  const materiasDisponibles = useMemo(
    () => borrador.map((regla) => regla.materia),
    [borrador],
  )
  const reglasPorMateria = useMemo(
    () => new Map(borrador.map((regla) => [regla.materia, regla])),
    [borrador],
  )
  const totalPrerequisitos = borrador.reduce(
    (total, regla) => total + regla.prerrequisitos.length,
    0,
  )
  const sinPrerequisitos = borrador.filter(
    (regla) => regla.prerrequisitos.length === 0,
  ).length

  // Solo se puede elegir una materia que ya existe en las reglas cargadas.
  const materiaSeleccionada = materiasDisponibles.includes(materia)
    ? materia
    : ''
  const reglaSeleccionada =
    borrador.find((regla) => regla.materia === materiaSeleccionada) || null
  const prerrequisitosActuales = reglaSeleccionada
    ? reglaSeleccionada.prerrequisitos
    : []
  const opcionesPrerequisito = materiasDisponibles.filter(
    (codigo) =>
      codigo !== materiaSeleccionada &&
      !prerrequisitosActuales.includes(codigo),
  )

  const formularioDeshabilitado =
    cargando ||
    guardando ||
    Boolean(errorCarga) ||
    materiasDisponibles.length === 0
  const quitarDeshabilitado =
    cargando || guardando || Boolean(errorCarga)

  const reglasFiltradas = useMemo(() => {
    const consulta = filtro.trim().toLowerCase()
    if (!consulta) return borrador

    return borrador.filter(
      (regla) =>
        regla.materia.toLowerCase().includes(consulta) ||
        texto(regla.nombre).toLowerCase().includes(consulta) ||
        regla.prerrequisitos.some((codigo) =>
          codigo.toLowerCase().includes(consulta),
        ),
    )
  }, [borrador, filtro])

  const estado = cargando
    ? { texto: 'Cargando reglas…', punto: 'bg-sky-500' }
    : errorCarga
      ? { texto: 'No se pudieron cargar las reglas', punto: 'bg-red-500' }
      : guardando
        ? { texto: 'Guardando cambios…', punto: 'bg-amber-500 animate-pulse' }
        : hayCambios
          ? { texto: 'Cambios sin guardar', punto: 'bg-amber-500' }
          : borrador.length === 0
            ? { texto: 'Sin reglas para editar', punto: 'bg-gray-400' }
            : { texto: 'Todo guardado', punto: 'bg-emerald-500' }

  const guardarDeshabilitado =
    guardando || cargando || Boolean(errorCarga) || !hayCambios

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <span
            aria-hidden="true"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-indigo-50 text-xl ring-1 ring-inset ring-indigo-100"
          >
            🔗
          </span>
          <div>
            <h2 className="text-xl font-semibold text-gray-800">
              Reglas de prerrequisitos
            </h2>
            <p className="mt-1 max-w-2xl text-sm text-gray-500">
              Define qué materias exigen otras antes de poder inscribirlas. Los
              cambios se revisan en el borrador y se envían como el arreglo
              completo de reglas.
            </p>
          </div>
        </div>

        <button
          type="button"
          onClick={recargar}
          disabled={cargando || guardando}
          className="inline-flex items-center gap-2 rounded-lg border border-indigo-200 bg-white px-3 py-2 text-sm font-medium text-indigo-700 shadow-sm transition-colors hover:bg-indigo-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300 disabled:opacity-50"
        >
          {cargando && <Spinner label={null} className="h-3.5 w-3.5" />}
          {cargando ? 'Actualizando…' : 'Recargar'}
        </button>
      </div>

      {/* Barra de acciones siempre visible: el guardado no queda al final. */}
      <div className="sticky top-2 z-30 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-indigo-100 bg-white/95 px-4 py-3 shadow-md ring-1 ring-black/5 backdrop-blur">
        <div className="flex min-w-0 items-center gap-2.5">
          <span
            aria-hidden="true"
            className={`h-2.5 w-2.5 shrink-0 rounded-full ${estado.punto}`}
          />
          <div className="min-w-0">
            <p
              aria-live="polite"
              className="text-sm font-semibold text-gray-800"
            >
              {estado.texto}
            </p>
            <p className="text-xs text-gray-500">
              {borrador.length}{' '}
              {borrador.length === 1 ? 'materia' : 'materias'} ·{' '}
              {totalPrerequisitos}{' '}
              {totalPrerequisitos === 1 ? 'prerrequisito' : 'prerrequisitos'} ·
              se envía el arreglo completo
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={descartarCambios}
            disabled={guardando || !hayCambios}
            className="inline-flex items-center justify-center rounded-xl border border-gray-300 bg-white px-4 py-2.5 text-sm font-semibold text-gray-700 transition-colors hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Descartar
          </button>

          <button
            type="button"
            onClick={guardarCambios}
            disabled={guardarDeshabilitado}
            aria-busy={guardando}
            title={
              !hayCambios && !guardando
                ? 'No hay cambios pendientes'
                : undefined
            }
            className="inline-flex items-center justify-center gap-2 rounded-xl bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white shadow-md transition-all hover:bg-indigo-500 hover:shadow-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 active:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {guardando ? (
              <Spinner label={null} className="h-4 w-4" />
            ) : (
              <span aria-hidden="true">💾</span>
            )}
            {guardando ? 'Guardando…' : 'Guardar Cambios'}
          </button>
        </div>
      </div>

      {mensaje && (
        <Banner tipo={mensaje.tipo} onCerrar={() => setMensaje(null)}>
          {mensaje.texto}
        </Banner>
      )}

      {/* Formulario: únicamente materias existentes en las reglas. */}
      <section className="rounded-2xl border border-indigo-100 bg-white p-4 shadow-sm sm:p-6">
        <div className="flex items-start gap-3">
          <span
            aria-hidden="true"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-indigo-600 text-lg font-bold text-white shadow-sm"
          >
            ＋
          </span>
          <div>
            <h3 className="text-base font-semibold text-gray-800">
              Agregar prerrequisito
            </h3>
            <p className="text-sm text-gray-500">
              Elige una materia existente y el prerrequisito que debe exigir.
              No se pueden crear materias nuevas desde este panel.
            </p>
          </div>
        </div>

        {materiasDisponibles.length === 0 ? (
          cargando ? (
            <div className="mt-4">
              <Spinner
                label="Cargando materias para editar…"
                className="h-4 w-4"
              />
            </div>
          ) : errorCarga ? (
            <p className="mt-4 rounded-xl border border-gray-200 bg-gray-50 px-3 py-2.5 text-sm text-gray-500">
              El formulario se habilitará cuando las reglas se carguen
              correctamente.
            </p>
          ) : (
            <p className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-sm text-amber-800">
              No hay materias disponibles para editar. Carga la oferta académica
              o revisa las reglas en MongoDB.
            </p>
          )
        ) : (
          <form onSubmit={agregarPrerequisito} className="mt-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label
                  htmlFor="admin-reglas-materia"
                  className="mb-1.5 flex items-center justify-between gap-2 text-sm font-semibold text-gray-700"
                >
                  <span>
                    Materia{' '}
                    <span className="text-red-500" aria-hidden="true">
                      *
                    </span>
                  </span>
                  <span className="text-[11px] font-normal text-gray-400">
                    Obligatorio
                  </span>
                </label>
                <select
                  id="admin-reglas-materia"
                  value={materiaSeleccionada}
                  onChange={(evento) => {
                    setMateria(evento.target.value)
                    setPrerequisito('')
                    setErrorFormulario(null)
                  }}
                  disabled={formularioDeshabilitado}
                  aria-describedby="admin-reglas-materia-ayuda"
                  className={CLASE_SELECT}
                >
                  <option value="">Selecciona una materia…</option>
                  {materiasDisponibles.map((codigo) => (
                    <option key={codigo} value={codigo}>
                      {etiquetaMateria(reglasPorMateria.get(codigo))}
                    </option>
                  ))}
                </select>
                <p
                  id="admin-reglas-materia-ayuda"
                  className="mt-1.5 text-xs text-gray-500"
                >
                  Solo aparecen materias que ya existen en las reglas.
                </p>
              </div>

              <div>
                <label
                  htmlFor="admin-reglas-prerrequisito"
                  className="mb-1.5 flex items-center justify-between gap-2 text-sm font-semibold text-gray-700"
                >
                  <span>
                    Prerrequisito requerido{' '}
                    <span className="text-red-500" aria-hidden="true">
                      *
                    </span>
                  </span>
                  <span className="text-[11px] font-normal text-gray-400">
                    Obligatorio
                  </span>
                </label>
                <select
                  id="admin-reglas-prerrequisito"
                  value={prerequisito}
                  onChange={(evento) => {
                    setPrerequisito(evento.target.value)
                    setErrorFormulario(null)
                  }}
                  disabled={
                    formularioDeshabilitado ||
                    !materiaSeleccionada ||
                    opcionesPrerequisito.length === 0
                  }
                  aria-describedby="admin-reglas-prerrequisito-ayuda"
                  className={CLASE_SELECT}
                >
                  <option value="">
                    {materiaSeleccionada
                      ? 'Selecciona un prerrequisito…'
                      : 'Primero elige una materia'}
                  </option>
                  {opcionesPrerequisito.map((codigo) => (
                    <option key={codigo} value={codigo}>
                      {etiquetaMateria(reglasPorMateria.get(codigo))}
                    </option>
                  ))}
                </select>
                <p
                  id="admin-reglas-prerrequisito-ayuda"
                  className="mt-1.5 text-xs text-gray-500"
                >
                  Se agrega al borrador; luego pulsa «Guardar Cambios».
                </p>
              </div>
            </div>

            {materiaSeleccionada && (
              <div className="mt-4 rounded-xl border border-gray-200 bg-gray-50/80 px-3 py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                    Prerrequisitos actuales de{' '}
                    <span className="font-mono text-indigo-700">
                      {materiaSeleccionada}
                    </span>
                  </p>
                  <span className="rounded-full bg-white px-2 py-0.5 text-[11px] font-medium text-gray-600 ring-1 ring-inset ring-gray-200">
                    {prerrequisitosActuales.length}
                  </span>
                </div>

                {prerrequisitosActuales.length === 0 ? (
                  <p className="mt-2 text-xs text-gray-400">
                    Esta materia no tiene prerrequisitos.
                  </p>
                ) : (
                  <ul className="mt-2 flex flex-wrap gap-1.5">
                    {prerrequisitosActuales.map((codigo) => (
                      <li
                        key={`${materiaSeleccionada}-${codigo}`}
                        className="rounded-full border border-indigo-100 bg-white px-2.5 py-1 font-mono text-xs font-medium text-indigo-800"
                      >
                        {codigo}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {materiaSeleccionada && opcionesPrerequisito.length === 0 && (
              <p className="mt-3 text-xs text-gray-500">
                No hay otras materias disponibles para agregar como
                prerrequisito a {materiaSeleccionada}.
              </p>
            )}

            {errorFormulario && (
              <div className="mt-3">
                <Banner tipo="aviso" onCerrar={() => setErrorFormulario(null)}>
                  {errorFormulario}
                </Banner>
              </div>
            )}

            <div className="mt-5 flex flex-col gap-3 border-t border-gray-100 pt-4 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-xs text-gray-500">
                El cambio queda en el borrador hasta que pulses «Guardar
                Cambios».
              </p>
              <button
                type="submit"
                disabled={
                  formularioDeshabilitado ||
                  !materiaSeleccionada ||
                  !prerequisito
                }
                className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-indigo-200 bg-indigo-50 px-5 py-2.5 text-sm font-semibold text-indigo-700 shadow-sm transition-colors hover:bg-indigo-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300 disabled:cursor-not-allowed disabled:opacity-40 sm:w-auto"
              >
                <span aria-hidden="true">＋</span>
                Agregar prerrequisito
              </button>
            </div>
          </form>
        )}
      </section>

      {/* Tarjetas por materia con chips de prerrequisitos y botón ×. */}
      <section className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm sm:p-6">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-lg font-semibold text-gray-800">
                Materias y prerrequisitos
              </h3>
              {!(cargando && borrador.length === 0) && (
                <>
                  <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700">
                    {borrador.length}{' '}
                    {borrador.length === 1 ? 'materia' : 'materias'}
                  </span>
                  <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600">
                    {totalPrerequisitos}{' '}
                    {totalPrerequisitos === 1
                      ? 'prerrequisito'
                      : 'prerrequisitos'}
                  </span>
                  {sinPrerequisitos > 0 && (
                    <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">
                      {sinPrerequisitos} sin prerrequisitos
                    </span>
                  )}
                  {hayCambios && (
                    <span className="rounded-full bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-700 ring-1 ring-inset ring-amber-200">
                      Sin guardar
                    </span>
                  )}
                </>
              )}
            </div>
            <p className="mt-1 text-sm text-gray-500">
              Quita un prerrequisito con «×» y pulsa «Guardar Cambios» para
              aplicarlo.
            </p>
          </div>

          {borrador.length > 0 && (
            <label className="relative w-full sm:max-w-xs">
              <span className="sr-only">
                Buscar materia o prerrequisito
              </span>
              <span
                aria-hidden="true"
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-gray-400"
              >
                🔍
              </span>
              <input
                type="search"
                value={filtro}
                onChange={(evento) => setFiltro(evento.target.value)}
                placeholder="Buscar materia o prerrequisito…"
                className={`w-full rounded-lg border border-gray-300 py-2 pl-9 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200 ${
                  filtro ? 'pr-9' : 'pr-3'
                }`}
              />
              {filtro && (
                <button
                  type="button"
                  onClick={() => setFiltro('')}
                  aria-label="Limpiar búsqueda"
                  className="absolute right-1.5 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-full text-base leading-none text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300"
                >
                  ×
                </button>
              )}
            </label>
          )}
        </div>

        {guardando && (
          <div className="mb-3">
            <Spinner
              label="Guardando cambios en las reglas…"
              className="h-4 w-4"
            />
          </div>
        )}

        {errorCarga && (
          <div className="mb-3">
            <Banner
              tipo="error"
              titulo="No se pudieron cargar las reglas"
              accion={
                <button
                  type="button"
                  onClick={() => cargar()}
                  disabled={cargando}
                  className="rounded-md border border-red-300 bg-white px-3 py-1 text-xs font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50"
                >
                  Reintentar
                </button>
              }
            >
              {errorCarga}
            </Banner>
          </div>
        )}

        {cargando && borrador.length === 0 ? (
          <Spinner
            label="Cargando reglas de prerrequisitos…"
            className="h-8 w-8"
            centrado
          />
        ) : borrador.length === 0 ? (
          errorCarga ? null : (
            <div className="rounded-xl border border-dashed border-gray-300 bg-gray-50 px-4 py-10 text-center">
              <p className="text-3xl" aria-hidden="true">
                🗂️
              </p>
              <p className="mt-2 font-medium text-gray-700">
                No hay materias precargadas en las reglas.
              </p>
              <p className="mt-1 text-sm text-gray-500">
                Carga la oferta académica o revisa las reglas en MongoDB.
              </p>
            </div>
          )
        ) : reglasFiltradas.length === 0 ? (
          <div className="rounded-xl border border-dashed border-gray-300 bg-gray-50 px-4 py-8 text-center text-sm text-gray-500">
            Ninguna materia coincide con «{filtro.trim()}».
          </div>
        ) : (
          <ul className="grid gap-3 lg:grid-cols-2">
            {reglasFiltradas.map((regla) => (
              <li
                key={regla.materia}
                className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm transition-shadow hover:border-indigo-200 hover:shadow-md"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="font-mono text-sm font-bold text-indigo-700">
                      {regla.materia}
                    </p>
                    {regla.nombre && (
                      <p
                        className="truncate text-sm text-gray-700"
                        title={regla.nombre}
                      >
                        {regla.nombre}
                      </p>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-1">
                    {regla.creditos != null && (
                      <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-600">
                        {regla.creditos} créd.
                      </span>
                    )}
                    {regla.semestre != null && (
                      <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-600">
                        Sem. {regla.semestre}
                      </span>
                    )}
                    <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] font-semibold text-indigo-700">
                      {regla.prerrequisitos.length}{' '}
                      {regla.prerrequisitos.length === 1
                        ? 'prereq.'
                        : 'prereqs.'}
                    </span>
                  </div>
                </div>

                <div className="mt-3 border-t border-gray-100 pt-3">
                  {regla.prerrequisitos.length === 0 ? (
                    <p className="text-xs text-gray-400">Sin prerrequisitos</p>
                  ) : (
                    <div className="flex flex-wrap gap-1.5">
                      {regla.prerrequisitos.map((codigo) => (
                        <span
                          key={`${regla.materia}-${codigo}`}
                          className="inline-flex items-center gap-1 rounded-full border border-indigo-100 bg-indigo-50 py-1 pl-2.5 pr-1 text-xs font-medium text-indigo-800 shadow-sm"
                        >
                          <span className="font-mono">{codigo}</span>
                          <button
                            type="button"
                            onClick={() =>
                              quitarPrerequisito(regla.materia, codigo)
                            }
                            disabled={quitarDeshabilitado}
                            title={`Quitar ${codigo} de ${regla.materia}`}
                            aria-label={`Quitar ${codigo} de ${regla.materia}`}
                            className="flex h-5 w-5 items-center justify-center rounded-full text-sm font-bold leading-none text-indigo-400 transition-colors hover:bg-red-100 hover:text-red-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300 disabled:opacity-40"
                          >
                            ×
                          </button>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}

        {borrador.length > 0 && (
          <p className="mt-4 text-xs text-gray-400">
            GET /api/admin/reglas · PUT /api/admin/reglas con el arreglo
            completo ({borrador.length}{' '}
            {borrador.length === 1 ? 'materia' : 'materias'}).
          </p>
        )}
      </section>
    </div>
  )
}
