import { useCallback, useEffect, useRef, useState } from 'react'
import Banner from './Banner.jsx'
import Spinner from './Spinner.jsx'
import MallaTable from './MallaTable.jsx'
import OfertaTableAdmin from './OfertaTableAdmin.jsx'
import { apiGet, apiPut, listaDeObjetos } from '../api.js'

// Dashboard editable del administrador. Carga la malla y la oferta desde los
// endpoints /api/admin/*, mantiene las filas en estado local y guarda ambas
// listas con PUT al pulsar «Guardar Cambios».

const DIAS_CORTOS = {
  1: 'Lun',
  2: 'Mar',
  3: 'Mié',
  4: 'Jue',
  5: 'Vie',
  6: 'Sáb',
  7: 'Dom',
}

const DIAS_POR_NOMBRE = {
  lunes: 1,
  lun: 1,
  martes: 2,
  mar: 2,
  miercoles: 3,
  mie: 3,
  jueves: 4,
  jue: 4,
  viernes: 5,
  vie: 5,
  sabado: 6,
  sab: 6,
  domingo: 7,
  dom: 7,
}

function texto(valor) {
  return valor == null ? '' : String(valor)
}

function sinAcentos(valor) {
  return texto(valor)
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
}

function aNumero(valor) {
  if (valor == null) return null
  if (typeof valor === 'string' && valor.trim() === '') return null
  const numero = Number(valor)
  return Number.isFinite(numero) ? numero : null
}

function validarNumero(valor, { entero = false, minimo = null, requerido = false } = {}) {
  const cadena = texto(valor).trim()

  if (cadena === '') {
    return requerido ? { ok: false, valor: null } : { ok: true, valor: null }
  }

  const numero = Number(cadena)
  if (!Number.isFinite(numero)) return { ok: false, valor: null }
  if (entero && !Number.isInteger(numero)) return { ok: false, valor: null }
  if (minimo != null && numero < minimo) return { ok: false, valor: null }

  return { ok: true, valor: numero }
}

function parsearLista(valor) {
  if (Array.isArray(valor)) {
    return [
      ...new Set(
        valor
          .map((item) => texto(item).trim().toUpperCase())
          .filter(Boolean),
      ),
    ]
  }

  return [
    ...new Set(
      texto(valor)
        .split(/[,;]/)
        .map((item) => item.trim().toUpperCase())
        .filter(Boolean),
    ),
  ]
}

function normalizarDia(valor) {
  const cadena = texto(valor).trim()
  if (!cadena) return null

  const numero = Number(cadena)
  if (Number.isInteger(numero) && numero >= 1 && numero <= 7) return numero

  const clave = sinAcentos(cadena).toLowerCase().replace(/[.\s]/g, '')
  return DIAS_POR_NOMBRE[clave] ?? null
}

function nombreDia(valor) {
  const dia = normalizarDia(valor)
  return dia ? DIAS_CORTOS[dia] : texto(valor)
}

function normalizarHora(valor) {
  const coincidencia = texto(valor)
    .trim()
    .match(/^(\d{1,2}):(\d{2})(?::\d{2})?$/)

  if (!coincidencia) return null

  const horas = Number(coincidencia[1])
  const minutos = Number(coincidencia[2])
  if (horas < 0 || horas > 23 || minutos < 0 || minutos > 59) return null

  return `${String(horas).padStart(2, '0')}:${String(minutos).padStart(2, '0')}`
}

function formatearHora(valor) {
  return normalizarHora(valor) || texto(valor)
}

function formatearHorarioTexto(horario) {
  if (!Array.isArray(horario)) return ''

  return horario
    .filter((sesion) => sesion && typeof sesion === 'object')
    .map((sesion) => {
      const dia = nombreDia(sesion.dia)
      const inicio = formatearHora(sesion.hora_inicio)
      const fin = formatearHora(sesion.hora_fin)

      if (!dia && !inicio && !fin) return ''
      return `${dia || '¿Día?'} ${inicio || '—'}-${fin || '—'}`
    })
    .filter(Boolean)
    .join(', ')
}

