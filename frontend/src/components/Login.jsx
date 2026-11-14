import { useId, useState } from 'react'
import Banner from './Banner.jsx'
import Spinner from './Spinner.jsx'
import { apiPost, esErrorDeConexion } from '../api.js'

// ---------------------------------------------------------------------------
// Inicio de sesión.
//
// Dos formas de entrar, en pestañas:
//   * Estudiante: solo el ID numérico -> POST /api/auth/login
//   * Administrador: usuario + contraseña -> POST /api/auth/login
//
// Al terminar, se entrega la sesión al contenedor con onLogin({rol, ...}).
// La pantalla no navega ni recarga: App.jsx decide qué panel mostrar.
// ---------------------------------------------------------------------------

const PESTANAS = [
  { id: 'estudiante', etiqueta: 'Estudiante', icono: '🎓' },
  { id: 'admin', etiqueta: 'Administrador', icono: '🛡️' },
]

function texto(valor) {
  if (valor == null) return ''
  return String(valor).trim()
}

function mensajeRespuesta(datos) {
  if (!datos || typeof datos !== 'object') return ''

  const bruto = datos.detail ?? datos.mensaje ?? datos.message ?? datos.error

  if (typeof bruto === 'string') return bruto.trim()

  if (Array.isArray(bruto)) {
    return bruto
      .map((item) => (typeof item === 'string' ? item : item && item.msg))
      .filter(Boolean)
      .join('. ')
  }

  return ''
}

/** Traduce los errores de la API a mensajes pensados para la persona. */
function mensajeAmigable(error, rol) {
  if (!error) {
    return 'No se pudo iniciar sesión. Inténtalo de nuevo en unos segundos.'
  }

  if (esErrorDeConexion(error)) {
    return (
      texto(error.message) ||
      'No se pudo conectar con el servidor. Revisa tu conexión e inténtalo de nuevo.'
    )
  }

  const estado = Number(error.status) || 0

  if (rol === 'estudiante') {
    if (estado === 401 || estado === 404) {
      return 'No encontramos un estudiante con ese ID. Verifica el número e inténtalo de nuevo.'
    }
    if (estado === 400 || estado === 422) {
      return (
        texto(error.message) ||
        'El ID ingresado no es válido. Revisa el número e inténtalo de nuevo.'
      )
    }
  } else {
    if (estado === 401 || estado === 403) {
      return 'Usuario o contraseña incorrectos. Revisa tus credenciales e inténtalo de nuevo.'
    }
    if (estado === 404) {
      return 'El servicio de inicio de sesión no está disponible. Verifica que el backend esté actualizado.'
    }
    if (estado === 400 || estado === 422) {
      return (
        texto(error.message) ||
        'Revisa el usuario y la contraseña: hay datos incompletos o no válidos.'
      )
    }
  }

  return (
    texto(error.message) ||
    'No se pudo iniciar sesión. Inténtalo de nuevo en unos segundos.'
  )
}

