// static/js/pages/admin.js
// JS de Administracion (responsables + sincronizacion Firebase) --
// extraido de templates/admin.html. Las URLs que Flask genera con
// url_for() (crear_usuario, admin, sincronizar_firebase,
// sincronizar_firebase_estado) no pueden vivir en un .js estatico, asi
// que quedaron como constantes en un bloque inline chico en el template,
// justo antes de cargar este archivo -- las llamadas de aca abajo que
// antes tenian la URL escrita literal ahora usan esas constantes.

const NETWATCH_USUARIOS = JSON.parse(document.getElementById('usuarios-data').textContent);
const NETWATCH_EQUIPOS_BASICO = JSON.parse(document.getElementById('equipos-basico-data').textContent);
// NETWATCH_URL_CREAR_USUARIO se movio al bloque de constantes del template.
const NETWATCH_PERFIL_ABIERTO = JSON.parse(document.getElementById('perfil-abierto-data').textContent);

function netwatchWhatsappLink(telefono) {
  if (!telefono) return null;
  let digitos = telefono.replace(/\D/g, '');
  if (!digitos) return null;
  if (digitos.startsWith('56') && (digitos.length === 10 || digitos.length === 11)) {
    // ya viene con codigo de pais
  } else if (digitos.length === 9 && digitos.startsWith('9')) {
    digitos = '56' + digitos;
  } else if (digitos.length === 8) {
    digitos = '569' + digitos;
  }
  // whatsapp://send abre la app directo, sin pasar por la pagina de wa.me.
  return 'whatsapp://send?phone=' + digitos;
}

function netwatchFotoSeleccionada(input, sufijo) {
  const nombreSpan = document.getElementById('foto-archivo-nombre-' + sufijo);
  if (input.files && input.files[0]) {
    nombreSpan.textContent = input.files[0].name;
    const reader = new FileReader();
    reader.onload = function (e) { netwatchMostrarFotoPreview(e.target.result, sufijo); };
    reader.readAsDataURL(input.files[0]);
    document.getElementById('foto-url-input-' + sufijo).value = '';
  } else {
    nombreSpan.textContent = 'Ningun archivo seleccionado';
  }
}

function netwatchFotoUrlCambiada(input, sufijo) {
  const valor = input.value.trim();
  if (valor) {
    netwatchMostrarFotoPreview(valor, sufijo);
    document.getElementById('foto-archivo-input-' + sufijo).value = '';
    document.getElementById('foto-archivo-nombre-' + sufijo).textContent = 'Ningun archivo seleccionado';
  }
}

function netwatchMostrarFotoPreview(src, sufijo) {
  const img = document.getElementById('foto-preview-' + sufijo);
  const placeholder = document.getElementById('foto-placeholder-' + sufijo);
  img.src = src;
  img.style.display = 'block';
  placeholder.style.display = 'none';
}

function netwatchResetFotoPreview() {
  const img = document.getElementById('foto-preview-nuevo');
  const placeholder = document.getElementById('foto-placeholder-nuevo');
  img.src = '';
  img.style.display = 'none';
  placeholder.style.display = 'block';
  document.getElementById('foto-archivo-input-nuevo').value = '';
  document.getElementById('foto-url-input-nuevo').value = '';
  document.getElementById('foto-archivo-nombre-nuevo').textContent = 'Ningun archivo seleccionado';
}

// El modal ahora es SOLO para agregar un empleado nuevo -- editar uno
// existente se hace con el panel acordeon inline "Editar" de la tabla (no
// se sale de Gestion de Empleados, y se puede seguir con el siguiente sin
// perder el lugar).
// Ordena equipos por IP numerica (octeto a octeto) en vez de orden de
// texto -- si no, "172.30.100.20" queda despues de "172.30.100.150" porque
// como string "2" > "1". Devuelve una copia nueva, no toca el array original.
function netwatchOrdenarEquiposPorIp(lista) {
  function partesIp(ip) {
    return (ip || '').split('.').map(function (p) { return parseInt(p, 10) || 0; });
  }
  return lista.slice().sort(function (a, b) {
    const pa = partesIp(a.ip);
    const pb = partesIp(b.ip);
    for (let i = 0; i < 4; i++) {
      if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) - (pb[i] || 0);
    }
    return 0;
  });
}