// Convierte el texto editable «Lun 07:00-09:00, Mié 09:00-11:00» en sesiones.
// Conserva salón y edificio de las sesiones originales por posición.
function parsearHorarioTexto(valor, horarioOriginal) {
  const limpio = texto(valor).trim()
  if (!limpio) return { ok: true, sesiones: [] }

  const partes = limpio
    .split(/[,;\n·]+/)
    .map((parte) => parte.trim())
    .filter(Boolean)

  if (partes.length === 0) return { ok: true, sesiones: [] }

  const originales = Array.isArray(horarioOriginal)
    ? horarioOriginal.filter((sesion) => sesion && typeof sesion === 'object')
    : []
  const sesiones = []

  for (let indice = 0; indice < partes.length; indice += 1) {
    const parte = partes[indice]
    const coincidencia = parte.match(
      /^([a-záéíóúñ]+\.?|\d)\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*(?:-|–|—|a)\s*(\d{1,2}:\d{2}(?::\d{2})?)$/i,
    )

    if (!coincidencia) {
      return {
        ok: false,
        sesiones: [],
        error: `«${parte}» no tiene el formato esperado (ej. «Lun 07:00-09:00»).`,
      }
    }

    const dia = normalizarDia(coincidencia[1])
    if (!dia) {
      return {
        ok: false,
        sesiones: [],
        error: `no reconozco el día «${coincidencia[1]}» en «${parte}».`,
      }
    }

    const inicio = normalizarHora(coincidencia[2])
    const fin = normalizarHora(coincidencia[3])
    if (!inicio || !fin) {
      return {
        ok: false,
        sesiones: [],
        error: `la hora de «${parte}» no es válida (usa HH:MM).`,
      }
    }

    if (inicio >= fin) {
      return {
        ok: false,
        sesiones: [],
        error: `en «${parte}» la hora de inicio debe ser anterior a la de fin.`,
      }
    }

    const base = originales[indice] || {}
    const sesion = {
      dia,
      hora_inicio: inicio,
      hora_fin: fin,
    }

    if (base.salon != null) sesion.salon = base.salon
    if (base.edificio != null) sesion.edificio = base.edificio

    sesiones.push(sesion)
  }

  return { ok: true, sesiones }
}

function calcularSaldo(cupo, inscritos) {
  const cupoNumero = aNumero(cupo)
  const inscritosNumero = aNumero(inscritos)

  if (cupoNumero == null || inscritosNumero == null) return ''
  return cupoNumero - inscritosNumero
}

function extraerLista(datos, tipo) {
  if (Array.isArray(datos)) return listaDeObjetos(datos)

  if (datos && typeof datos === 'object' && !Array.isArray(datos)) {
    const claves =
      tipo === 'malla'
        ? ['malla', 'cursos', 'datos', 'items']
        : ['oferta', 'nrcs', 'datos', 'items']

    for (const clave of claves) {
      if (Array.isArray(datos[clave])) return listaDeObjetos(datos[clave])
    }
  }

  return []
}

function normalizarMalla(datos) {
  return extraerLista(datos, 'malla').map((fila, indice) => {
    const prerrequisitos = parsearLista(fila.prerrequisitos)

    return {
      ...fila,
      _key: `malla-${indice}`,
      programa: texto(fila.programa),
      sede: texto(fila.sede),
      semestre: fila.semestre ?? '',
      codigo: texto(fila.codigo),
      nombre: texto(fila.nombre),
      creditos: fila.creditos ?? '',
      horas: fila.horas ?? '',
      prerrequisitos,
      prerrequisitosTexto: prerrequisitos.join(', '),
    }
  })
}

function normalizarOferta(datos) {
  return extraerLista(datos, 'oferta').map((fila, indice) => {
    const horario = Array.isArray(fila.horario)
      ? fila.horario.filter(
          (sesion) => sesion && typeof sesion === 'object',
        )
      : []

    return {
      ...fila,
      _key: `oferta-${indice}`,
      id_nrc: fila.id_nrc ?? '',
      curso_id: texto(fila.curso_id),
      materia: texto(fila.materia),
      docente_nombre: texto(fila.docente_nombre),
      sede_nombre: texto(fila.sede_nombre),
      jornada: texto(fila.jornada),
      modalidad: texto(fila.modalidad),
      creditos: fila.creditos ?? '',
      cupo_max: fila.cupo_max ?? '',
      inscritos: fila.inscritos ?? '',
      saldo:
        fila.saldo != null
          ? fila.saldo
          : calcularSaldo(fila.cupo_max, fila.inscritos),
      estado: texto(fila.estado),
      periodo: texto(fila.periodo),
      horario,
      horarioTexto: formatearHorarioTexto(horario),
    }
  })
}

