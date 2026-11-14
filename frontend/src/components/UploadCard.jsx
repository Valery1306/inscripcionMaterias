import { useId, useRef, useState } from 'react'
import Banner from './Banner.jsx'
import Spinner from './Spinner.jsx'
import { apiUpload } from '../api.js'

// Tarjeta genérica de carga de archivos del panel de administración.
// Todo pasa por src/api.js y por rutas relativas /api; el navegador arma el
// multipart/form-data con el campo `archivo`.

const CLAVES_RESUMEN = {
  cursos: [
    'cursos',
    'total_cursos',
    'cursos_insertados',
    'cursos_procesados',
    'cursos_cargados',
    'totalCursos',
  ],
  programas: [
    'programas',
    'total_programas',
    'programas_insertados',
    'programas_procesados',
    'totalProgramas',
  ],
  nrcs: [
    'nrcs',
    'total_nrcs',
    'nrcs_insertados',
    'nrcs_procesados',
    'nrcs_cargados',
    'totalNrcs',
  ],
  sesiones: [
    'sesiones',
    'total_sesiones',
    'sesiones_insertadas',
    'sesiones_procesadas',
    'sesiones_cargadas',
    'totalSesiones',
  ],
  docentes: [
    'docentes',
    'total_docentes',
    'docentes_insertados',
    'docentes_procesados',
    'totalDocentes',
  ],
  sedes: [
    'sedes',
    'total_sedes',
    'sedes_insertadas',
    'sedes_procesadas',
    'totalSedes',
  ],
}

function esObjeto(valor) {
  return Boolean(valor) && typeof valor === 'object' && !Array.isArray(valor)
}

// Acepta números, cadenas numéricas o arreglos (en ese caso usa su longitud).
function aCantidad(valor) {
  if (Array.isArray(valor)) return valor.length
  if (typeof valor === 'number' && Number.isFinite(valor)) return valor
  if (typeof valor === 'string' && valor.trim() !== '') {
    const numero = Number(valor)
    if (Number.isFinite(numero)) return numero
  }
  return null
}

function contarUnicos(valores) {
  return new Set(
    valores
      .map((valor) => (valor == null ? '' : String(valor).trim()))
      .filter(Boolean),
  ).size
}

// El backend puede responder los conteos en la raíz o dentro de un objeto
// anidado; se revisan todas las variantes habituales.
function fuentesDe(datos) {
  const fuentes = [datos]

  if (esObjeto(datos)) {
    const claves = ['resumen', 'resultado', 'estadisticas', 'detalle', 'datos']
    claves.forEach((clave) => {
      if (esObjeto(datos[clave])) fuentes.push(datos[clave])
    })
  }

  return fuentes.filter(esObjeto)
}

function buscarCantidad(fuentes, claves) {
  for (const fuente of fuentes) {
    for (const clave of claves) {
      const cantidad = aCantidad(fuente[clave])
      if (cantidad != null) return cantidad
    }
  }
  return null
}

function listaDeObjetosEn(datos, claves) {
  if (!esObjeto(datos)) return []

  for (const clave of claves) {
    if (Array.isArray(datos[clave])) {
      const objetos = datos[clave].filter(esObjeto)
      if (objetos.length > 0) return objetos
    }
  }

  return []
}

function extraerResumen(datos) {
  const fuentes = fuentesDe(datos)
  const resumen = {}

  Object.entries(CLAVES_RESUMEN).forEach(([nombre, claves]) => {
    resumen[nombre] = buscarCantidad(fuentes, claves)
  })

  // Si el backend devuelve las filas en vez de los conteos, se derivan aquí.
  const malla = listaDeObjetosEn(datos, ['malla', 'lista_malla', 'cursos'])
  if (malla.length > 0) {
    if (resumen.cursos == null) resumen.cursos = malla.length
    if (resumen.programas == null) {
      resumen.programas = contarUnicos(malla.map((fila) => fila.programa))
    }
  }

  const oferta = listaDeObjetosEn(datos, ['oferta', 'nrcs', 'lista_nrcs'])
  if (oferta.length > 0) {
    if (resumen.nrcs == null) resumen.nrcs = oferta.length
    if (resumen.cursos == null) {
      resumen.cursos = contarUnicos(
        oferta.map((fila) => fila.curso_id || fila.materia),
      )
    }
    if (resumen.sesiones == null) {
      resumen.sesiones = oferta.reduce(
        (total, fila) =>
          total + (Array.isArray(fila.horario) ? fila.horario.length : 0),
        0,
      )
    }
    if (resumen.docentes == null) {
      resumen.docentes = contarUnicos(
        oferta.map((fila) => fila.docente_nombre),
      )
    }
    if (resumen.sedes == null) {
      resumen.sedes = contarUnicos(oferta.map((fila) => fila.sede_nombre))
    }
  }

  return resumen
}

function mensajeDe(datos) {
  if (!esObjeto(datos)) return ''
  const mensaje = datos.mensaje ?? datos.message ?? datos.detail
  return typeof mensaje === 'string' ? mensaje.trim() : ''
}