function netwatchAbrirModalEmpleado() {
  const form = document.getElementById('form-empleado');
  form.reset();
  netwatchResetFotoPreview();
  document.getElementById('modal-empleado-titulo').textContent = 'Agregar Nuevo Empleado';
  form.action = NETWATCH_URL_CREAR_USUARIO;
  document.getElementById('f-activo').checked = true;

  // Selector de equipo a asignar -- solo equipos sin responsable actual,
  // ordenados por IP para que sea facil ubicar el que buscas.
  const selectEquipo = document.getElementById('f-equipo');
  selectEquipo.innerHTML = '<option value="">-- Sin asignar (puedes vincularlo despues) --</option>';
  netwatchOrdenarEquiposPorIp(NETWATCH_EQUIPOS_BASICO).forEach(function (eq) {
    if (eq.responsable_id) return;
    const opt = document.createElement('option');
    opt.value = eq.id;
    opt.textContent = eq.ip + (eq.hostname ? ' - ' + eq.hostname : '');
    selectEquipo.appendChild(opt);
  });

  document.getElementById('modal-empleado').classList.add('modal-open');
}

function netwatchCerrarModalEmpleado() {
  document.getElementById('modal-empleado').classList.remove('modal-open');
}

function netwatchAbrirPerfil(id) {
  const u = NETWATCH_USUARIOS.find(function (x) { return x.id === id; });
  if (!u) return;

  document.getElementById('perfil-nombre').textContent = u.nombre;
  const correoEl = document.getElementById('perfil-correo');
  correoEl.textContent = u.correo || '';
  correoEl.href = u.correo ? ('mailto:' + u.correo) : '#';

  const telefonoEl = document.getElementById('perfil-telefono');
  telefonoEl.textContent = u.telefono || '-';
  const waLink = netwatchWhatsappLink(u.telefono);
  telefonoEl.href = waLink || '#';
  document.getElementById('perfil-cargo').textContent = u.cargo || '-';
  document.getElementById('perfil-lugar').textContent = u.lugar_trabajo || '-';
  document.getElementById('perfil-departamento').textContent = u.departamento || '-';
  document.getElementById('perfil-ciudad').textContent = u.ciudad || '-';
  document.getElementById('perfil-sistemas').textContent = u.sistemas_autorizados || '-';

  const estadoBadge = document.getElementById('perfil-estado-badge');
  estadoBadge.textContent = u.activo ? 'Activo' : 'Inactivo';
  estadoBadge.className = 'tag perfil-estado-badge ' + (u.activo ? 'tag-confirmado' : 'tag-descartado');

  const vpnBadge = document.getElementById('perfil-vpn-badge');
  if (u.tipo_vpn) {
    vpnBadge.textContent = (u.vpn_activa ? 'VPN Conectada' : 'VPN Desconectada') + ' (' + u.tipo_vpn + ')';
    vpnBadge.className = 'badge ' + (u.vpn_activa ? 'badge-confiable' : 'badge-sin_respuesta');
  } else {
    vpnBadge.textContent = 'Sin VPN asignada';
    vpnBadge.className = 'badge badge-sin_respuesta';
  }

  const fotoImg = document.getElementById('perfil-foto');
  const fotoPlaceholder = document.getElementById('perfil-foto-placeholder');
  if (u.foto_perfil) {
    fotoImg.src = u.foto_perfil.startsWith('http') ? u.foto_perfil : ('/static/' + u.foto_perfil);
    fotoImg.style.display = 'block';
    fotoPlaceholder.style.display = 'none';
  } else {
    fotoImg.style.display = 'none';
    fotoPlaceholder.style.display = 'block';
  }

  const equipos = u.equipos_asignados || [];
  document.getElementById('perfil-equipos-titulo').textContent = 'Equipos asignados (' + equipos.length + ')';
  const tbody = document.getElementById('perfil-equipos-tbody');
  const tabla = document.getElementById('perfil-equipos-tabla');
  const vacio = document.getElementById('perfil-equipos-vacio');
  tbody.innerHTML = '';
  if (equipos.length === 0) {
    tabla.style.display = 'none';
    vacio.style.display = 'block';
  } else {
    tabla.style.display = '';
    vacio.style.display = 'none';
    equipos.forEach(function (eq) {
      const tr = document.createElement('tr');

      const tdEquipo = document.createElement('td');
      const a = document.createElement('a');
      a.href = '/equipo/' + eq.id;
      a.className = 'ip-link';
      a.textContent = eq.hostname || eq.ip;
      tdEquipo.appendChild(a);

      const tdIp = document.createElement('td');
      tdIp.textContent = eq.ip;

      const tdEstado = document.createElement('td');
      const tag = document.createElement('span');
      tag.className = 'tag ' + (eq.en_linea ? 'tag-confirmado' : 'tag-descartado');
      tag.textContent = eq.en_linea ? 'ONLINE' : 'OFFLINE';
      tdEstado.appendChild(tag);

      const tdAccion = document.createElement('td');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn btn-discard';
      btn.textContent = 'Desvincular';
      btn.onclick = function () {
        if (!confirm('Desvincular ' + (eq.hostname || eq.ip) + ' de ' + u.nombre + '?')) return;
        const f = document.createElement('form');
        f.method = 'POST';
        f.action = '/admin/usuarios/' + id + '/equipos/' + eq.id + '/desvincular';
        document.body.appendChild(f);
        f.submit();
      };
      tdAccion.appendChild(btn);

      tr.appendChild(tdEquipo);
      tr.appendChild(tdIp);
      tr.appendChild(tdEstado);
      tr.appendChild(tdAccion);
      tbody.appendChild(tr);
    });
  }

  document.getElementById('form-vincular-equipo').action = '/admin/usuarios/' + id + '/equipos/vincular';
  const select = document.getElementById('perfil-select-equipo');
  select.innerHTML = '';
  const nombrePorId = {};
  NETWATCH_USUARIOS.forEach(function (x) { nombrePorId[x.id] = x.nombre; });
  netwatchOrdenarEquiposPorIp(NETWATCH_EQUIPOS_BASICO).forEach(function (eq) {
    if (eq.responsable_id === id) return;
    const opt = document.createElement('option');
    opt.value = eq.id;
    let label = eq.ip + (eq.hostname ? ' - ' + eq.hostname : '');
    if (eq.responsable_id && nombrePorId[eq.responsable_id]) {
      label += ' (actualmente: ' + nombrePorId[eq.responsable_id] + ')';
    }
    opt.textContent = label;
    select.appendChild(opt);
  });

  document.getElementById('perfil-editar-btn').onclick = function () {
    // El modal de "Agregar Nuevo Empleado" no sirve para editar (siempre
    // arranca en blanco) -- el edicion real es el panel acordeon inline de
    // la fila en la tabla (ver netwatchToggleAcordeon), ya precargado con
    // los datos reales de este empleado via Jinja.
    netwatchCerrarPerfil();
    netwatchToggleAcordeon('empleado-' + id);
  };

  document.getElementById('modal-perfil').classList.add('modal-open');
}

