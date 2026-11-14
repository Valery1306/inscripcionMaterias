import { useEffect, useState } from 'react'
import Banner from './Banner.jsx'
import Spinner from './Spinner.jsx'
import OfertaTableAdmin from './OfertaTableAdmin.jsx'
import { apiGet, listaDeObjetos } from '../api.js'

// ---------------------------------------------------------------------------
// Pestaña «Ver Oferta Académica».
//
// Carga GET /api/admin/oferta y entrega las filas a OfertaTableAdmin en modo
// solo lectura. El componente de UX se encarga de la paginación; aquí solo
// se normalizan los datos y se gestionan los estados de carga y error.
// ---------------------------------------------------------------------------

const DIAS_POR_NOMBRE = {
  lunes: 1,
  martes: 2,
  miercoles: 3,
  jueves: 4,
  viernes: 5,
  sabado: 6,
  domingo: 7,
}

const DIAS_CORTOS = {
  1: 'Lun',
  2: 'Mar',
  3: 'Mié',
  4: 'Jue',
  5: 'Vie',
  6: 'Sáb',
  7: 'Dom',
}

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

function normalizarDia(valor) {
  const cadena = texto(valor)
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

function formatearHora(valor) {
  const cadena = texto(valor)
  const coincidencia = cadena.match(/^(\d{1,2}):(\d{2})/)
  if (!coincidencia) return cadena
  return `${coincidencia[1].padStart(2, '0')}:${coincidencia[2]}`
}

function formatearHorario(horario) {
  if (!Array.isArray(horario)) return ''

  return horario
    .filter(esObjeto)
    .map((sesion) => {
      const dia = nombreDia(sesion.dia ?? sesion.dia_nombre)
      const inicio = formatearHora(
        sesion.hora_inicio ?? sesion.horaInicio ?? sesion.inicio,
      )
      const fin = formatearHora(
        sesion.hora_fin ?? sesion.horaFin ?? sesion.fin,
      )

      if (!dia && !inicio && !fin) return ''
      return `${dia || '¿Día?'} ${inicio || '—'}-${fin || '—'}`
    })
    .filter(Boolean)
    .join(', ')
}

function calcularSaldo(cupo, inscritos) {
  const c = Number(cupo)
  const i = Number(inscritos)
  if (!Number.isFinite(c) || !Number.isFinite(i)) return ''
  if (cupo === '' || inscritos === '' || cupo == null || inscritos == null) {
    return ''
  }
  return c - i
}

function extraerFilas(datos) {
  if (Array.isArray(datos)) return listaDeObjetos(datos)

  if (esObjeto(datos)) {
    const claves = ['oferta', 'nrcs', 'datos', 'items', 'filas', 'rows']
    for (const clave of claves) {
      if (Array.isArray(datos[clave])) return listaDeObjetos(datos[clave])
    }
  }

  return []
}

function normalizarOferta(datos) {
  return extraerFilas(datos).map((fila, indice) => {
    const horario = Array.isArray(fila.horario)
      ? fila.horario.filter(esObjeto)
      : []
    const horarioTexto =
      texto(fila.horarioTexto) ||
      texto(fila.horario_texto) ||
      (typeof fila.horario === 'string' ? texto(fila.horario) : '')

    return {
      ...fila,
      _key:
        fila._key ??
        `oferta-${texto(fila.id_nrc) || String(indice)}-${String(indice)}`,
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
      horarioTexto: horarioTexto || formatearHorario(horario),
    }
  })
}

export default function OfertaTab() {
  const [nrcs, setNrcs] = useState([])
  const [cargando, setCargando] = useState(true)
  const [error, setError] = useState(null)
  const [recarga, setRecarga] = useState(0)

  useEffect(() => {
    let vigente = true

    setCargando(true)
    setError(null)

    apiGet('/api/admin/oferta')
      .then((datos) => {
        if (!vigente) return
        setNrcs(normalizarOferta(datos))
      })
      .catch((err) => {
        if (!vigente) return
        setError(
          texto(err && err.message) ||
            'No se pudo cargar la oferta académica. Inténtalo de nuevo.',
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

  const hayFilas = nrcs.length > 0

  return (
    <div className="space-y-3" aria-busy={cargando}>
      {error && (
        <Banner
          tipo="error"
          titulo="No se pudo cargar la oferta académica"
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

      {cargando && hayFilas && (
        <div className="flex items-center justify-end gap-2 text-xs text-gray-400">
          <Spinner label={null} className="h-3.5 w-3.5" />
          Actualizando la oferta…
        </div>
      )}

      {cargando && !hayFilas ? (
        <div className="rounded-xl border border-gray-200 bg-white p-10 shadow-sm">
          <Spinner
            label="Cargando la oferta académica…"
            className="h-8 w-8"
            centrado
          />
        </div>
      ) : null}

      {hayFilas || (!cargando && !error) ? (
        <OfertaTableAdmin
          filas={nrcs}
          soloLectura
          onRecargar={recargar}
        />
      ) : null}
    </div>
  )
}
