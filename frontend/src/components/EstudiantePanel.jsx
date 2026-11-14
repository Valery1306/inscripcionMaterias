import { useEffect, useRef, useState } from 'react'
import Banner from './Banner.jsx'
import Spinner from './Spinner.jsx'
import PerfilForm from './PerfilForm.jsx'
import ResumenPerfil from './ResumenPerfil.jsx'
import HorarioResultado from './HorarioResultado.jsx'
import { apiGet, apiGetOpcional, apiPost, apiUpload } from '../api.js'

// ---------------------------------------------------------------------------
// Portal del estudiante.
//
// El ID y el nombre llegan desde la sesión iniciada en App.jsx. Al montar se
// consulta GET /api/estudiante/{id}/perfil:
//   * 404  -> se muestra el formulario para completar el perfil con ese ID.
//   * 200  -> resumen del perfil + generación del horario automático.
//
// Nunca se inventa ni se pide un ID nuevo: todas las escrituras usan el ID de
// la sesión. La matriz de estudio es opcional.
// ---------------------------------------------------------------------------

const EXTENSIONES_MATRIZ = /\.(csv|xlsx|xls)$/i
const TAMANO_MAXIMO_MATRIZ = 10 * 1024 * 1024 // 10 MB

// Mensaje exacto cuando el cambio de programa/jornada/sede obliga al backend
// a borrar las inscripciones y liberar los NRC del estudiante.
const MENSAJE_HORARIO_INVALIDADO =
  'Perfil actualizado. Tu horario anterior fue liberado, por favor genera uno nuevo.'

const PASOS = [
  { numero: 1, etiqueta: 'Datos' },
  { numero: 2, etiqueta: 'Resumen' },
  { numero: 3, etiqueta: 'Horario' },
]

function esObjeto(valor) {
  return Boolean(valor) && typeof valor === 'object' && !Array.isArray(valor)
}

function texto(valor) {
  if (valor == null) return ''
  return String(valor).trim()
}

function aEntero(valor) {
  if (typeof valor === 'number' && Number.isFinite(valor)) return Math.trunc(valor)
  if (typeof valor === 'string' && valor.trim() !== '') {
    const numero = Number(valor)
    if (Number.isFinite(numero)) return Math.trunc(numero)
  }
  return null
}

function mensajeDeError(error, respaldo) {
  const mensaje =
    error && typeof error.message === 'string' ? error.message.trim() : ''
  return mensaje || respaldo
}

function mensajeDeRespuesta(datos) {
  if (!esObjeto(datos)) return ''

  const bruto = datos.mensaje ?? datos.message ?? datos.error ?? datos.detail
  return typeof bruto === 'string' ? bruto.trim() : ''
}

/**
 * Interpreta banderas que el backend puede enviar como booleano, número o
 * string. Cualquier otro valor (incluido `undefined`) cuenta como false.
 */
function banderaVerdadera(valor) {
  if (valor === true || valor === 1) return true
  if (typeof valor === 'string') {
    const limpio = valor.trim().toLowerCase()
    return limpio === 'true' || limpio === '1'
  }
  return false
}

// ---------------------------------------------------------------------------
// Catálogos
// ---------------------------------------------------------------------------

/**
 * El backend puede devolver opciones como strings o como objetos
 * ({id, nombre}, {codigo, descripcion}, etc.). Se normalizan a
 * {valor, etiqueta}, que es lo que consumen los <select>.
 */
function normalizarOpciones(lista) {
  if (!Array.isArray(lista)) return []

  const opciones = []
  const vistos = new Set()

  for (const item of lista) {
    if (item == null) continue

    let valor = ''
    let etiqueta = ''

    if (typeof item === 'string' || typeof item === 'number') {
      valor = texto(item)
      etiqueta = valor
    } else if (esObjeto(item)) {
      const bruto =
        item.id ??
        item.value ??
        item.valor ??
        item.codigo ??
        item.codigo_programa ??
        item.id_programa ??
        item.id_jornada ??
        item.id_sede ??
        item.nombre ??
        item.nombre_programa ??
        item.nombre_jornada ??
        item.nombre_sede

      valor = texto(bruto)
      etiqueta =
        texto(
          item.nombre ??
            item.nombre_programa ??
            item.nombre_jornada ??
            item.nombre_sede ??
            item.descripcion ??
            item.label ??
            item.etiqueta ??
            bruto,
        ) || valor
    }

    if (!valor || vistos.has(valor)) continue

    vistos.add(valor)
    opciones.push({ valor, etiqueta: etiqueta || valor })
  }

  return opciones
}

function extraerCatalogos(datos) {
  if (!esObjeto(datos)) return { programas: [], jornadas: [], sedes: [] }

  const base = esObjeto(datos.catalogos)
    ? datos.catalogos
    : esObjeto(datos.datos)
      ? { ...datos, ...datos.datos }
      : datos

  return {
    programas: normalizarOpciones(base.programas ?? base.programa),
    jornadas: normalizarOpciones(base.jornadas ?? base.jornada),
    sedes: normalizarOpciones(base.sedes ?? base.sede),
  }
}

