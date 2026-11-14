import { useEffect, useState } from 'react'
import AdminPanel from './components/AdminPanel.jsx'
import EstudiantePanel from './components/EstudiantePanel.jsx'
import Login from './components/Login.jsx'
import { API_BASE } from './api.js'

// ---------------------------------------------------------------------------
// Shell de la aplicación.
//
// La sesión se guarda en localStorage para sobrevivir a una recarga. Según el
// rol, se muestra uno de los dos paneles autocontenidos:
//   * admin      -> AdminPanel (cargas, oferta, reglas, estudiantes)
//   * estudiante -> EstudiantePanel (perfil + horario automático)
//
// Todas las peticiones al backend viven en los paneles y usan src/api.js con
// rutas relativas /api.
// ---------------------------------------------------------------------------

const CLAVE_SESION = 'inscripcion-materias.sesion'
const ROLES = new Set(['admin', 'estudiante'])

function texto(valor) {
  if (valor == null) return ''
  return String(valor).trim()
}

/**
 * Valida y normaliza una sesión (recién creada o recuperada de localStorage).
 * Devuelve null si no es utilizable para que App vuelva al login.
 */
function normalizarSesion(valor) {
  if (!valor || typeof valor !== 'object' || Array.isArray(valor)) return null

  const rol = valor.rol
  if (!ROLES.has(rol)) return null

  const token =
    typeof valor.token === 'string' && valor.token.trim()
      ? valor.token
      : valor.token ?? null
  const nombre = texto(valor.nombre)

  if (rol === 'admin') {
    return {
      rol: 'admin',
      estudianteId: null,
      nombre: nombre || 'Administrador',
      token,
    }
  }

  const id = Number(valor.estudianteId ?? valor.estudiante_id)
  if (!Number.isSafeInteger(id) || id <= 0) return null

  return {
    rol: 'estudiante',
    estudianteId: id,
    nombre: nombre || `Estudiante #${String(id)}`,
    token,
  }
}

function leerSesion() {
  if (typeof window === 'undefined') return null

  try {
    const guardada = window.localStorage?.getItem(CLAVE_SESION)
    if (!guardada) return null
    return normalizarSesion(JSON.parse(guardada))
  } catch {
    // localStorage puede fallar en modo privado o con un valor corrupto.
    return null
  }
}

export default function App() {
  const [sesion, setSesion] = useState(leerSesion)

  useEffect(() => {
    try {
      if (sesion) {
        window.localStorage.setItem(CLAVE_SESION, JSON.stringify(sesion))
      } else {
        window.localStorage.removeItem(CLAVE_SESION)
      }
    } catch {
      // Si el navegador bloquea el almacenamiento, la sesión sigue en memoria.
    }
  }, [sesion])

  function manejarLogin(datos) {
    const normalizada = normalizarSesion(datos)
    if (normalizada) setSesion(normalizada)
  }

  function cerrarSesion() {
    setSesion(null)
    try {
      window.localStorage.removeItem(CLAVE_SESION)
    } catch {
      // Sin acceso al almacenamiento; el estado en memoria ya está limpio.
    }
  }

  const esAdmin = sesion?.rol === 'admin'
  const nombreVisible = sesion
    ? sesion.nombre ||
      (esAdmin ? 'Administrador' : `Estudiante #${String(sesion.estudianteId)}`)
    : ''

  return (
    <div className="min-h-screen bg-gray-100">
      <header className="border-b border-indigo-800/40 bg-indigo-700 text-white shadow">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-4 py-4">
          <div className="flex items-center gap-3">
            <span
              aria-hidden="true"
              className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/15 text-lg font-bold shadow-inner"
            >
              🎓
            </span>
            <div>
              <h1 className="text-xl font-bold leading-tight">
                Inscripción de Materias
              </h1>
              <p className="text-xs text-indigo-200">
                Ruta académica · Oferta real · Horario automático
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {sesion ? (
              <>
                <div className="flex min-w-0 items-center gap-2.5 rounded-full bg-indigo-800/60 py-1.5 pl-1.5 pr-3">
                  <span
                    aria-hidden="true"
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-white text-sm"
                  >
                    {esAdmin ? '🛡️' : '🎓'}
                  </span>
                  <span className="min-w-0 leading-tight">
                    <span
                      className="block max-w-[130px] truncate text-sm font-semibold sm:max-w-[220px]"
                      title={nombreVisible}
                    >
                      {nombreVisible}
                    </span>
                    <span className="block text-[11px] text-indigo-200">
                      {esAdmin
                        ? 'Administrador'
                        : `Estudiante · ID ${String(sesion.estudianteId)}`}
                    </span>
                  </span>
                </div>

                <button
                  type="button"
                  onClick={cerrarSesion}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-300/60 bg-white/10 px-3.5 py-2 text-sm font-semibold text-white transition-colors hover:bg-white/20 focus:outline-none focus-visible:ring-2 focus-visible:ring-white/70"
                >
                  <span aria-hidden="true">⎋</span>
                  Salir
                </button>
              </>
            ) : (
              <span className="rounded-full bg-indigo-800/60 px-3 py-1.5 text-xs font-medium text-indigo-100">
                Acceso
              </span>
            )}
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6">
        {!sesion ? (
          <Login onLogin={manejarLogin} />
        ) : esAdmin ? (
          <AdminPanel onLogout={cerrarSesion} />
        ) : (
          <EstudiantePanel
            estudianteId={sesion.estudianteId}
            nombre={sesion.nombre}
            onLogout={cerrarSesion}
          />
        )}
      </main>

      <footer className="mx-auto max-w-7xl px-4 pb-8 text-center text-xs text-gray-400">
        API: <span className="font-mono">{API_BASE || 'mismo origen (proxy /api)'}</span>
      </footer>
    </div>
  )
}
