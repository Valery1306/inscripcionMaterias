import Spinner from './Spinner.jsx'

const DIAS = {
  1: 'Lunes',
  2: 'Martes',
  3: 'Miércoles',
  4: 'Jueves',
  5: 'Viernes',
  6: 'Sábado',
  7: 'Domingo',
}

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
        valor.nombre_docente ??
        valor.nombre_sede ??
        valor.descripcion ??
        valor.codigo ??
        valor.id,
    )
  }
  return texto(valor)
}

function formatearHora(valor) {
  if (valor == null || valor === '') return '—'

  const cadena = String(valor).trim()
  const coincidencia = cadena.match(/^(\d{1,2}):(\d{2})/)
  if (coincidencia) {
    return `${coincidencia[1].padStart(2, '0')}:${coincidencia[2]}`
  }

  return cadena || '—'
}

function nombreDia(valor) {
  if (valor == null || valor === '') return '—'

  const cadena = String(valor).trim()
  const numero = Number(cadena)
  if (Number.isInteger(numero) && DIAS[numero]) return DIAS[numero]

  return cadena.charAt(0).toUpperCase() + cadena.slice(1).toLowerCase()
}

/**
 * Aplana la respuesta del backend a una fila por sesión.
 * Cada materia conserva sus datos generales (NRC, docente, sede, créditos)
 * y cada sesión aporta día, horas y salón.
 */
function construirFilas(horario) {
  if (!Array.isArray(horario)) return []

  const filas = []

  horario.forEach((item, indiceMateria) => {
    if (!esObjeto(item)) return

    const sesiones = Array.isArray(item.sesiones)
      ? item.sesiones.filter(esObjeto)
      : []

    const base = {
      materia:
        textoCampo(item.materia ?? item.nombre ?? item.nombre_materia ?? item.curso) ||
        texto(item.codigo) ||
        'Materia sin nombre',
      codigo: texto(
        item.codigo ?? item.codigo_alfanumerico ?? item.codigo_materia,
      ),
      creditos: item.creditos,
      nrc: item.nrc_id ?? item.nrc ?? item.id_nrc,
      docente: textoCampo(
        item.docente_nombre ?? item.docente ?? item.nombre_docente,
      ),
      sede: textoCampo(item.sede_nombre ?? item.sede ?? item.nombre_sede),
      dia: textoCampo(item.dia_nombre ?? item.dia),
      inicio: item.hora_inicio ?? item.horaInicio,
      fin: item.hora_fin ?? item.horaFin,
      salon: textoCampo(item.salon ?? item.salón ?? item.aula),
    }

    if (sesiones.length === 0) {
      filas.push({ ...base, key: `${indiceMateria}-0` })
      return
    }

    sesiones.forEach((sesion, indiceSesion) => {
      filas.push({
        ...base,
        key: `${base.nrc ?? 'materia'}-${indiceMateria}-${indiceSesion}`,
        docente:
          textoCampo(sesion.docente_nombre ?? sesion.docente) || base.docente,
        sede: textoCampo(sesion.sede_nombre ?? sesion.sede) || base.sede,
        dia: textoCampo(sesion.dia_nombre ?? sesion.dia) || base.dia,
        inicio: sesion.hora_inicio ?? sesion.horaInicio ?? base.inicio,
        fin: sesion.hora_fin ?? sesion.horaFin ?? base.fin,
        salon:
          textoCampo(sesion.salon ?? sesion.salón ?? sesion.aula) || base.salon,
      })
    })
  })

  return filas
}

function calcularCreditos(horario) {
  if (!Array.isArray(horario)) return 0

  const vistos = new Set()
  let total = 0

  horario.forEach((item, indice) => {
    if (!esObjeto(item)) return

    const clave =
      item.nrc_id ??
      item.nrc ??
      item.codigo ??
      item.materia ??
      `materia-${indice}`
    if (vistos.has(clave)) return

    vistos.add(clave)
    const creditos = Number(item.creditos)
    if (Number.isFinite(creditos)) total += creditos
  })

  return total
}

/**
 * Paso 3 (resultado): tabla del horario asignado y materias no asignadas.
 * Recibe `{ horario, noAsignadas, resumen }` ya normalizado por el panel.
 */