function prepararMalla(filas) {
  const errores = []

  const payload = filas.map((fila, indice) => {
    const numeroFila = indice + 1
    const codigo = texto(fila.codigo).trim()
    const nombre = texto(fila.nombre).trim()
    const etiqueta = codigo ? `fila ${numeroFila} (${codigo})` : `fila ${numeroFila}`

    if (!codigo) errores.push(`Malla · ${etiqueta}: el código es obligatorio.`)
    if (!nombre) errores.push(`Malla · ${etiqueta}: el nombre es obligatorio.`)

    const semestre = validarNumero(fila.semestre, {
      entero: true,
      minimo: 1,
      requerido: true,
    })
    if (!semestre.ok) {
      errores.push(
        `Malla · ${etiqueta}: el semestre debe ser un entero mayor o igual a 1.`,
      )
    }

    const creditos = validarNumero(fila.creditos, { entero: true, minimo: 0 })
    if (!creditos.ok) {
      errores.push(
        `Malla · ${etiqueta}: los créditos deben ser un entero mayor o igual a 0.`,
      )
    }

    const horas = validarNumero(fila.horas, { entero: true, minimo: 0 })
    if (!horas.ok) {
      errores.push(
        `Malla · ${etiqueta}: las horas deben ser un entero mayor o igual a 0.`,
      )
    }

    return {
      programa: texto(fila.programa).trim(),
      sede: texto(fila.sede).trim(),
      semestre: semestre.valor,
      codigo: codigo.toUpperCase(),
      nombre,
      creditos: creditos.valor,
      horas: horas.valor,
      prerrequisitos: parsearLista(fila.prerrequisitosTexto),
    }
  })

  return { payload, errores }
}

function prepararOferta(filas) {
  const errores = []
  const nrcsVistos = new Set()

  const payload = filas.map((fila, indice) => {
    const numeroFila = indice + 1
    const nrcTexto = texto(fila.id_nrc).trim()
    const etiqueta = `NRC ${nrcTexto || `(fila ${numeroFila})`}`

    const nrc = validarNumero(fila.id_nrc, {
      entero: true,
      minimo: 0,
      requerido: true,
    })

    if (!nrc.ok || nrc.valor == null) {
      errores.push(
        `Oferta · ${etiqueta}: el NRC es obligatorio y debe ser un entero.`,
      )
    } else if (nrcsVistos.has(nrc.valor)) {
      errores.push(`Oferta · ${etiqueta}: el NRC está repetido en la tabla.`)
    } else {
      nrcsVistos.add(nrc.valor)
    }

    const cursoId = texto(fila.curso_id).trim()
    const materia = texto(fila.materia).trim()

    if (!cursoId) errores.push(`Oferta · ${etiqueta}: el curso es obligatorio.`)
    if (!materia) errores.push(`Oferta · ${etiqueta}: la materia es obligatoria.`)

    const cupo = validarNumero(fila.cupo_max, {
      entero: true,
      minimo: 0,
      requerido: true,
    })
    if (!cupo.ok) {
      errores.push(
        `Oferta · ${etiqueta}: el cupo debe ser un entero mayor o igual a 0.`,
      )
    }

    const inscritos = validarNumero(fila.inscritos, {
      entero: true,
      minimo: 0,
      requerido: true,
    })
    if (!inscritos.ok) {
      errores.push(
        `Oferta · ${etiqueta}: los inscritos deben ser un entero mayor o igual a 0.`,
      )
    }

    const saldo = validarNumero(fila.saldo, { entero: true })
    if (!saldo.ok) {
      errores.push(
        `Oferta · ${etiqueta}: el saldo debe ser un entero (puede ser negativo si hay sobrecupo).`,
      )
    }

    const horario = parsearHorarioTexto(fila.horarioTexto, fila.horario)
    if (!horario.ok) {
      errores.push(`Oferta · ${etiqueta}: ${horario.error}`)
    }

    const saldoCalculado =
      cupo.valor != null && inscritos.valor != null
        ? cupo.valor - inscritos.valor
        : null
    const creditos = validarNumero(fila.creditos, { entero: true, minimo: 0 })

    return {
      id_nrc: nrc.valor,
      curso_id: cursoId.toUpperCase(),
      materia,
      creditos: creditos.ok ? creditos.valor : null,
      docente_nombre: texto(fila.docente_nombre).trim(),
      sede_nombre: texto(fila.sede_nombre).trim(),
      jornada: texto(fila.jornada).trim(),
      modalidad: texto(fila.modalidad).trim(),
      cupo_max: cupo.valor,
      inscritos: inscritos.valor,
      saldo: saldo.valor ?? saldoCalculado,
      estado: texto(fila.estado).trim(),
      periodo: texto(fila.periodo).trim(),
      horario: horario.ok ? horario.sesiones : [],
    }
  })

  return { payload, errores }
}