function netwatchCerrarPerfil() {
  document.getElementById('modal-perfil').classList.remove('modal-open');
}

document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') {
    netwatchCerrarPerfil();
    netwatchCerrarModalEmpleado();
  }
});

if (NETWATCH_PERFIL_ABIERTO) {
  netwatchAbrirPerfil(NETWATCH_PERFIL_ABIERTO);
}

// --- buscador de empleados ---------------------------------------------
const netwatchBuscadorEmpleados = document.getElementById('buscador-empleados');
if (netwatchBuscadorEmpleados) {
  netwatchBuscadorEmpleados.addEventListener('input', function (ev) {
    const q = ev.target.value.trim().toLowerCase();
    // :not(.acordeon-panel) para no tocar las filas del panel "Editar" inline.
    const filas = document.querySelectorAll('#tabla-empleados tbody tr:not(.acordeon-panel)');
    let visibles = 0;
    filas.forEach(function (tr) {
      const coincide = !q || tr.dataset.buscar.indexOf(q) !== -1;
      tr.style.display = coincide ? '' : 'none';
      if (coincide) visibles++;
    });
    const vacio = document.getElementById('empleados-vacio');
    if (vacio) vacio.style.display = visibles === 0 ? 'block' : 'none';
    netwatchActualizarSeleccion();
  });
}

// --- sincronizar con la nube (Firebase), con barra de progreso ----------
const NETWATCH_SYNC_FASES = {
  conectando: 'Conectando con Firebase...',
  leyendo_firebase: 'Leyendo datos de la nube...',
  sincronizando: 'Sincronizando...',
};

function netwatchSincronizarNube() {
  const btn = document.getElementById('btn-sincronizar-nube');
  const barra = document.getElementById('barra-sync-progreso');
  const texto = document.getElementById('sync-progreso-texto');
  const fill = document.getElementById('sync-progreso-fill');
  if (btn) btn.disabled = true;
  barra.style.display = 'block';
  fill.style.width = '0%';
  texto.textContent = 'Conectando con Firebase...';

  fetch(NETWATCH_URL_SINCRONIZAR_FIREBASE, { method: 'POST' })
    .then(function (r) { return r.json(); })
    .then(function (r) {
      if (!r.iniciado) {
        texto.textContent = 'Ya hay una sincronizacion en curso, esperando a que termine...';
      }
      netwatchPollSync();
    })
    .catch(function () {
      texto.textContent = 'No se pudo conectar con el servidor local. Revisa que NetWatch siga corriendo.';
      if (btn) btn.disabled = false;
    });
}

