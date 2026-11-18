import { useCallback, useState } from 'react'
import UploadCard from './UploadCard.jsx'
import AdminReglas from './AdminReglas.jsx'
import OfertaTab from './OfertaTab.jsx'
import MallaTab from './MallaTab.jsx'
import ListaEstudiantes from './ListaEstudiantes.jsx'

// ---------------------------------------------------------------------------
// Panel de administración con pestañas (sin recargar la página):
//   1. Carga de Archivos   -> PDF de malla + Excel/CSV de oferta
//   2. Ver Oferta          -> oferta académica en modo solo lectura
//   3. Ver Malla           -> malla curricular agrupada por semestre
//   4. Editar Reglas       -> prerrequisitos (AdminReglas)
//   5. Lista de Estudiantes-> estudiantes registrados, solo lectura
//
// Cada pestaña monta únicamente su contenido, así que las consultas se hacen
// al abrirla y no se acumulan peticiones innecesarias.
// ---------------------------------------------------------------------------

const PESTANAS = [
  { id: 'carga', etiqueta: 'Carga de Archivos', icono: '📤' },
  { id: 'oferta', etiqueta: 'Ver Oferta Académica', icono: '📚' },
  { id: 'malla', etiqueta: 'Ver Malla Curricular', icono: '🎓' },
  { id: 'reglas', etiqueta: 'Editar Reglas', icono: '🧩' },
  { id: 'estudiantes', etiqueta: 'Lista de Estudiantes', icono: '👥' },
]

const ESTADISTICAS_MALLA = [
  { clave: 'cursos', etiqueta: 'Cursos' },
  { clave: 'programas', etiqueta: 'Programas' },
]

const ESTADISTICAS_OFERTA = [
  { clave: 'cursos', etiqueta: 'Cursos' },
  { clave: 'nrcs', etiqueta: 'NRC' },
  { clave: 'sesiones', etiqueta: 'Sesiones' },
  { clave: 'docentes', etiqueta: 'Docentes' },
  { clave: 'sedes', etiqueta: 'Sedes' },
]

export default function AdminPanel({ onLogout, onSubida } = {}) {
  const [pestana, setPestana] = useState('carga')

  const manejarSubida = useCallback(
    async (tipo, datos) => {
      if (typeof onSubida !== 'function') return

      try {
        await onSubida(datos, tipo)
      } catch {
        // Un fallo del callback del contenedor no invalida la carga.
      }
    },
    [onSubida],
  )

  return (
    <div className="space-y-6">
      <header className="card relative flex flex-wrap items-center justify-between gap-4 overflow-hidden p-6 sm:p-7">
        <span
          aria-hidden="true"
          className="absolute inset-y-0 left-0 w-1.5 bg-gradient-to-b from-indigo-500 to-indigo-700"
        />
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">
            Administración
          </p>
          <h2 className="mt-1 text-2xl font-bold tracking-tight text-gray-900 sm:text-3xl">
            Panel de administración
          </h2>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-gray-500">
            Carga la malla y la oferta, consulta los datos académicos y
            administra las reglas de prerrequisitos.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-2 rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs font-semibold text-indigo-700">
            <span aria-hidden="true">🛡️</span>
            Administrador
          </span>
          <button
            type="button"
            onClick={() => onLogout?.()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3.5 py-2 text-sm font-semibold text-gray-700 shadow-sm transition-colors hover:border-red-300 hover:bg-red-50 hover:text-red-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-200"
          >
            <span aria-hidden="true">⎋</span>
            Salir
          </button>
        </div>
      </header>

      <nav className="z-20 sm:sticky sm:top-[76px] rounded-2xl border border-gray-200/80 bg-white/90 p-1.5 shadow-md backdrop-blur">
        <div
          role="tablist"
          aria-label="Secciones del panel de administración"
          className="flex gap-1 overflow-x-auto"
        >
          {PESTANAS.map((item) => {
            const activa = pestana === item.id

            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                id={`admin-tab-${item.id}`}
                aria-selected={activa}
                aria-controls="admin-panel"
                onClick={() => setPestana(item.id)}
                className={`inline-flex flex-1 shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-xl px-4 py-2.5 text-sm font-semibold transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300 ${
                  activa
                    ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/25'
                    : 'text-gray-600 hover:bg-indigo-50 hover:text-indigo-700'
                }`}
              >
                <span aria-hidden="true">{item.icono}</span>
                {item.etiqueta}
              </button>
            )
          })}
        </div>
      </nav>

      <div
        id="admin-panel"
        role="tabpanel"
        aria-labelledby={`admin-tab-${pestana}`}
        tabIndex={0}
        className="focus:outline-none"
      >
        {pestana === 'carga' && (
          <section
            aria-label="Carga de archivos"
            className="space-y-4"
          >
            <div className="rounded-xl border border-indigo-100 bg-indigo-50/70 px-4 py-3 text-sm text-indigo-900">
              <p className="font-semibold">Orden recomendado de carga</p>
              <p className="mt-1 text-indigo-800/90">
                Primero sube la malla curricular (PDF) y después la oferta
                académica (Excel o CSV). Al terminar, revisa los resultados en
                «Ver Oferta Académica» o en «Lista de Estudiantes».
              </p>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <UploadCard
                titulo="Malla Curricular (PDF)"
                descripcion="Sube el PDF de la malla para extraer cursos, programas, créditos y prerrequisitos."
                accept=".pdf"
                endpoint="/api/admin/upload-malla"
                icono="📘"
                textoBoton="Subir malla"
                estadisticas={ESTADISTICAS_MALLA}
                onSubida={(datos) => manejarSubida('malla', datos)}
              />

              <UploadCard
                titulo="Oferta Académica (Excel)"
                descripcion="Sube el Excel o CSV de la oferta para cargar cursos, NRC, sesiones, docentes y sedes."
                accept=".xlsx,.xls,.csv"
                endpoint="/api/admin/upload-oferta"
                icono="🗂️"
                textoBoton="Subir oferta"
                estadisticas={ESTADISTICAS_OFERTA}
                onSubida={(datos) => manejarSubida('oferta', datos)}
              />
            </div>
          </section>
        )}

        {pestana === 'oferta' && <OfertaTab />}

        {pestana === 'malla' && <MallaTab />}

        {pestana === 'reglas' && (
          <section aria-label="Reglas de prerrequisitos">
            <AdminReglas />
          </section>
        )}

        {pestana === 'estudiantes' && <ListaEstudiantes />}
      </div>
    </div>
  )
}