export default function AdminDashboard({ refreshKey = 0 }) {
  const [malla, setMalla] = useState([])
  const [oferta, setOferta] = useState([])
  const [cargandoMalla, setCargandoMalla] = useState(true)
  const [cargandoOferta, setCargandoOferta] = useState(true)
  const [errorMalla, setErrorMalla] = useState(null)
  const [errorOferta, setErrorOferta] = useState(null)
  const [guardando, setGuardando] = useState(false)
  const [mensaje, setMensaje] = useState(null)

  // Descarta respuestas viejas si se recarga varias veces seguidas.
  const solicitudMallaRef = useRef(0)
  const solicitudOfertaRef = useRef(0)

  const cargarMalla = useCallback(async () => {
    const solicitud = ++solicitudMallaRef.current
    setCargandoMalla(true)
    setErrorMalla(null)

    try {
      const datos = await apiGet('/api/admin/malla')
      if (solicitud !== solicitudMallaRef.current) return false
      setMalla(normalizarMalla(datos))
      return true
    } catch (error) {
      if (solicitud !== solicitudMallaRef.current) return false
      setErrorMalla(
        error?.message || 'No se pudo cargar la malla curricular.',
      )
      return false
    } finally {
      if (solicitud === solicitudMallaRef.current) setCargandoMalla(false)
    }
  }, [])

  const cargarOferta = useCallback(async () => {
    const solicitud = ++solicitudOfertaRef.current
    setCargandoOferta(true)
    setErrorOferta(null)

    try {
      const datos = await apiGet('/api/admin/oferta')
      if (solicitud !== solicitudOfertaRef.current) return false
      setOferta(normalizarOferta(datos))
      return true
    } catch (error) {
      if (solicitud !== solicitudOfertaRef.current) return false
      setErrorOferta(
        error?.message || 'No se pudo cargar la oferta académica.',
      )
      return false
    } finally {
      if (solicitud === solicitudOfertaRef.current) setCargandoOferta(false)
    }
  }, [])

  useEffect(() => {
    cargarMalla()
    cargarOferta()
  }, [cargarMalla, cargarOferta, refreshKey])

  function editarMalla(indice, campo, valor) {
    setMensaje(null)
    setMalla((actual) =>
      actual.map((fila, posicion) =>
        posicion === indice ? { ...fila, [campo]: valor } : fila,
      ),
    )
  }

  function editarOferta(indice, campo, valor) {
    setMensaje(null)
    setOferta((actual) =>
      actual.map((fila, posicion) => {
        if (posicion !== indice) return fila

        const nueva = { ...fila, [campo]: valor }

        // Mantiene el saldo coherente al cambiar cupo o inscritos.
        if (campo === 'cupo_max' || campo === 'inscritos') {
          nueva.saldo = calcularSaldo(nueva.cupo_max, nueva.inscritos)
        }

        return nueva
      }),
    )
  }

  async function guardarCambios() {
    if (guardando) return

    const mallaPreparada = prepararMalla(malla)
    const ofertaPreparada = prepararOferta(oferta)
    const errores = [...mallaPreparada.errores, ...ofertaPreparada.errores]

    if (errores.length > 0) {
      const primeros = errores.slice(0, 6)
      const restantes = errores.length - primeros.length
      setMensaje({
        tipo: 'error',
        titulo: 'Revisa los datos antes de guardar',
        texto: `${primeros.join(' ')}${
          restantes > 0 ? ` … y ${restantes} error(es) más.` : ''
        }`,
      })
      return
    }

    const tareas = []

    if (malla.length > 0) {
      tareas.push({
        clave: 'malla',
        etiqueta: 'Malla',
        ejecutar: () => apiPut('/api/admin/malla', mallaPreparada.payload),
      })
    }

    if (oferta.length > 0) {
      tareas.push({
        clave: 'oferta',
        etiqueta: 'Oferta',
        ejecutar: () => apiPut('/api/admin/oferta', ofertaPreparada.payload),
      })
    }

    if (tareas.length === 0) {
      setMensaje({
        tipo: 'aviso',
        texto: 'No hay filas de malla ni de oferta para guardar.',
      })
      return
    }

    setGuardando(true)
    setMensaje(null)

    try {
      const resultados = await Promise.allSettled(
        tareas.map((tarea) => Promise.resolve().then(() => tarea.ejecutar())),
      )

      const exitos = []
      const fallos = []

      resultados.forEach((resultado, indice) => {
        const tarea = tareas[indice]
        if (resultado.status === 'fulfilled') exitos.push(tarea)
        else fallos.push({ ...tarea, error: resultado.reason })
      })

      // Solo se recargan las tablas que se guardaron bien: si una falló, se
      // conservan las ediciones locales para que el usuario pueda reintentar.
      const recargas = await Promise.all(
        exitos.map((tarea) =>
          tarea.clave === 'malla' ? cargarMalla() : cargarOferta(),
        ),
      )
      const recargaFallida = recargas.some((resultado) => !resultado)

      if (fallos.length === 0) {
        setMensaje({
          tipo: recargaFallida ? 'aviso' : 'ok',
          titulo: recargaFallida
            ? 'Cambios guardados con avisos'
            : 'Cambios guardados',
          texto: recargaFallida
            ? 'Los cambios se guardaron, pero no se pudo recargar alguna tabla. Pulsa «Recargar» para refrescarla.'
            : `${exitos
                .map((tarea) => tarea.etiqueta)
                .join(' y ')} ${
                exitos.length === 1 ? 'actualizada' : 'actualizadas'
              } correctamente.`,
        })
      } else {
        const detalle = fallos
          .map(
            (fallo) =>
              `${fallo.etiqueta}: ${fallo.error?.message || 'no se pudo guardar.'}`,
          )
          .join(' ')

        setMensaje({
          tipo: 'error',
          titulo:
            exitos.length === 0
              ? 'No se pudieron guardar los cambios'
              : 'Guardado parcial',
          texto: `${detalle}${
            exitos.length > 0
              ? ` Se guardaron: ${exitos
                  .map((tarea) => tarea.etiqueta)
                  .join(', ')}.`
              : ''
          }`,
        })
      }
    } finally {
      setGuardando(false)
    }
  }

  const totalMalla = malla.length
  const totalOferta = oferta.length

  return (
    <div id="admin-dashboard" className="space-y-5">
      <section className="rounded-xl border border-indigo-100 bg-white p-4 shadow-sm sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-semibold text-gray-800">
                Dashboard de datos
              </h2>
              <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700">
                {totalMalla} {totalMalla === 1 ? 'curso' : 'cursos'}
              </span>
              <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">
                {totalOferta} NRC
              </span>
            </div>
            <p className="mt-1 text-sm text-gray-500">
              Edita las tablas y guarda ambas listas en el backend. Los cambios
              no se envían hasta pulsar «Guardar Cambios».
            </p>
          </div>

          <button
            type="button"
            onClick={guardarCambios}
            disabled={guardando || (totalMalla === 0 && totalOferta === 0)}
            aria-busy={guardando}
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-indigo-600 px-5 py-2.5 font-semibold text-white shadow transition-colors hover:bg-indigo-500 active:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {guardando && <Spinner label={null} className="h-4 w-4" />}
            {guardando ? 'Guardando…' : 'Guardar Cambios'}
          </button>
        </div>

        <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-500">
          Se envían las listas completas de malla y oferta con PUT
          <span className="font-mono"> /api/admin/malla</span> y
          <span className="font-mono"> /api/admin/oferta</span>. Tras guardar,
          las tablas se recargan.
        </div>

        {mensaje && (
          <div className="mt-3">
            <Banner
              tipo={mensaje.tipo}
              titulo={mensaje.titulo}
              onCerrar={() => setMensaje(null)}
            >
              {mensaje.texto}
            </Banner>
          </div>
        )}
      </section>

      <MallaTable
        filas={malla}
        cargando={cargandoMalla}
        error={errorMalla}
        deshabilitado={guardando}
        onEditar={editarMalla}
        onRecargar={cargarMalla}
      />

      <OfertaTableAdmin
        filas={oferta}
        cargando={cargandoOferta}
        error={errorOferta}
        deshabilitado={guardando}
        onEditar={editarOferta}
        onRecargar={cargarOferta}
      />
    </div>
  )
}