function etiquetaDe(opciones, valor) {
  const buscado = texto(valor)
  if (!buscado) return ''

  const clave = buscado.toLowerCase()
  const encontrada = opciones.find(
    (opcion) =>
      opcion.valor === buscado || opcion.etiqueta.toLowerCase() === clave,
  )

  return encontrada ? encontrada.etiqueta : ''
}

// ---------------------------------------------------------------------------
// Perfil
// ---------------------------------------------------------------------------

function valorCrudo(valor) {
  if (!esObjeto(valor)) return valor
  return valor.id ?? valor.codigo ?? valor.nombre ?? valor.descripcion ?? ''
}

function textoCampo(valor) {
  if (esObjeto(valor)) {
    return texto(
      valor.nombre ??
        valor.nombre_programa ??
        valor.nombre_jornada ??
        valor.nombre_sede ??
        valor.descripcion ??
        valor.label ??
        valor.codigo ??
        valor.id,
    )
  }
  return texto(valor)
}

/** Posibles valores de un campo que puede venir como id, código u objeto. */
function candidatosValor(valor) {
  if (esObjeto(valor)) {
    return [
      valor.id,
      valor.value,
      valor.valor,
      valor.codigo,
      valor.codigo_programa,
      valor.id_programa,
      valor.id_jornada,
      valor.id_sede,
      valor.nombre,
      valor.nombre_programa,
      valor.nombre_jornada,
      valor.nombre_sede,
      valor.descripcion,
    ]
      .map(texto)
      .filter(Boolean)
  }

  const simple = texto(valor)
  return simple ? [simple] : []
}

/**
 * Convierte el valor devuelto por el backend al `valor` que usa el <select>
 * del catálogo, probando tanto por id/código como por etiqueta.
 */
function resolverValor(opciones, valor) {
  const candidatos = candidatosValor(valor)
  if (candidatos.length === 0) return ''

  for (const candidato of candidatos) {
    const clave = candidato.toLowerCase()
    const encontrada =
      opciones.find((opcion) => opcion.valor === candidato) ||
      opciones.find((opcion) => opcion.etiqueta.toLowerCase() === clave)

    if (encontrada) return encontrada.valor
  }

  return candidatos[0]
}

function valoresDesdePerfil(datos, catalogos) {
  if (!esObjeto(datos)) return { programa: '', jornada: '', sede: '' }

  let base = { ...datos }
  if (esObjeto(datos.resumen)) base = { ...base, ...datos.resumen }
  if (esObjeto(datos.perfil)) base = { ...base, ...datos.perfil }

  const programaCrudo = base.programa ?? base.programa_id ?? base.id_programa
  const jornadaCruda = base.jornada ?? base.jornada_id ?? base.id_jornada
  const sedeCruda = base.sede ?? base.sede_id ?? base.id_sede

  return {
    programa: resolverValor(catalogos.programas || [], programaCrudo),
    jornada: resolverValor(catalogos.jornadas || [], jornadaCruda),
    sede: resolverValor(catalogos.sedes || [], sedeCruda),
  }
}

function normalizarMateria(item) {
  if (typeof item === 'string' || typeof item === 'number') {
    const codigo = texto(item)
    return codigo ? { codigo, nombre: '' } : null
  }

  if (!esObjeto(item)) return null

  const codigo = texto(
    item.codigo ??
      item.codigo_materia ??
      item.codigo_alfanumerico ??
      item.codigo_curso ??
      item.materia ??
      item.nombre,
  )
  const nombre = texto(
    item.nombre ?? item.nombre_materia ?? item.nombre_curso ?? item.materia_nombre,
  )

  if (!codigo && !nombre) return null

  return { codigo: codigo || nombre, nombre: nombre && nombre !== codigo ? nombre : '' }
}

function normalizarLista(valor) {
  if (!Array.isArray(valor)) return []

  const vistos = new Set()
  const lista = []

  valor.forEach((item) => {
    const materia = normalizarMateria(item)
    if (!materia) return

    const clave = materia.codigo.toUpperCase()
    if (vistos.has(clave)) return

    vistos.add(clave)
    lista.push(materia)
  })

  return lista
}

/**
 * Normaliza la respuesta de GET /api/estudiante/{id}/perfil (y la de
 * POST /api/estudiante/perfil) a la forma que consume la UI.
 */
