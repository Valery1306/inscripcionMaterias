import Banner from './Banner.jsx'

const ACENTOS = {
  indigo: 'border-indigo-100 bg-indigo-50/70',
  emerald: 'border-emerald-100 bg-emerald-50/70',
  amber: 'border-amber-100 bg-amber-50/70',
  sky: 'border-sky-100 bg-sky-50/70',
}

function Tarjeta({ etiqueta, valor, detalle, acento = 'indigo' }) {
  return (
    <div
      className={`rounded-xl border p-4 shadow-sm ${ACENTOS[acento] || ACENTOS.indigo}`}
    >
      <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
        {etiqueta}
      </p>
      <p className="mt-1 break-words text-lg font-bold leading-tight text-gray-900">
        {valor}
      </p>
      {detalle && <p className="mt-1 text-xs text-gray-500">{detalle}</p>}
    </div>
  )
}

function Chip({ materia, tono }) {
  const estilos =
    tono === 'aprobada'
      ? 'border-emerald-200 bg-white text-emerald-800 hover:bg-emerald-50'
      : 'border-amber-200 bg-white text-amber-800 hover:bg-amber-50'

  return (
    <span
      title={materia.nombre || materia.codigo}
      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold shadow-sm transition-colors ${estilos}`}
    >
      {materia.codigo}
    </span>
  )
}

function PanelMaterias({ titulo, materias, total, tono }) {
  const esAprobada = tono === 'aprobada'

  return (
    <div
      className={`rounded-xl border p-4 ${
        esAprobada
          ? 'border-emerald-100 bg-emerald-50/50'
          : 'border-amber-100 bg-amber-50/50'
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-gray-800">{titulo}</h3>
        <span
          className={`rounded-full px-2.5 py-0.5 text-xs font-bold ring-1 ${
            esAprobada
              ? 'bg-white text-emerald-700 ring-emerald-200'
              : 'bg-white text-amber-700 ring-amber-200'
          }`}
        >
          {total}
        </span>
      </div>

      {materias.length > 0 ? (
        <div className="mt-3 flex max-h-52 flex-wrap gap-2 overflow-y-auto pr-1">
          {materias.map((materia, indice) => (
            <Chip
              key={`${materia.codigo}-${indice}`}
              materia={materia}
              tono={tono}
            />
          ))}
        </div>
      ) : (
        <p className="mt-2 text-xs text-gray-500">
          {esAprobada
            ? 'No se registran materias aprobadas en tu matriz.'
            : 'No hay materias pendientes por ver.'}
        </p>
      )}
    </div>
  )
}

/**
 * Paso 2: resumen visual del perfil devuelto por el backend.
 * Es de presentación; los datos ya vienen normalizados desde EstudiantePanel.
 */
export default function ResumenPerfil({
  perfil,
  estudianteId = null,
  mensajeOk = null,
  onCerrarMensaje,
  onCambiarDatos,
}) {
  if (!perfil) return null

  const programa = perfil.carrera || perfil.programa || 'No registrado'
  const detallePrograma =
    perfil.carrera && perfil.programa && perfil.carrera !== perfil.programa
      ? perfil.programa
      : 'Programa académico'

  return (
    <section
      aria-labelledby="resumen-perfil-titulo"
      className="rounded-2xl border-2 border-emerald-100 bg-white p-5 shadow-sm sm:p-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <span
            aria-hidden="true"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-base font-bold text-white"
          >
            2
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2
                id="resumen-perfil-titulo"
                className="text-lg font-semibold text-gray-800"
              >
                Resumen de tu perfil
              </h2>
              <span className="rounded-full bg-emerald-50 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-emerald-700">
                Paso 2
              </span>
              {estudianteId != null && (
                <span className="rounded-full border border-gray-200 bg-gray-50 px-2.5 py-0.5 text-[11px] font-medium text-gray-600">
                  ID {estudianteId}
                </span>
              )}
            </div>
            <p className="mt-0.5 text-sm text-gray-500">
              Verifica que tu programa, jornada y materias sean correctos antes
              de generar el horario.
            </p>
          </div>
        </div>

        <button
          type="button"
          onClick={onCambiarDatos}
          className="inline-flex items-center gap-2 rounded-lg border border-indigo-200 bg-white px-3.5 py-2 text-sm font-semibold text-indigo-700 shadow-sm transition-colors hover:bg-indigo-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-200"
        >
          <span aria-hidden="true">✏️</span>
          Cambiar datos
        </button>
      </div>

      {mensajeOk && (
        <div className="mt-4">
          <Banner tipo="ok" onCerrar={onCerrarMensaje}>
            {mensajeOk}
          </Banner>
        </div>
      )}

      <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Tarjeta
          etiqueta="Programa / carrera"
          valor={programa}
          detalle={detallePrograma}
        />
        <Tarjeta
          etiqueta="Jornada"
          valor={perfil.jornada || 'No registrada'}
          detalle="Jornada de estudio"
          acento="sky"
        />
        <Tarjeta
          etiqueta="Sede"
          valor={perfil.sede || 'No registrada'}
          detalle="Sede asignada"
          acento="sky"
        />
        <Tarjeta
          etiqueta="Semestre"
          valor={perfil.semestre || '—'}
          detalle="Semestre actual"
        />
        <Tarjeta
          etiqueta="Materias aprobadas"
          valor={perfil.totalAprobadas}
          detalle="Base para validar prerrequisitos"
          acento="emerald"
        />
        <Tarjeta
          etiqueta="Materias por ver"
          valor={perfil.totalPorVer}
          detalle="Pendientes por cursar"
          acento="amber"
        />
      </div>

      <div className="mt-5 grid gap-4 lg:grid-cols-2">
        <PanelMaterias
          titulo="Materias aprobadas"
          materias={perfil.aprobadas || []}
          total={perfil.totalAprobadas}
          tono="aprobada"
        />
        <PanelMaterias
          titulo="Materias por ver"
          materias={perfil.porVer || []}
          total={perfil.totalPorVer}
          tono="por-ver"
        />
      </div>
    </section>
  )
}