function formatearPeso(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`

  const kb = bytes / 1024
  if (kb < 1024) return `${Math.max(1, Math.round(kb))} KB`

  return `${(kb / 1024).toFixed(1)} MB`
}

function extensionDe(nombre) {
  const punto = nombre.lastIndexOf('.')
  return punto >= 0 ? nombre.slice(punto + 1).toLowerCase() : ''
}

function Estadistica({ etiqueta, valor }) {
  return (
    <div
      className="rounded-xl border border-emerald-100 bg-white px-2.5 py-2.5 text-center shadow-sm"
      title={etiqueta}
    >
      <p className="text-xl font-bold tabular-nums text-emerald-700">
        {valor != null ? valor : '—'}
      </p>
      <p className="mt-0.5 truncate text-[11px] font-medium uppercase tracking-wide text-gray-500">
        {etiqueta}
      </p>
    </div>
  )
}

export default function UploadCard({
  titulo,
  descripcion,
  accept = '',
  endpoint,
  icono = '📄',
  textoBoton = 'Subir archivo',
  estadisticas = [],
  onSubida,
}) {
  const inputRef = useRef(null)
  const idArchivo = useId()
  const idAyuda = useId()

  const [archivo, setArchivo] = useState(null)
  const [subiendo, setSubiendo] = useState(false)
  const [error, setError] = useState(null)
  const [exito, setExito] = useState(null)
  const [resumen, setResumen] = useState(null)
  const [nombreSubido, setNombreSubido] = useState('')

  function seleccionarArchivo(evento) {
    const seleccionado =
      evento.target.files && evento.target.files[0]
        ? evento.target.files[0]
        : null

    setArchivo(seleccionado)
    setError(null)
    setExito(null)
    setResumen(null)
    setNombreSubido('')
  }

  function limpiarSeleccion() {
    setArchivo(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  async function subir(evento) {
    evento.preventDefault()

    if (subiendo) return

    setError(null)
    setExito(null)
    setResumen(null)
    setNombreSubido('')

    if (!archivo) {
      setError('Selecciona un archivo antes de continuar.')
      return
    }

    const nombre = archivo.name || ''
    const extension = extensionDe(nombre)
    const extensiones = (accept || '')
      .split(',')
      .map((valor) => valor.trim().toLowerCase().replace(/^\./, ''))
      .filter(Boolean)

    if (extensiones.length > 0 && !extensiones.includes(extension)) {
      setError(
        `El archivo debe tener una de estas extensiones: ${extensiones
          .map((valor) => `.${valor}`)
          .join(', ')}.`,
      )
      return
    }

    if (archivo.size === 0) {
      setError('El archivo está vacío. Selecciona un archivo con datos.')
      return
    }

    const formData = new FormData()
    formData.append('archivo', archivo)

    const nombreArchivo = nombre
    setSubiendo(true)

    try {
      const datos = await apiUpload(endpoint, formData, {
        // Los PDF/Excel pueden tardar más que una petición normal.
        timeoutMs: 180000,
      })

      setResumen(extraerResumen(datos))
      setNombreSubido(nombreArchivo)
      setExito(
        mensajeDe(datos) || 'El archivo se cargó y procesó correctamente.',
      )
      limpiarSeleccion()

      if (typeof onSubida === 'function') {
        try {
          await onSubida(datos)
        } catch {
          // Un fallo al refrescar el dashboard no invalida la subida.
        }
      }
    } catch (err) {
      setError(err.message || 'No se pudo subir ni procesar el archivo.')
    } finally {
      setSubiendo(false)
    }
  }

  const hayConteos =
    resumen != null &&
    estadisticas.some(({ clave }) => resumen[clave] != null)
  const claseGrid =
    estadisticas.length >= 5
      ? 'grid-cols-2 sm:grid-cols-3'
      : estadisticas.length === 3
        ? 'grid-cols-3'
        : 'grid-cols-2'

  const formatos =
    (accept || '')
      .split(',')
      .map((valor) => valor.trim())
      .filter(Boolean)
      .join(' · ') || 'Cualquier formato'

  const extensionSeleccionada = archivo ? extensionDe(archivo.name) : ''

  return (
    <section className="flex h-full flex-col rounded-2xl border border-gray-200 bg-white p-4 shadow-sm transition-shadow hover:shadow-md sm:p-6">
      <div className="flex items-start gap-3">
        <span
          aria-hidden="true"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-50 to-indigo-100 text-2xl ring-1 ring-inset ring-indigo-100"
        >
          {icono}
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-lg font-semibold text-gray-800">{titulo}</h2>
            <span
              className="break-all font-mono text-[11px] text-gray-400"
              title={endpoint}
            >
              {endpoint}
            </span>
          </div>
          <p className="mt-1 text-sm text-gray-500">{descripcion}</p>
        </div>
      </div>

      {error && (
        <div className="mt-4">
          <Banner tipo="error" onCerrar={() => setError(null)}>
            {error}
          </Banner>
        </div>
      )}

      {exito && (
        <div className="mt-4">
          <Banner
            tipo="ok"
            titulo="Carga completada"
            onCerrar={() => setExito(null)}
          >
            {nombreSubido ? `${nombreSubido}. ` : ''}
            {exito}
          </Banner>
        </div>
      )}

      {hayConteos && (
        <div className="mt-4 rounded-xl border border-emerald-100 bg-emerald-50/40 p-3">
          <div className="mb-2 flex items-center gap-2">
            <span aria-hidden="true" className="text-sm">
              ✅
            </span>
            <p className="text-xs font-semibold uppercase tracking-wide text-emerald-800">
              Resumen de la carga
            </p>
          </div>
          <div className={`grid gap-2 ${claseGrid}`}>
            {estadisticas.map(({ clave, etiqueta }) => (
              <Estadistica
                key={clave}
                etiqueta={etiqueta}
                valor={resumen[clave]}
              />
            ))}
          </div>
        </div>
      )}

      <form
        onSubmit={subir}
        aria-busy={subiendo}
        className="mt-5 flex flex-1 flex-col gap-4"
      >
        <div>
          <p className="mb-1.5 text-sm font-semibold text-gray-700">Archivo</p>

          <label
            htmlFor={idArchivo}
            className={`flex items-center gap-3 rounded-xl border-2 border-dashed px-4 py-3 transition-colors focus-within:border-indigo-500 focus-within:ring-2 focus-within:ring-indigo-200 ${
              subiendo
                ? 'cursor-not-allowed border-gray-200 bg-gray-50 opacity-70'
                : archivo
                  ? 'cursor-pointer border-indigo-300 bg-indigo-50/50 hover:border-indigo-400'
                  : 'cursor-pointer border-gray-300 bg-gray-50 hover:border-indigo-400 hover:bg-indigo-50/40'
            }`}
          >
            <span
              aria-hidden="true"
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white text-xl shadow-sm ring-1 ring-inset ring-gray-200"
            >
              {archivo ? '📄' : '📎'}
            </span>

            <span className="min-w-0 flex-1">
              <span
                className="block truncate text-sm font-semibold text-gray-800"
                title={archivo ? archivo.name : undefined}
              >
                {archivo ? archivo.name : 'Selecciona un archivo'}
              </span>
              <span className="mt-0.5 block truncate text-xs text-gray-500">
                {archivo
                  ? [
                      formatearPeso(archivo.size),
                      extensionSeleccionada
                        ? `.${extensionSeleccionada}`
                        : '',
                    ]
                      .filter(Boolean)
                      .join(' · ')
                  : `Formatos: ${formatos}`}
              </span>
            </span>

            <span
              aria-hidden="true"
              className="hidden shrink-0 items-center rounded-lg bg-white px-3 py-1.5 text-xs font-semibold text-indigo-700 shadow-sm ring-1 ring-inset ring-indigo-200 sm:inline-flex"
            >
              {archivo ? 'Cambiar' : 'Elegir'}
            </span>

            <input
              id={idArchivo}
              ref={inputRef}
              type="file"
              accept={accept || undefined}
              onChange={seleccionarArchivo}
              disabled={subiendo}
              aria-describedby={idAyuda}
              className="sr-only"
            />
          </label>

          <p id={idAyuda} className="mt-1.5 text-xs text-gray-400">
            {formatos}. Se envía como multipart/form-data (campo «archivo»).
          </p>
        </div>

        {subiendo && (
          <div
            aria-live="polite"
            className="rounded-xl border border-indigo-100 bg-indigo-50 px-3 py-3"
          >
            <Spinner
              label="Subiendo y procesando el archivo…"
              className="h-4 w-4 text-indigo-600"
            />
            <p className="mt-1 pl-6 text-xs text-indigo-700/80">
              Esto puede tardar unos segundos según el tamaño del archivo.
            </p>
            <div
              aria-hidden="true"
              className="mt-2 h-1.5 overflow-hidden rounded-full bg-indigo-100"
            >
              <div className="h-full w-1/3 animate-pulse rounded-full bg-indigo-500" />
            </div>
          </div>
        )}

        <div className="mt-auto flex flex-wrap items-center gap-3">
          <button
            type="submit"
            disabled={subiendo || !archivo}
            aria-busy={subiendo}
            className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 px-5 py-2.5 font-semibold text-white shadow-sm transition-all hover:bg-indigo-500 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 active:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-40 sm:w-auto"
          >
            {subiendo ? (
              <Spinner label={null} className="h-4 w-4" />
            ) : (
              <span aria-hidden="true">⬆️</span>
            )}
            {subiendo ? 'Procesando…' : textoBoton}
          </button>
          <span className="text-xs text-gray-400">
            {archivo && !subiendo
              ? `${formatearPeso(archivo.size)} listos para enviar`
              : 'El botón se habilita al elegir un archivo.'}
          </span>
        </div>
      </form>
    </section>
  )
}