function normalizarPerfil(datos, opciones = {}) {
  if (!esObjeto(datos)) return null

  let base = { ...datos }
  if (esObjeto(datos.resumen)) base = { ...base, ...datos.resumen }
  if (esObjeto(datos.perfil)) base = { ...base, ...datos.perfil }

  const carrera = textoCampo(
    base.carrera ?? base.nombre_carrera ?? base.carrera_nombre,
  )

  const programaCrudo = base.programa ?? base.programa_id ?? base.id_programa
  const jornadaCruda = base.jornada ?? base.jornada_id ?? base.id_jornada
  const sedeCruda = base.sede ?? base.sede_id ?? base.id_sede

  const programa =
    etiquetaDe(opciones.programas || [], valorCrudo(programaCrudo)) ||
    textoCampo(programaCrudo) ||
    opciones.programa ||
    ''
  const jornada =
    etiquetaDe(opciones.jornadas || [], valorCrudo(jornadaCruda)) ||
    textoCampo(jornadaCruda) ||
    opciones.jornada ||
    ''
  const sede =
    etiquetaDe(opciones.sedes || [], valorCrudo(sedeCruda)) ||
    textoCampo(sedeCruda) ||
    opciones.sede ||
    ''

  const semestre = texto(
    base.semestre ?? base.semestre_actual ?? base.semestreActual,
  )

  const aprobadas = normalizarLista(
    base.aprobadas ??
      base.materias_aprobadas ??
      base.materiasAprobadas ??
      base.historial,
  )
  const porVer = normalizarLista(
    base.por_ver ??
      base.porVer ??
      base.materias_por_ver ??
      base.materias_pendientes ??
      base.pendientes ??
      base.materias_por_cursar,
  )

  const totalAprobadas =
    aEntero(
      base.total_aprobadas ?? base.totalAprobadas ?? base.cantidad_aprobadas,
    ) ?? aprobadas.length
  const totalPorVer =
    aEntero(
      base.total_por_ver ??
        base.totalPorVer ??
        base.cantidad_por_ver ??
        base.total_pendientes,
    ) ?? porVer.length

  return {
    carrera: carrera || programa,
    programa,
    jornada,
    sede,
    semestre,
    aprobadas,
    porVer,
    totalAprobadas,
    totalPorVer,
  }
}

// ---------------------------------------------------------------------------
// Horario
// ---------------------------------------------------------------------------

function extraerHorario(datos) {
  if (Array.isArray(datos)) return datos.filter(esObjeto)
  if (!esObjeto(datos)) return []

  const claves = ['horario', 'materias', 'asignadas']
  for (const clave of claves) {
    if (Array.isArray(datos[clave])) return datos[clave].filter(esObjeto)
  }

  if (esObjeto(datos.resultado)) return extraerHorario(datos.resultado)

  return []
}

function normalizarNoAsignada(item) {
  if (typeof item === 'string' || typeof item === 'number') {
    const materia = texto(item)
    return materia
      ? {
          materia,
          codigo: '',
          motivo: 'No se pudo asignar con las reglas actuales del horario.',
        }
      : null
  }

  if (!esObjeto(item)) return null

  const materia = textoCampo(
    item.materia ?? item.nombre ?? item.nombre_materia ?? item.curso,
  )
  const codigo = texto(
    item.codigo ?? item.codigo_materia ?? item.codigo_alfanumerico,
  )
  const motivo = texto(
    item.motivo ??
      item.razon ??
      item.razón ??
      item.reason ??
      item.detalle ??
      item.mensaje,
  )

  if (!materia && !codigo) return null

  return {
    materia: materia || codigo,
    codigo,
    motivo: motivo || 'El servidor no reportó un motivo.',
  }
}

function extraerNoAsignadas(datos) {
  if (!esObjeto(datos)) return []

  const claves = [
    'no_asignadas',
    'materias_no_asignadas',
    'noAsignadas',
    'no_asignados',
  ]

  for (const clave of claves) {
    if (Array.isArray(datos[clave])) {
      return datos[clave].map(normalizarNoAsignada).filter(Boolean)
    }
  }

  if (esObjeto(datos.resultado)) return extraerNoAsignadas(datos.resultado)

  return []
}

/**
 * Normaliza la respuesta de GET /api/estudiante/horario/{id} (y la del POST)
 * a la forma que consume <HorarioResultado/>.
 *
 * GET devuelve {estudiante_id, periodo, total, horario: [...]} y
 * POST agrega {ok, ya_existe, mensaje, no_asignadas, resumen}.
 */
function normalizarHorarioGuardado(datos) {
  if (!esObjeto(datos)) return null

  const horario = extraerHorario(datos)
  const total = aEntero(datos.total)

  return {
    horario,
    noAsignadas: extraerNoAsignadas(datos),
    resumen: esObjeto(datos.resumen) ? datos.resumen : null,
    periodo: texto(datos.periodo),
    total: total ?? horario.length,
  }
}

/**
 * El backend responde `ya_existe: true` cuando el estudiante ya tenía un
 * horario guardado. Algunas versiones antiguas solo devolvían `mensaje` junto
 * con el horario: también se considera un horario existente para no volver a
 * generarlo ni mostrar un error.
 */
function esHorarioYaExistente(datos, horario) {
  if (!esObjeto(datos)) return false
  if (datos.ya_existe === true) return true
  if (datos.ya_existe === false) return false

  return Boolean(mensajeDeRespuesta(datos)) && horario.length > 0
}

// ---------------------------------------------------------------------------
// Indicador de pasos
// ---------------------------------------------------------------------------