export default function Login({ onLogin } = {}) {
  const idBase = useId()
  const [rol, setRol] = useState('estudiante')
  const [identificacion, setIdentificacion] = useState('')
  const [usuario, setUsuario] = useState('')
  const [password, setPassword] = useState('')
  const [verPassword, setVerPassword] = useState(false)
  const [cargando, setCargando] = useState(false)
  const [error, setError] = useState(null)

  function cambiarPestana(nuevoRol) {
    if (cargando || nuevoRol === rol) return
    setRol(nuevoRol)
    setError(null)
  }

  function validar() {
    if (rol === 'estudiante') {
      if (!identificacion) {
        return 'Escribe tu ID de estudiante para continuar.'
      }
      if (!/^\d+$/.test(identificacion)) {
        return 'El ID del estudiante debe contener solo números.'
      }
      const numero = Number(identificacion)
      if (!Number.isSafeInteger(numero) || numero <= 0) {
        return 'El ID del estudiante debe ser un número entero mayor que 0.'
      }
      return ''
    }

    if (!usuario.trim()) {
      return 'Escribe tu usuario de administrador.'
    }
    if (!password) {
      return 'Escribe tu contraseña.'
    }
    return ''
  }

  async function enviar(evento) {
    evento.preventDefault()
    if (cargando) return

    const faltante = validar()
    if (faltante) {
      setError(faltante)
      return
    }

    setCargando(true)
    setError(null)

    try {
      const cuerpo =
        rol === 'admin'
          ? { rol: 'admin', usuario: usuario.trim(), password }
          : { rol: 'estudiante', estudiante_id: Number(identificacion) }

      const datos = await apiPost('/api/auth/login', cuerpo)

      if (!datos || typeof datos !== 'object' || Array.isArray(datos)) {
        throw new Error(
          'El servidor devolvió una respuesta inesperada. Inténtalo de nuevo.',
        )
      }

      if (datos.ok === false) {
        throw new Error(
          mensajeRespuesta(datos) ||
            'El servidor rechazó el inicio de sesión. Revisa tus datos.',
        )
      }

      const rolFinal =
        datos.rol === 'admin' || datos.rol === 'estudiante' ? datos.rol : rol

      if (rolFinal === 'estudiante') {
        const idFinal = Number(datos.estudiante_id ?? identificacion)

        if (!Number.isSafeInteger(idFinal) || idFinal <= 0) {
          throw new Error(
            'El servidor no devolvió un ID de estudiante válido. Inténtalo de nuevo.',
          )
        }

        onLogin?.({
          rol: 'estudiante',
          estudianteId: idFinal,
          nombre:
            texto(datos.nombre) || `Estudiante #${String(idFinal)}`,
          token: datos.token ?? null,
        })
      } else {
        onLogin?.({
          rol: 'admin',
          estudianteId: null,
          nombre:
            texto(datos.usuario) || usuario.trim() || 'Administrador',
          token: datos.token ?? null,
        })
      }
    } catch (err) {
      setError(mensajeAmigable(err, rol))
    } finally {
      setCargando(false)
    }
  }

  return (
    <div className="mx-auto max-w-4xl py-4 sm:py-8">
      <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-xl">
        <div className="grid lg:grid-cols-[0.95fr_1.05fr]">
          <aside className="relative hidden flex-col justify-between bg-gradient-to-br from-indigo-700 via-indigo-600 to-violet-700 p-8 text-white lg:flex">
            <div>
              <span className="inline-flex items-center gap-2 rounded-full bg-white/15 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em]">
                Inscripción de materias
              </span>
              <h2 className="mt-6 text-3xl font-bold leading-tight">
                Tu ruta académica, resuelta.
              </h2>
              <p className="mt-3 max-w-sm text-sm leading-relaxed text-indigo-100">
                Consulta la oferta real, valida tus prerrequisitos y genera un
                horario sin cruces con los NRC disponibles.
              </p>
            </div>

            <ul className="mt-10 space-y-3 text-sm text-indigo-50">
              {[
                'Perfil académico con tu historial de materias',
                'Oferta actualizada con cupos y saldos',
                'Horario automático sin choques de horario',
              ].map((item) => (
                <li key={item} className="flex items-start gap-2.5">
                  <span
                    aria-hidden="true"
                    className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-white/15 text-[11px] font-bold"
                  >
                    ✓
                  </span>
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </aside>

          <div className="p-6 sm:p-8">
            <h1 className="text-2xl font-bold text-gray-900">Iniciar sesión</h1>
            <p className="mt-1 text-sm text-gray-500">
              Elige cómo quieres entrar. La sesión se conserva en este navegador.
            </p>

            <div
              role="tablist"
              aria-label="Tipo de usuario"
              className="mt-6 flex rounded-xl bg-gray-100 p-1"
            >
              {PESTANAS.map((pestana) => {
                const activa = rol === pestana.id

                return (
                  <button
                    key={pestana.id}
                    type="button"
                    role="tab"
                    id={`${idBase}-tab-${pestana.id}`}
                    aria-selected={activa}
                    aria-controls={`${idBase}-panel`}
                    onClick={() => cambiarPestana(pestana.id)}
                    className={`flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2.5 text-sm font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300 ${
                      activa
                        ? 'bg-white text-indigo-700 shadow-sm'
                        : 'text-gray-500 hover:bg-white/60 hover:text-gray-700'
                    }`}
                  >
                    <span aria-hidden="true">{pestana.icono}</span>
                    {pestana.etiqueta}
                  </button>
                )
              })}
            </div>

            <form onSubmit={enviar} noValidate className="mt-6 space-y-4">
              <div
                role="tabpanel"
                id={`${idBase}-panel`}
                aria-labelledby={`${idBase}-tab-${rol}`}
                className="space-y-4"
              >
                {rol === 'estudiante' ? (
                  <>
                    <label htmlFor={`${idBase}-id`} className="block">
                      <span className="text-sm font-medium text-gray-700">
                        ID del estudiante
                      </span>
                      <div className="relative mt-1">
                        <span
                          aria-hidden="true"
                          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm font-semibold text-gray-400"
                        >
                          #
                        </span>
                        <input
                          id={`${idBase}-id`}
                          name="estudiante_id"
                          type="text"
                          inputMode="numeric"
                          autoComplete="off"
                          autoFocus
                          maxLength={12}
                          value={identificacion}
                          onChange={(evento) => {
                            setIdentificacion(
                              evento.target.value.replace(/\D/g, ''),
                            )
                            if (error) setError(null)
                          }}
                          disabled={cargando}
                          aria-describedby={`${idBase}-id-ayuda`}
                          className="w-full rounded-xl border border-gray-300 bg-white py-2.5 pl-8 pr-3 text-sm text-gray-800 shadow-sm transition focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200 disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-400"
                          placeholder="Ej.: 1"
                        />
                      </div>
                    </label>
                    <p
                      id={`${idBase}-id-ayuda`}
                      className="text-xs text-gray-400"
                    >
                      Ingresa el ID que te asignó la universidad. No crees uno
                      nuevo: el sistema usará exactamente ese número.
                    </p>
                  </>
                ) : (
                  <>
                    <label htmlFor={`${idBase}-usuario`} className="block">
                      <span className="text-sm font-medium text-gray-700">
                        Usuario
                      </span>
                      <input
                        id={`${idBase}-usuario`}
                        name="usuario"
                        type="text"
                        autoComplete="username"
                        autoFocus
                        value={usuario}
                        onChange={(evento) => {
                          setUsuario(evento.target.value)
                          if (error) setError(null)
                        }}
                        disabled={cargando}
                        className="mt-1 w-full rounded-xl border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-800 shadow-sm transition focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200 disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-400"
                        placeholder="Usuario de administrador"
                      />
                    </label>

                    <label htmlFor={`${idBase}-password`} className="block">
                      <span className="text-sm font-medium text-gray-700">
                        Contraseña
                      </span>
                      <div className="relative mt-1">
                        <input
                          id={`${idBase}-password`}
                          name="password"
                          type={verPassword ? 'text' : 'password'}
                          autoComplete="current-password"
                          value={password}
                          onChange={(evento) => {
                            setPassword(evento.target.value)
                            if (error) setError(null)
                          }}
                          disabled={cargando}
                          className="w-full rounded-xl border border-gray-300 bg-white py-2.5 pl-3 pr-20 text-sm text-gray-800 shadow-sm transition focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200 disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-400"
                          placeholder="Tu contraseña"
                        />
                        <button
                          type="button"
                          onClick={() => setVerPassword((valor) => !valor)}
                          disabled={cargando}
                          aria-label={
                            verPassword
                              ? 'Ocultar la contraseña'
                              : 'Mostrar la contraseña'
                          }
                          className="absolute right-2 top-1/2 -translate-y-1/2 rounded-lg px-2.5 py-1 text-xs font-semibold text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 disabled:opacity-50"
                        >
                          {verPassword ? 'Ocultar' : 'Ver'}
                        </button>
                      </div>
                    </label>
                  </>
                )}
              </div>

              {error && (
                <Banner
                  tipo="error"
                  titulo="No se pudo iniciar sesión"
                  onCerrar={() => setError(null)}
                >
                  {error}
                </Banner>
              )}

              <button
                type="submit"
                disabled={cargando}
                aria-busy={cargando}
                className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 px-5 py-3 text-base font-semibold text-white shadow-lg shadow-indigo-200/70 transition-colors hover:bg-indigo-500 focus:outline-none focus-visible:ring-4 focus-visible:ring-indigo-200 active:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-indigo-300 disabled:shadow-none"
              >
                {cargando ? (
                  <>
                    <Spinner label={null} className="h-4 w-4" />
                    Ingresando…
                  </>
                ) : (
                  <>
                    {rol === 'admin'
                      ? 'Entrar como administrador'
                      : 'Entrar como estudiante'}
                    <span aria-hidden="true" className="text-lg leading-none">
                      →
                    </span>
                  </>
                )}
              </button>
            </form>

            <p className="mt-5 border-t border-gray-100 pt-4 text-xs leading-relaxed text-gray-400">
              Tu sesión queda guardada en este navegador. Si usas un equipo
              compartido, usa «Salir» al terminar.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