export default function HorarioResultado({ resultado, generando = false }) {
  if (generando) {
    return (
      <div
        aria-busy="true"
        className="mt-5 rounded-xl border border-indigo-100 bg-indigo-50/60 px-4 py-8 text-indigo-700"
      >
        <Spinner
          centrado
          label="Buscando los NRC que mejor encajan con tu perfil…"
          className="h-8 w-8"
        />
      </div>
    )
  }

  if (!resultado) {
    return (
      <div className="mt-5 rounded-xl border border-dashed border-gray-300 bg-gray-50 px-4 py-6 text-center">
        <p className="text-sm font-medium text-gray-600">
          Tu horario aparecerá aquí
        </p>
        <p className="mt-1 text-xs text-gray-500">
          Al generar el horario verás cada sesión con su NRC, docente, sede, día,
          hora y salón.
        </p>
      </div>
    )
  }

  const horario = Array.isArray(resultado.horario) ? resultado.horario : []
  const noAsignadas = Array.isArray(resultado.noAsignadas)
    ? resultado.noAsignadas
    : []
  const filas = construirFilas(horario)
  const totalCreditos = calcularCreditos(horario)
  const totalMaterias = horario.filter(esObjeto).length

  return (
    <div className="mt-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-gray-800">
            Horario propuesto
          </h3>
          <p className="mt-0.5 text-xs text-gray-500">
            Una fila por cada sesión semanal. Revisa que no haya cruces.
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <span className="rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1 text-xs font-semibold text-indigo-700">
            {totalMaterias} {totalMaterias === 1 ? 'materia' : 'materias'}
          </span>
          <span className="rounded-full border border-sky-200 bg-sky-50 px-3 py-1 text-xs font-semibold text-sky-700">
            {filas.length} {filas.length === 1 ? 'sesión' : 'sesiones'}
          </span>
          <span className="rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700">
            {totalCreditos} créditos
          </span>
        </div>
      </div>

      {filas.length > 0 ? (
        <div className="mt-3 overflow-x-auto rounded-xl border border-gray-200 shadow-sm">
          <table className="w-full min-w-[940px] border-collapse text-sm">
            <caption className="sr-only">
              Horario propuesto con materia, NRC, docente, sede, día, horas y
              salón por sesión
            </caption>
            <thead>
              <tr className="bg-gradient-to-r from-indigo-600 to-violet-600 text-left text-[11px] uppercase tracking-wide text-white">
                <th scope="col" className="px-3 py-3 font-semibold">
                  Materia
                </th>
                <th scope="col" className="px-3 py-3 font-semibold">
                  NRC
                </th>
                <th scope="col" className="px-3 py-3 font-semibold">
                  Docente
                </th>
                <th scope="col" className="px-3 py-3 font-semibold">
                  Sede
                </th>
                <th scope="col" className="px-3 py-3 font-semibold">
                  Día
                </th>
                <th scope="col" className="px-3 py-3 font-semibold">
                  Hora inicio
                </th>
                <th scope="col" className="px-3 py-3 font-semibold">
                  Hora fin
                </th>
                <th scope="col" className="px-3 py-3 font-semibold">
                  Salón
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filas.map((fila) => (
                <tr
                  key={fila.key}
                  className="bg-white transition-colors odd:bg-white even:bg-slate-50/80 hover:bg-indigo-50/70"
                >
                  <td className="px-3 py-3 align-top">
                    <p className="font-semibold text-gray-800">{fila.materia}</p>
                    <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-gray-500">
                      {fila.codigo && fila.codigo !== fila.materia && (
                        <span className="font-mono">{fila.codigo}</span>
                      )}
                      {fila.creditos != null && fila.creditos !== '' && (
                        <span className="rounded bg-gray-100 px-1.5 py-0.5 font-medium text-gray-600">
                          {fila.creditos} cr
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-3 align-top">
                    <span className="inline-flex rounded-md bg-indigo-50 px-2 py-1 font-mono text-xs font-semibold text-indigo-700 ring-1 ring-indigo-100">
                      {fila.nrc != null && fila.nrc !== '' ? fila.nrc : '—'}
                    </span>
                  </td>
                  <td className="px-3 py-3 align-top text-gray-700">
                    {fila.docente || '—'}
                  </td>
                  <td className="px-3 py-3 align-top text-gray-600">
                    {fila.sede || '—'}
                  </td>
                  <td className="px-3 py-3 align-top">
                    <span className="inline-flex rounded-full bg-sky-50 px-2.5 py-0.5 text-xs font-semibold text-sky-700 ring-1 ring-sky-100">
                      {nombreDia(fila.dia)}
                    </span>
                  </td>
                  <td className="px-3 py-3 align-top font-mono text-xs tabular-nums text-gray-700">
                    {formatearHora(fila.inicio)}
                  </td>
                  <td className="px-3 py-3 align-top font-mono text-xs tabular-nums text-gray-700">
                    {formatearHora(fila.fin)}
                  </td>
                  <td className="px-3 py-3 align-top text-gray-700">
                    {fila.salon || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          <p className="font-semibold">No se asignó ninguna materia.</p>
          <p className="mt-1">
            Puede que no haya NRC disponibles o que todos los grupos se crucen
            con tu horario. Revisa las materias no asignadas.
          </p>
        </div>
      )}

      {noAsignadas.length > 0 && (
        <div className="mt-5 rounded-xl border border-amber-200 bg-amber-50 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h4 className="text-sm font-semibold text-amber-900">
              Materias no asignadas
            </h4>
            <span className="rounded-full bg-white px-2.5 py-0.5 text-xs font-bold text-amber-700 ring-1 ring-amber-200">
              {noAsignadas.length}
            </span>
          </div>

          <ul className="mt-3 space-y-2">
            {noAsignadas.map((item, indice) => (
              <li
                key={`${item.materia}-${indice}`}
                className="flex items-start gap-2 rounded-lg bg-white/80 px-3 py-2 ring-1 ring-amber-100"
              >
                <span aria-hidden="true" className="mt-0.5 text-sm">
                  ⚠️
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-800">
                    {item.materia}
                    {item.codigo && item.codigo !== item.materia && (
                      <span className="ml-1.5 font-mono text-xs font-normal text-amber-700">
                        ({item.codigo})
                      </span>
                    )}
                  </p>
                  <p className="mt-0.5 text-xs text-amber-800">{item.motivo}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {filas.length > 0 && noAsignadas.length === 0 && (
        <p className="mt-3 flex items-center gap-1.5 text-xs font-medium text-emerald-700">
          <span aria-hidden="true">✓</span>
          Todas las materias se asignaron correctamente.
        </p>
      )}
    </div>
  )
}