function netwatchPollSync() {
  fetch(NETWATCH_URL_SINCRONIZAR_FIREBASE_ESTADO)
    .then(function (r) { return r.json(); })
    .then(function (estado) {
      const texto = document.getElementById('sync-progreso-texto');
      const fill = document.getElementById('sync-progreso-fill');
      if (estado.total > 0) {
        const pct = Math.min(100, Math.round((estado.procesados / estado.total) * 100));
        fill.style.width = pct + '%';
        texto.textContent = 'Sincronizando... ' + estado.procesados + ' / ' + estado.total;
      } else {
        texto.textContent = NETWATCH_SYNC_FASES[estado.fase] || 'Preparando...';
      }
      if (estado.corriendo) {
        setTimeout(netwatchPollSync, 500);
        return;
      }
      const btn = document.getElementById('btn-sincronizar-nube');
      if (btn) btn.disabled = false;
      if (estado.error) {
        window.location.href = NETWATCH_URL_ADMIN + '?error=firebase_' + encodeURIComponent(estado.error);
        return;
      }
      if (estado.resumen) {
        const r = estado.resumen;
        window.location.href = NETWATCH_URL_ADMIN + '?sincronizado=1'
          + '&fu_bajados=' + r.usuarios.bajados_nuevos + '&fu_actualizados=' + r.usuarios.actualizados
          + '&fu_subidos=' + r.usuarios.subidos
          + '&fe_bajados=' + r.equipos.bajados_nuevos + '&fe_actualizados=' + r.equipos.actualizados
          + '&fe_subidos=' + r.equipos.subidos;
      }
    })
    .catch(function () {
      const texto = document.getElementById('sync-progreso-texto');
      texto.textContent = 'Se perdio la conexion con el servidor local mientras sincronizaba.';
      const btn = document.getElementById('btn-sincronizar-nube');
      if (btn) btn.disabled = false;
    });
}

// --- seleccion multiple + eliminar en bloque --------------------------
function netwatchActualizarSeleccion() {
  const marcados = document.querySelectorAll('.usuario-check:checked');
  const barra = document.getElementById('barra-seleccion');
  const contador = document.getElementById('contador-seleccion');
  if (!barra || !contador) return;
  if (marcados.length > 0) {
    barra.style.display = 'flex';
    contador.textContent = marcados.length + ' seleccionado' + (marcados.length > 1 ? 's' : '');
  } else {
    barra.style.display = 'none';
  }
  const checkTodos = document.getElementById('check-todos');
  const visibles = Array.prototype.filter.call(
    document.querySelectorAll('.usuario-check'),
    function (cb) { const fila = cb.closest('tr'); return fila && fila.style.display !== 'none'; }
  );
  if (checkTodos) {
    checkTodos.checked = visibles.length > 0 && visibles.every(function (cb) { return cb.checked; });
  }
}

function netwatchToggleTodos(origen) {
  document.querySelectorAll('.usuario-check').forEach(function (cb) {
    const fila = cb.closest('tr');
    if (fila && fila.style.display !== 'none') cb.checked = origen.checked;
  });
  netwatchActualizarSeleccion();
}

// --- panel "Editar" inline (acordeon), igual que en Inventario de Equipos --
function netwatchToggleAcordeon(key) {
  const panel = document.getElementById('panel-' + key);
  if (!panel) return;
  const abriendo = panel.style.display === 'none';
  document.querySelectorAll('.acordeon-panel').forEach(function (p) {
    p.style.display = 'none';
  });
  panel.style.display = abriendo ? 'table-row' : 'none';
  if (abriendo) {
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

// Nombres de equipos/dispositivos que quedaron cargados como si fueran
// empleados por una importacion vieja (ej. "Impresora Canon", "Switch
// Juniper", "Servidor Sirio"). Marca el checkbox de los que calcen para que
// Andres los revise de un vistazo antes de eliminar -- no borra nada solo.
const NETWATCH_PATRON_SOSPECHOSO = /\b(impresora|switch|router|fortinet|servidor|server|radio|c[aá]mara|dvr|nvr|milenium|canopus|sirio|\bilo\b|pc virtual|sip|nortemil|ciclon|libre|garita|antena|modem|m[oó]dem|firewall|access ?point|^ap\s|administrador local)\b/i;

function netwatchMarcarSospechosos() {
  let marcados = 0;
  NETWATCH_USUARIOS.forEach(function (u) {
    if (NETWATCH_PATRON_SOSPECHOSO.test(u.nombre)) {
      const cb = document.querySelector('.usuario-check[value="' + u.id + '"]');
      if (cb) { cb.checked = true; marcados++; }
    }
  });
  netwatchActualizarSeleccion();
  if (marcados === 0) {
    alert('No encontre nombres que parezcan equipos/dispositivos. Revisa la lista a mano si crees que falta alguno.');
  }
}