function IndicadorPasos({ actual }) {
  return (
    <ol
      aria-label="Progreso del proceso"
      className="flex flex-wrap items-center gap-1.5"
    >
      {PASOS.map((item, indice) => {
        const completado = item.numero < actual
        const activo = item.numero === actual

        return (
          <li key={item.numero} className="flex items-center gap-1.5">
            {indice > 0 && (
              <span aria-hidden="true" className="text-gray-300">
                ›
              </span>
            )}
            <span
              aria-current={activo ? 'step' : undefined}
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${
                activo
                  ? 'border-indigo-200 bg-indigo-50 text-indigo-700'
                  : completado
                    ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                    : 'border-gray-200 bg-white text-gray-400'
              }`}
            >
              <span
                aria-hidden="true"
                className={`flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${
                  activo
                    ? 'bg-indigo-600 text-white'
                    : completado
                      ? 'bg-emerald-500 text-white'
                      : 'bg-gray-100 text-gray-400'
                }`}
              >
                {completado ? '✓' : item.numero}
              </span>
              {item.etiqueta}
            </span>
          </li>
        )
      })}
    </ol>
  )
}

// ---------------------------------------------------------------------------
// Componente principal
// ---------------------------------------------------------------------------

export default function EstudiantePanel({
  estudianteId,
  nombre,
  onLogout,
} = {}) {
  const id = Number(estudianteId)
  const idValido = Number.isSafeInteger(id) && id > 0

  const [catalogos, setCatalogos] = useState({
    programas: [],
    jornadas: [],
    sedes: [],
  })
  const [cargandoCatalogos, setCargandoCatalogos] = useState(true)
  const [errorCatalogos, setErrorCatalogos] = useState(null)
  const [recargaCatalogos, setRecargaCatalogos] = useState(0)

  const [perfil, setPerfil] = useState(null)
  const [perfilCrudo, setPerfilCrudo] = useState(null)
  const [cargandoPerfil, setCargandoPerfil] = useState(true)
  const [errorCarga, setErrorCarga] = useState(null)
  const [recargaPerfil, setRecargaPerfil] = useState(0)

  const [editando, setEditando] = useState(false)
  const [valores, setValores] = useState({
    programa: '',
    jornada: '',
    sede: '',
  })
  const [archivo, setArchivo] = useState(null)
  const [guardando, setGuardando] = useState(false)
  const [errorGuardado, setErrorGuardado] = useState(null)
  const [mensajeOk, setMensajeOk] = useState(null)

  const [resultado, setResultado] = useState(null)
  const [generando, setGenerando] = useState(false)
  const [errorHorario, setErrorHorario] = useState(null)

  // Horario ya persistido en el backend (se muestra al recargar la página).
  const [horarioGuardado, setHorarioGuardado] = useState(null)
  const [cargandoHorario, setCargandoHorario] = useState(false)
  const [errorHorarioGuardado, setErrorHorarioGuardado] = useState(null)
  const [recargaHorario, setRecargaHorario] = useState(0)
  const [mensajeExistente, setMensajeExistente] = useState(null)
  // Evita mostrar el horario de una sesión anterior si cambia el estudiante.
  const estudianteHorarioRef = useRef(null)

  const hayPerfil = perfil != null

  // Catálogo de programas, jornadas y sedes.
  useEffect(() => {
    let vigente = true

    setCargandoCatalogos(true)
    setErrorCatalogos(null)

    apiGet('/api/catalogos')
      .then((datos) => {
        if (!vigente) return

        const normalizados = extraerCatalogos(datos)
        setCatalogos(normalizados)

        if (
          normalizados.programas.length === 0 ||
          normalizados.jornadas.length === 0 ||
          normalizados.sedes.length === 0
        ) {
          setErrorCatalogos(
            'El catálogo no trajo todas las opciones necesarias (programas, jornadas y sedes). Inténtalo de nuevo.',
          )
        }
      })
      .catch((error) => {
        if (!vigente) return
        setErrorCatalogos(
          mensajeDeError(
            error,
            'No se pudo cargar el catálogo académico. Revisa tu conexión e inténtalo de nuevo.',
          ),
        )
      })
      .finally(() => {
        if (vigente) setCargandoCatalogos(false)
      })

    return () => {
      vigente = false
    }
  }, [recargaCatalogos])

  // Perfil del estudiante autenticado. 404 => perfil aún no creado.
  useEffect(() => {
    if (!idValido) {
      setCargandoPerfil(false)
      setErrorCarga(
        'No hay una sesión de estudiante válida. Vuelve a iniciar sesión para continuar.',
      )
      setPerfil(null)
      setPerfilCrudo(null)
      return undefined
    }

    let vigente = true

    setCargandoPerfil(true)
    setErrorCarga(null)

    apiGetOpcional(`/api/estudiante/${id}/perfil`)
      .then((datos) => {
        if (!vigente) return

        if (datos == null) {
          setPerfil(null)
          setPerfilCrudo(null)
          setEditando(false)
          setValores({ programa: '', jornada: '', sede: '' })
          setArchivo(null)
          setMensajeOk(null)
          setResultado(null)
          setErrorHorario(null)
          return
        }

        setPerfilCrudo(datos)
        setPerfil(normalizarPerfil(datos, catalogos))
        setEditando(false)
        setValores({ programa: '', jornada: '', sede: '' })
      })
      .catch((error) => {
        if (!vigente) return
        setErrorCarga(
          mensajeDeError(
            error,
            'No se pudo cargar tu perfil. Revisa tu conexión e inténtalo de nuevo.',
          ),
        )
        setPerfil(null)
        setPerfilCrudo(null)
      })
      .finally(() => {
        if (vigente) setCargandoPerfil(false)
      })

    return () => {
      vigente = false
    }
    // `catalogos` se omite a propósito: el perfil no debe recargarse cuando
    // llegan los catálogos. El efecto siguiente vuelve a normalizar las
    // etiquetas cuando eso ocurre.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [estudianteId, recargaPerfil, idValido])

  // Si el perfil llegó antes que los catálogos, se recalcula con las etiquetas
  // legibles de programa/jornada/sede.
  useEffect(() => {
    if (!perfilCrudo) return
    setPerfil(normalizarPerfil(perfilCrudo, catalogos))
  }, [perfilCrudo, catalogos])

  // Horario ya guardado. Se consulta al montar y cuando el perfil queda
  // disponible (por ejemplo, justo después de completarlo), de modo que al
  // recargar la página el horario se vea de inmediato.
  useEffect(() => {
    if (!idValido) {
      setHorarioGuardado(null)
      setCargandoHorario(false)
      setErrorHorarioGuardado(null)
      return undefined
    }

    let vigente = true

    if (estudianteHorarioRef.current !== id) {
      estudianteHorarioRef.current = id
      setHorarioGuardado(null)
      setResultado(null)
      setMensajeExistente(null)
      setErrorHorario(null)
    }

    setCargandoHorario(true)
    setErrorHorarioGuardado(null)

    apiGetOpcional(`/api/estudiante/horario/${id}`)
      .then((datos) => {
        if (!vigente) return

        const normalizado = normalizarHorarioGuardado(datos)
        // Una respuesta vacía no debe borrar un horario recién generado que
        // aún no había llegado a persistirse en esta petición.
        if (normalizado && normalizado.horario.length > 0) {
          setHorarioGuardado(normalizado)
        }
      })
      .catch((error) => {
        if (!vigente) return
        setErrorHorarioGuardado(
          mensajeDeError(
            error,
            'No se pudo recuperar tu horario guardado. Inténtalo de nuevo.',
          ),
        )
      })
      .finally(() => {
        if (vigente) setCargandoHorario(false)
      })

    return () => {
      vigente = false
    }
  }, [estudianteId, idValido, hayPerfil, recargaHorario])

  const mostrarFormulario =
    !cargandoPerfil && !errorCarga && (!hayPerfil || editando)

  const horarioExistente = Boolean(
    horarioGuardado &&
      Array.isArray(horarioGuardado.horario) &&
      horarioGuardado.horario.length > 0,
  )
  // Lo recién generado (con sus no asignadas) tiene prioridad sobre lo que
  // devuelve el GET de persistencia.
  const resultadoMostrado = resultado ?? horarioGuardado
  const periodoHorario =
    texto(horarioGuardado?.periodo) ||
    texto(resultado?.periodo) ||
    texto(horarioGuardado?.resumen?.periodo) ||
    texto(resultado?.resumen?.periodo)

  const puedeGenerar =
    hayPerfil &&
    !editando &&
    !cargandoPerfil &&
    !errorCarga &&
    !guardando &&
    !horarioExistente
  const pasoActual = mostrarFormulario
    ? 1
    : resultadoMostrado || generando
      ? 3
      : 2
  const nombreMostrado = texto(nombre) || 'Estudiante'

  const ayudaHorario = editando
    ? 'Guarda los cambios de tu perfil para generar un horario con los datos actualizados.'
    : !hayPerfil
      ? 'Completa y guarda tu perfil para habilitar el horario automático.'
      : ''

  function actualizarCampo(campo, valor) {
    setValores((anteriores) => ({ ...anteriores, [campo]: valor }))
    if (errorGuardado) setErrorGuardado(null)
  }

  function seleccionarArchivo(nuevoArchivo) {
    setArchivo(nuevoArchivo)
    if (errorGuardado) setErrorGuardado(null)
  }

  async function enviarPerfil(evento) {
    if (evento) evento.preventDefault()
    if (guardando) return

    if (!idValido) {
      setErrorGuardado(
        'No hay una sesión de estudiante válida. Vuelve a iniciar sesión.',
      )
      return
    }

    const faltante = !valores.programa
      ? 'Selecciona un programa académico.'
      : !valores.jornada
        ? 'Selecciona una jornada.'
        : !valores.sede
          ? 'Selecciona una sede.'
          : ''

    if (faltante) {
      setErrorGuardado(faltante)
      return
    }

    if (archivo) {
      if (!EXTENSIONES_MATRIZ.test(archivo.name)) {
        setErrorGuardado(
          'La matriz de estudio debe ser un archivo CSV o Excel (.csv, .xlsx o .xls).',
        )
        return
      }
      if (archivo.size === 0) {
        setErrorGuardado(
          'El archivo de la matriz está vacío. Selecciona otro archivo.',
        )
        return
      }
      if (archivo.size > TAMANO_MAXIMO_MATRIZ) {
        setErrorGuardado(
          'La matriz supera el tamaño máximo permitido (10 MB). Elige un archivo más pequeño.',
        )
        return
      }
    }

    const formData = new FormData()
    formData.append('programa', valores.programa)
    formData.append('jornada', valores.jornada)
    formData.append('sede', valores.sede)
    if (archivo) formData.append('matriz', archivo)
    // Siempre el ID de la sesión: el perfil se crea o actualiza para este
    // estudiante, nunca para otro.
    formData.append('estudiante_id', String(id))

    const actualizando = hayPerfil
    setGuardando(true)
    setErrorGuardado(null)
    setMensajeOk(null)

    try {
      const datos = await apiUpload('/api/estudiante/perfil', formData)

      if (esObjeto(datos) && datos.ok === false) {
        throw new Error(
          mensajeDeRespuesta(datos) ||
            'El servidor no pudo guardar tu perfil. Revisa tus datos e inténtalo de nuevo.',
        )
      }

      // El perfil debe quedar asociado al ID de la sesión, nunca a otro.
      const idDevuelto = esObjeto(datos) ? aEntero(datos.estudiante_id) : null
      if (idDevuelto != null && idDevuelto !== id) {
        throw new Error(
          'El servidor guardó el perfil con un identificador distinto al de tu sesión. Vuelve a iniciar sesión e inténtalo de nuevo.',
        )
      }

      // Se vuelve a consultar el perfil para mostrar los datos canónicos
      // (materias, totales y semestre calculados por el backend).
      let fresco = null
      try {
        fresco = await apiGetOpcional(`/api/estudiante/${id}/perfil`)
      } catch {
        fresco = null
      }

      const basePrevia = esObjeto(perfilCrudo) ? perfilCrudo : {}
      const base = {
        ...basePrevia,
        ...(esObjeto(datos) ? datos : {}),
        programa: valores.programa,
        jornada: valores.jornada,
        sede: valores.sede,
      }
      const baseFinal = esObjeto(fresco) ? { ...base, ...fresco } : base

      const perfilNormalizado = normalizarPerfil(baseFinal, {
        ...catalogos,
        programa:
          etiquetaDe(catalogos.programas, valores.programa) || valores.programa,
        jornada:
          etiquetaDe(catalogos.jornadas, valores.jornada) || valores.jornada,
        sede: etiquetaDe(catalogos.sedes, valores.sede) || valores.sede,
      })

      if (!perfilNormalizado) {
        throw new Error(
          'El servidor no devolvió los datos de tu perfil. Inténtalo de nuevo.',
        )
      }

      setPerfilCrudo(baseFinal)
      setPerfil(perfilNormalizado)
      setValores(valoresDesdePerfil(baseFinal, catalogos))
      setArchivo(null)
      setEditando(false)
      setResultado(null)
      setErrorHorario(null)

      // Cambiar programa/jornada/sede invalida las inscripciones: el backend
      // borra el horario y libera los NRC. La bandera puede faltar en
      // respuestas antiguas, así que se asume false y se conserva el horario
      // (y el mensaje) exactamente como antes.
      const horarioInvalidado = esObjeto(datos)
        ? banderaVerdadera(datos.horario_invalidado)
        : false

      if (horarioInvalidado) {
        // Al vaciar `horarioGuardado`, `horarioExistente` pasa a false y
        // `puedeGenerar` vuelve a habilitar "Generar Horario Automático".
        setHorarioGuardado(null)
        setMensajeExistente(null)
        setErrorHorarioGuardado(null)
        setMensajeOk(MENSAJE_HORARIO_INVALIDADO)
      } else {
        setMensajeOk(
          actualizando
            ? 'Perfil actualizado correctamente. Revisa tus datos y genera tu horario.'
            : 'Perfil completado correctamente. Ya puedes generar tu horario automático.',
        )
      }
    } catch (error) {
      setErrorGuardado(
        mensajeDeError(
          error,
          'No se pudo guardar tu perfil. Revisa tus datos e inténtalo de nuevo.',
        ),
      )
    } finally {
      setGuardando(false)
    }
  }

  async function generarHorario() {
    if (!puedeGenerar || generando) return

    setGenerando(true)
    setErrorHorario(null)
    setErrorHorarioGuardado(null)
    setMensajeExistente(null)
    setResultado(null)

    try {
      const datos = await apiPost('/api/horario/automatico', {
        estudiante_id: id,
      })

      if (datos == null || typeof datos !== 'object') {
        throw new Error(
          'El servidor no devolvió ningún horario. Inténtalo de nuevo.',
        )
      }

      if (esObjeto(datos) && datos.ok === false) {
        throw new Error(
          mensajeDeRespuesta(datos) ||
            'El servidor no pudo generar el horario. Inténtalo de nuevo.',
        )
      }

      const horario = extraerHorario(datos)
      const normalizado = {
        horario,
        noAsignadas: extraerNoAsignadas(datos),
        resumen: esObjeto(datos.resumen) ? datos.resumen : null,
        periodo: texto(datos.periodo),
        total: aEntero(datos.total) ?? horario.length,
      }
      const yaExistia = esHorarioYaExistente(datos, horario)

      setResultado(normalizado)

      if (horario.length > 0) {
        setHorarioGuardado({
          ...normalizado,
          // Las no asignadas solo vienen en la respuesta del POST; el GET de
          // persistencia no las incluye.
          noAsignadas: [],
        })
      }

      if (yaExistia) {
        setMensajeExistente(
          mensajeDeRespuesta(datos) ||
            'Ya tenías un horario generado para este periodo. Te mostramos el horario guardado.',
        )
      }

      // Refresca el horario persistido para confirmar lo que quedó guardado.
      try {
        const guardado = normalizarHorarioGuardado(
          await apiGetOpcional(`/api/estudiante/horario/${id}`),
        )

        if (guardado && guardado.horario.length > 0) {
          setHorarioGuardado(guardado)
        }
      } catch (errorRefresco) {
        setErrorHorarioGuardado(
          mensajeDeError(
            errorRefresco,
            'El horario se generó, pero no se pudo confirmar el horario guardado.',
          ),
        )
      }
    } catch (error) {
      setErrorHorario(
        mensajeDeError(
          error,
          'No se pudo generar el horario automático. Inténtalo de nuevo.',
        ),
      )
    } finally {
      setGenerando(false)
    }
  }

  function cambiarDatos() {
    if (!hayPerfil) return

    setValores(valoresDesdePerfil(perfilCrudo, catalogos))
    setArchivo(null)
    setErrorGuardado(null)
    setMensajeOk(null)
    setResultado(null)
    setErrorHorario(null)
    setMensajeExistente(null)
    setEditando(true)
  }

  function cancelarEdicion() {
    setValores(valoresDesdePerfil(perfilCrudo, catalogos))
    setArchivo(null)
    setErrorGuardado(null)
    setEditando(false)
  }

  return (
    <section aria-labelledby="panel-estudiante-titulo" className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">
            Portal del estudiante
          </p>
          <h2
            id="panel-estudiante-titulo"
            className="mt-1 text-2xl font-bold text-gray-900"
          >
            Hola, {nombreMostrado}
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-gray-500">
            Estás identificado con el ID{' '}
            <span className="font-mono font-semibold text-gray-700">
              #{idValido ? String(id) : '—'}
            </span>
            . Revisa tu perfil y genera tu horario sin cruces.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1 text-xs font-semibold text-indigo-700">
            ID {idValido ? String(id) : '—'}
          </span>
          <IndicadorPasos actual={pasoActual} />
        </div>
      </header>

      {errorCarga ? (
        <Banner
          tipo="error"
          titulo="No se pudo cargar tu perfil"
          accion={
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => setRecargaPerfil((n) => n + 1)}
                className="rounded-md border border-red-300 bg-white px-3 py-1 text-xs font-semibold text-red-700 transition-colors hover:bg-red-50"
              >
                Reintentar
              </button>
              {!idValido && typeof onLogout === 'function' && (
                <button
                  type="button"
                  onClick={onLogout}
                  className="rounded-md border border-gray-300 bg-white px-3 py-1 text-xs font-semibold text-gray-600 transition-colors hover:bg-gray-50"
                >
                  Volver al inicio
                </button>
              )}
            </div>
          }
        >
          {errorCarga}
        </Banner>
      ) : cargandoPerfil ? (
        <div className="rounded-2xl border border-gray-200 bg-white p-10 shadow-sm">
          <Spinner
            label="Buscando tu perfil académico…"
            className="h-8 w-8"
            centrado
          />
        </div>
      ) : (
        <>
          {mostrarFormulario && !hayPerfil && (
            <Banner tipo="info" titulo="Completa tu perfil">
              Aún no tenemos tus datos académicos. Selecciona programa, jornada
              y sede; si la tienes, adjunta también tu matriz de estudio. Se
              guardará con tu ID #{idValido ? String(id) : '—'}.
            </Banner>
          )}

          {mostrarFormulario ? (
            <PerfilForm
              catalogo={catalogos}
              catalogoCargando={cargandoCatalogos}
              catalogoError={errorCatalogos}
              onReintentarCatalogo={() =>
                setRecargaCatalogos((n) => n + 1)
              }
              valores={valores}
              onCambio={actualizarCampo}
              archivo={archivo}
              onArchivo={seleccionarArchivo}
              enviando={guardando}
              error={errorGuardado}
              onCerrarError={() => setErrorGuardado(null)}
              estudianteId={editando && hayPerfil ? id : null}
              editando={editando && hayPerfil}
              onCancelarEdicion={hayPerfil ? cancelarEdicion : undefined}
              onSubmit={enviarPerfil}
            />
          ) : (
            <ResumenPerfil
              perfil={perfil}
              estudianteId={idValido ? id : null}
              mensajeOk={mensajeOk}
              onCerrarMensaje={() => setMensajeOk(null)}
              onCambiarDatos={cambiarDatos}
            />
          )}

          {hayPerfil && (
            <section
              aria-labelledby="paso-horario-titulo"
              className="rounded-2xl border-2 border-indigo-100 bg-white p-5 shadow-sm sm:p-6"
            >
              <div className="flex items-start gap-3">
                <span
                  aria-hidden="true"
                  className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-base font-bold ${
                    horarioExistente
                      ? 'bg-emerald-600 text-white'
                      : puedeGenerar || generando
                        ? 'bg-indigo-600 text-white'
                        : 'bg-gray-100 text-gray-400'
                  }`}
                >
                  {horarioExistente ? '✓' : '3'}
                </span>
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h2
                      id="paso-horario-titulo"
                      className="text-lg font-semibold text-gray-800"
                    >
                      Generar horario automático
                    </h2>
                    <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-indigo-700">
                      Paso 3
                    </span>
                  </div>
                  <p className="mt-0.5 text-sm text-gray-500">
                    El sistema buscará los NRC compatibles con tus materias
                    aprobadas y sin cruces de horario.
                  </p>
                </div>
              </div>

              <button
                type="button"
                onClick={generarHorario}
                disabled={!puedeGenerar || generando}
                aria-describedby={
                  horarioExistente
                    ? 'horario-nota'
                    : ayudaHorario
                      ? 'horario-ayuda'
                      : undefined
                }
                className="mt-4 inline-flex w-full items-center justify-center gap-3 rounded-2xl bg-gradient-to-r from-indigo-600 via-violet-600 to-indigo-600 px-6 py-5 text-lg font-bold text-white shadow-lg shadow-indigo-200/70 transition hover:from-indigo-500 hover:via-violet-500 hover:to-indigo-500 focus:outline-none focus-visible:ring-4 focus-visible:ring-indigo-200 active:from-indigo-700 active:via-violet-700 active:to-indigo-700 disabled:cursor-not-allowed disabled:from-gray-300 disabled:via-gray-300 disabled:to-gray-300 disabled:text-gray-500 disabled:shadow-none"
              >
                {generando ? (
                  <>
                    <Spinner label={null} className="h-5 w-5" />
                    Generando horario…
                  </>
                ) : horarioExistente ? (
                  <>
                    <span aria-hidden="true" className="text-xl">
                      ✓
                    </span>
                    Horario ya generado
                  </>
                ) : (
                  <>
                    <span aria-hidden="true" className="text-xl">
                      ⚡
                    </span>
                    Generar Horario Automático
                  </>
                )}
              </button>

              {horarioExistente && (
                <p
                  id="horario-nota"
                  className="mt-3 flex flex-wrap items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-800"
                >
                  <span aria-hidden="true">✓</span>
                  Ya existe un horario guardado para el periodo
                  <span className="font-bold">
                    {periodoHorario || 'actual'}
                  </span>
                  . Se mostrará cada vez que ingreses.
                </p>
              )}

              {ayudaHorario && (
                <p
                  id="horario-ayuda"
                  className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-800"
                >
                  {ayudaHorario}
                </p>
              )}

              {mensajeExistente && (
                <div className="mt-4">
                  <Banner
                    tipo="info"
                    titulo="Tu horario ya estaba generado"
                    onCerrar={() => setMensajeExistente(null)}
                  >
                    <p>{mensajeExistente}</p>
                    {periodoHorario && (
                      <p className="mt-1 text-xs">
                        Periodo:{' '}
                        <span className="font-semibold">{periodoHorario}</span>
                      </p>
                    )}
                  </Banner>
                </div>
              )}

              {errorHorario && (
                <div className="mt-4">
                  <Banner
                    tipo="error"
                    titulo="No se pudo generar tu horario"
                    onCerrar={() => setErrorHorario(null)}
                  >
                    {errorHorario}
                  </Banner>
                </div>
              )}

              {errorHorarioGuardado && (
                <div className="mt-4">
                  <Banner
                    tipo="aviso"
                    titulo="No se pudo recuperar tu horario guardado"
                    accion={
                      <button
                        type="button"
                        onClick={() => setRecargaHorario((n) => n + 1)}
                        disabled={cargandoHorario}
                        className="rounded-md border border-amber-300 bg-white px-3 py-1 text-xs font-semibold text-amber-800 transition-colors hover:bg-amber-50 disabled:opacity-50"
                      >
                        Reintentar
                      </button>
                    }
                    onCerrar={() => setErrorHorarioGuardado(null)}
                  >
                    {errorHorarioGuardado}
                  </Banner>
                </div>
              )}

              {cargandoHorario && !resultadoMostrado && !generando ? (
                <div className="mt-5 rounded-xl border border-indigo-100 bg-indigo-50/60 px-4 py-8 text-indigo-700">
                  <Spinner
                    centrado
                    label="Recuperando tu horario guardado…"
                    className="h-8 w-8"
                  />
                </div>
              ) : (
                <HorarioResultado
                  resultado={resultadoMostrado}
                  generando={generando}
                />
              )}
            </section>
          )}
        </>
      )}
    </section>
  )
}
