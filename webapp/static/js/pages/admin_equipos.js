// static/js/pages/admin_equipos.js
// JS de Administracion > Inventario de Equipos -- extraido de
// templates/admin_equipos.html (eran 2 <script> separados, se unificaron
// en este archivo porque ya compartian el mismo scope global en la pagina).
// Las URLs que Flask genera con url_for() quedaron en un bloque chico e
// inline en el template (no pueden vivir aca: este archivo lo sirve
// Flask como estatico, sin pasar por Jinja).

const NETWATCH_USUARIOS_PERFIL = JSON.parse(document.getElementById('usuarios-perfil-data').textContent);
// (movida al bloque de constantes en el template, ver arriba del <script src=admin_equipos.js>)

function netwatchWhatsappLinkResponsable(telefono) {
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
  return 'whatsapp://send?phone=' + digitos;
}

function netwatchAbrirPerfilResponsable(id) {
  const u = NETWATCH_USUARIOS_PERFIL.find(function (x) { return x.id === id; });
  if (!u) return;

  document.getElementById('perfilr-nombre').textContent = u.nombre;
  const correoEl = document.getElementById('perfilr-correo');
  correoEl.textContent = u.correo || '';
  correoEl.href = u.correo ? ('mailto:' + u.correo) : '#';

  const telefonoEl = document.getElementById('perfilr-telefono');
  telefonoEl.textContent = u.telefono || '-';
  const waLink = netwatchWhatsappLinkResponsable(u.telefono);
  telefonoEl.href = waLink || '#';
  document.getElementById('perfilr-cargo').textContent = u.cargo || '-';
  document.getElementById('perfilr-lugar').textContent = u.lugar_trabajo || '-';
  document.getElementById('perfilr-departamento').textContent = u.departamento || '-';
  document.getElementById('perfilr-ciudad').textContent = u.ciudad || '-';
  document.getElementById('perfilr-sistemas').textContent = u.sistemas_autorizados || '-';

  const estadoBadge = document.getElementById('perfilr-estado-badge');
  estadoBadge.textContent = u.activo ? 'Activo' : 'Inactivo';
  estadoBadge.className = 'tag perfil-estado-badge ' + (u.activo ? 'tag-confirmado' : 'tag-descartado');

  const vpnBadge = document.getElementById('perfilr-vpn-badge');
  if (u.tipo_vpn) {
    vpnBadge.textContent = (u.vpn_activa ? 'VPN Conectada' : 'VPN Desconectada') + ' (' + u.tipo_vpn + ')';
    vpnBadge.className = 'badge ' + (u.vpn_activa ? 'badge-confiable' : 'badge-sin_respuesta');
  } else {
    vpnBadge.textContent = 'Sin VPN asignada';
    vpnBadge.className = 'badge badge-sin_respuesta';
  }

  const fotoImg = document.getElementById('perfilr-foto');
  const fotoPlaceholder = document.getElementById('perfilr-foto-placeholder');
  if (u.foto_perfil) {
    fotoImg.src = u.foto_perfil.startsWith('http') ? u.foto_perfil : ('/static/' + u.foto_perfil);
    fotoImg.style.display = 'block';
    fotoPlaceholder.style.display = 'none';
  } else {
    fotoImg.style.display = 'none';
    fotoPlaceholder.style.display = 'block';
  }

  const equipos = u.equipos_asignados || [];
  document.getElementById('perfilr-equipos-titulo').textContent = 'Equipos asignados (' + equipos.length + ')';
  const tbody = document.getElementById('perfilr-equipos-tbody');
  const tabla = document.getElementById('perfilr-equipos-tabla');
  const vacio = document.getElementById('perfilr-equipos-vacio');
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
      a.href = '/equipo/' + eq.id + '?origen=inventario';
      a.target = '_blank';
      a.rel = 'noopener';
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

      tr.appendChild(tdEquipo);
      tr.appendChild(tdIp);
      tr.appendChild(tdEstado);
      tbody.appendChild(tr);
    });
  }

  document.getElementById('perfilr-editar-link').href = NETWATCH_URL_ADMIN_PERFIL + '?perfil=' + id;

  document.getElementById('modal-perfil-responsable').classList.add('modal-open');
}

function netwatchCerrarPerfilResponsable() {
  document.getElementById('modal-perfil-responsable').classList.remove('modal-open');
}

document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') netwatchCerrarPerfilResponsable();
});

// --- segundo bloque original (agente de apagado remoto) ---

// Columna "Agente" -- de si ESTE equipo tiene puesto el agente de apagado/
// reinicio remoto (ver agente_apagado/netwatch_agente.ps1). El backend ya
// matcheo por IP el ultimo heartbeat en Firebase (o vacio si nunca ha
// reportado); aca solo se calcula "hace cuanto" y se pinta el badge.
function netwatchTiempoRelativoAgente(iso) {
  const entonces = new Date(iso);
  if (isNaN(entonces.getTime())) return null;
  const segundos = Math.max(0, Math.round((Date.now() - entonces.getTime()) / 1000));
  if (segundos < 60) return 'hace segundos';
  const minutos = Math.round(segundos / 60);
  if (minutos < 60) return 'hace ' + minutos + ' min';
  const horas = Math.round(minutos / 60);
  if (horas < 24) return 'hace ' + horas + (horas === 1 ? ' hora' : ' horas');
  const dias = Math.round(horas / 24);
  return 'hace ' + dias + (dias === 1 ? ' dia' : ' dias');
}

// Si no ha vuelto a avisar en mas de 10 minutos (el agente pasa cada 1 min
// via tarea programada) probablemente la PC esta apagada o el agente dejo
// de correr -- se pinta distinto al verde "activo ahora" sin decir
// directamente "no instalado" (si lo estuvo, solo que no ha avisado hace rato).
const NETWATCH_AGENTE_UMBRAL_RECIENTE_SEG = 10 * 60;

function netwatchPintarAgentes() {
  document.querySelectorAll('.agente-cell').forEach(function (td) {
    const visto = td.dataset.visto;
    if (!visto) {
      td.innerHTML = '<span class="tag tag-descartado"><span class="dot dot-off"></span> No instalado</span>';
      return;
    }
    const relativo = netwatchTiempoRelativoAgente(visto);
    const segundos = (Date.now() - new Date(visto).getTime()) / 1000;
    const reciente = segundos >= 0 && segundos < NETWATCH_AGENTE_UMBRAL_RECIENTE_SEG;
    td.innerHTML = '<span class="tag ' + (reciente ? 'tag-confirmado' : 'tag-pendiente') + '" title="Ultimo aviso: ' + visto + '">'
      + '<span class="dot ' + (reciente ? 'dot-on' : 'dot-off') + '"></span> Si' + (relativo ? ' (' + relativo + ')' : '') + '</span>';
  });
}
netwatchPintarAgentes();

function netwatchAplicarCategoriaEquipo(sel) {
  const red = (sel.dataset.categoriasRed || '').split(',').filter(Boolean);
  const esRed = red.indexOf(sel.value) !== -1;
  const form = sel.closest('.ficha-form');
  if (!form) return;
  form.querySelectorAll('.fieldset-solo-pc').forEach(function (fs) { fs.style.display = esRed ? 'none' : ''; });
}

function netwatchFotoEquipoSeleccionada(input, id) {
  if (!input.files || !input.files[0]) return;
  const reader = new FileReader();
  reader.onload = function (ev) {
    const img = document.getElementById('foto-equipo-img-' + id);
    const placeholder = document.getElementById('foto-equipo-placeholder-' + id);
    img.src = ev.target.result;
    img.style.display = 'block';
    if (placeholder) placeholder.style.display = 'none';
    document.getElementById('foto-equipo-eliminar-' + id).value = '0';
  };
  reader.readAsDataURL(input.files[0]);
}

function netwatchEliminarFotoEquipo(id) {
  const img = document.getElementById('foto-equipo-img-' + id);
  img.style.display = 'none';
  img.src = '';
  const placeholder = document.getElementById('foto-equipo-placeholder-' + id);
  if (placeholder) placeholder.style.display = 'flex';
  document.getElementById('foto-equipo-archivo-' + id).value = '';
  document.getElementById('foto-equipo-eliminar-' + id).value = '1';
}

let netwatchOrdenRamAsc = false; // primer click: de mayor a menor RAM

// Ordena las filas de la tabla por memoria RAM (parsea el primer numero que
// encuentre en el texto, ej. "16 GB" o "4 Giga" -> 16 / 4; equipos sin dato
// quedan siempre al final, sin importar la direccion). Cada fila de equipo
// va seguida de su panel "Editar" inline (acordeon-panel) -- hay que mover
// el par completo junto o el panel se queda huerfano en el lugar viejo.
function netwatchOrdenarPorRam() {
  const tabla = document.getElementById('tabla-equipos');
  if (!tabla) return;
  const tbody = tabla.querySelector('tbody');
  // OJO -- 2026-08-27: querySelectorAll('tr') busca en TODOS los
  // descendientes, no solo los hijos directos -- desde que el acordeon
  // "Editar" tiene adentro su propia tabla de Tickets de soporte (con su
  // propio <tbody><tr>...), esto tambien traia esas filas de tickets
  // mezcladas con las de equipos. Ademas de romper el orden, tbody.appendChild
  // las SACABA de la tabla de tickets y las tiraba sueltas en la tabla
  // principal. tbody.children solo trae los hijos directos (las filas de
  // equipo y sus paneles), nunca las anidadas dentro de un acordeon.
  const filas = Array.from(tbody.children).filter(function (tr) {
    return tr.tagName === 'TR' && !tr.classList.contains('acordeon-panel');
  });
  const pares = filas.map(function (fila) {
    const panel = (fila.nextElementSibling && fila.nextElementSibling.classList.contains('acordeon-panel'))
      ? fila.nextElementSibling : null;
    const texto = fila.dataset.ram || '';
    const match = texto.match(/([\d.,]+)/);
    const valor = match ? parseFloat(match[1].replace(',', '.')) : null;
    return { fila, panel, valor };
  });
  netwatchOrdenRamAsc = !netwatchOrdenRamAsc;
  pares.sort(function (a, b) {
    if (a.valor === null && b.valor === null) return 0;
    if (a.valor === null) return 1;  // sin dato siempre al final
    if (b.valor === null) return -1;
    return netwatchOrdenRamAsc ? a.valor - b.valor : b.valor - a.valor;
  });
  pares.forEach(function (p) {
    tbody.appendChild(p.fila);
    if (p.panel) tbody.appendChild(p.panel);
  });
  const th = document.getElementById('th-ram');
  if (th) th.innerHTML = 'RAM ' + (netwatchOrdenRamAsc ? '&#8593;' : '&#8595;');
}

let netwatchCategoriaActiva = '';
let netwatchMostrarDescartados = false;  // Por defecto no mostrar descartados

// Aplica a la vez el texto del buscador Y la pestana de categoria activa --
// ambos filtros conviven (ej. buscar "matta" dentro de solo los Servidores).
function netwatchAplicarFiltrosEquipos() {
  const q = document.getElementById('buscador-equipos').value.trim().toLowerCase();
  // OJO -- 2026-08-27: '#tabla-equipos tbody tr' es un selector descendiente,
  // asi que tambien encontraba las filas de la tabla de Tickets de soporte
  // anidada dentro de cada acordeon "Editar" (esas <tr> no tienen
  // data-buscar/data-categoria). Como esto corre dentro de un forEach, la
  // primera fila de ticket que aparecia (de CUALQUIER equipo con tickets)
  // tiraba un TypeError al leer tr.dataset.buscar y cortaba el forEach ahi
  // mismo -- todos los equipos que venian despues en la lista se quedaban
  // sin filtrar (por eso "112" no encontraba nada: el buscador se rompia
  // silenciosamente a mitad de tabla). tbody.children son solo los hijos
  // DIRECTOS (las filas de equipo y sus paneles), nunca las anidadas.
  const tbody = document.querySelector('#tabla-equipos tbody');
  const filas = tbody ? Array.from(tbody.children).filter(function (tr) {
    return tr.tagName === 'TR' && !tr.classList.contains('acordeon-panel');
  }) : [];
  let visibles = 0;
  filas.forEach(function (tr) {
    const coincideTexto = !q || tr.dataset.buscar.indexOf(q) !== -1;
    let coincideCategoria = true;
    if (netwatchCategoriaActiva === '__sin_categoria__') {
      coincideCategoria = !tr.dataset.categoria;
    } else if (netwatchCategoriaActiva) {
      coincideCategoria = tr.dataset.categoria === netwatchCategoriaActiva;
    }
    // Filtro de estado_deteccion: por defecto oculta descartados
    let noEsDescartado = true;
    if (!netwatchMostrarDescartados) {
      const texto = tr.dataset.buscar || '';
      noEsDescartado = !texto.includes('descartado');
    }
    const visible = coincideTexto && coincideCategoria && noEsDescartado;
    tr.style.display = visible ? '' : 'none';
    if (visible) visibles++;
  });
  const vacio = document.getElementById('equipos-vacio');
  if (vacio) vacio.style.display = visibles === 0 ? 'block' : 'none';
}

// Recordar busqueda/categoria/scroll entre recargas -- al guardar una edicion
// el formulario hace un POST normal que recarga la pagina, y sin esto se
// perdia el texto buscado, la pestana de categoria y el lugar donde ibas.
const NETWATCH_FILTRO_TEXTO_KEY = 'netwatchEquiposFiltroTexto';
const NETWATCH_FILTRO_CATEGORIA_KEY = 'netwatchEquiposFiltroCategoria';
const NETWATCH_SCROLL_KEY = 'netwatchEquiposScroll';

document.getElementById('buscador-equipos').addEventListener('input', function () {
  localStorage.setItem(NETWATCH_FILTRO_TEXTO_KEY, this.value);
  netwatchAplicarFiltrosEquipos();
});

function netwatchFiltrarCategoria(btn) {
  netwatchCategoriaActiva = btn.dataset.categoria;
  localStorage.setItem(NETWATCH_FILTRO_CATEGORIA_KEY, netwatchCategoriaActiva);
  document.querySelectorAll('#categoria-tabs .categoria-tab').forEach(function (b) {
    b.classList.toggle('activa', b === btn);
  });
  netwatchAplicarFiltrosEquipos();
}

// Guarda donde estabas parado justo antes de que "Guardar Cambios" recargue la pagina.
document.querySelectorAll('.acordeon-form').forEach(function (form) {
  form.addEventListener('submit', function () {
    sessionStorage.setItem(NETWATCH_SCROLL_KEY, String(window.scrollY));
  });
});

// Al cargar la pagina (incluyendo justo despues de guardar una edicion):
// restaura la busqueda, la pestana de categoria activa y el scroll de antes.
(function netwatchRestaurarEstadoEquipos() {
  const textoGuardado = localStorage.getItem(NETWATCH_FILTRO_TEXTO_KEY);
  if (textoGuardado) {
    document.getElementById('buscador-equipos').value = textoGuardado;
  }
  const categoriaGuardada = localStorage.getItem(NETWATCH_FILTRO_CATEGORIA_KEY);
  if (categoriaGuardada !== null) {
    netwatchCategoriaActiva = categoriaGuardada;
    document.querySelectorAll('#categoria-tabs .categoria-tab').forEach(function (b) {
      b.classList.toggle('activa', b.dataset.categoria === categoriaGuardada);
    });
  }
  netwatchAplicarFiltrosEquipos();
  const scrollGuardado = sessionStorage.getItem(NETWATCH_SCROLL_KEY);
  if (scrollGuardado) {
    sessionStorage.removeItem(NETWATCH_SCROLL_KEY);
    requestAnimationFrame(function () {
      window.scrollTo(0, parseInt(scrollGuardado, 10));
    });
  }
})();

// --- Estado Red / Agente en vivo, sin recargar la pagina -------------------
// Antes solo se actualizaba al recargar. Andres lo noto justo despues de
// reiniciar un equipo: se queda mirando la tabla varios minutos sin saber
// si ya volvio, hasta que se le ocurre refrescar la pagina a mano. Ahora se
// consulta un endpoint liviano (solo Estado Red + Agente, sin el resto de
// la ficha) cada 12s y se pintan solo esas dos celdas de cada fila -- el
// resto de la tabla (busqueda, acordeon abierto, seleccion de checkboxes,
// etc.) queda intacto.
const NETWATCH_ESTADO_VIVO_INTERVALO_MS = 12000;

function netwatchActualizarEstadoVivo() {
  fetch(NETWATCH_URL_ESTADO_VIVO)
    .then(function (r) { return r.json(); })
    .then(function (datos) {
      Object.keys(datos).forEach(function (id) {
        const fila = document.querySelector('tr[data-id="' + id + '"]');
        if (!fila) return;
        const info = datos[id];

        const celdaRed = fila.querySelector('.estado-red-cell');
        if (celdaRed && info.origen !== 'manual') {
          celdaRed.innerHTML = '<span class="tag ' + (info.en_linea ? 'tag-confirmado' : 'tag-descartado') + '">'
            + '<span class="dot ' + (info.en_linea ? 'dot-on' : 'dot-off') + '"></span> '
            + (info.en_linea ? 'Online' : 'Offline') + '</span>';
        }

        const celdaAgente = fila.querySelector('.agente-cell');
        if (celdaAgente) celdaAgente.dataset.visto = info.agente_visto_en || '';
      });
      netwatchPintarAgentes();
    })
    .catch(function () {
      // Fallo transitorio (Wi-Fi, VPN, el server local reiniciando) -- no es
      // grave, se reintenta solo en el proximo ciclo sin avisarle nada al
      // usuario; las columnas se quedan con el ultimo valor conocido.
    });
}
setInterval(netwatchActualizarEstadoVivo, NETWATCH_ESTADO_VIVO_INTERVALO_MS);

// --- apagado remoto en bloque, via cola de Firebase ------------------------
// Reemplaza el confirm() nativo de una sola linea (ilegible con lotes
// grandes) por un modal con la lista scrolleable de equipos afectados, y
// el envio pasa a ser async con barra de progreso -- encolar cada equipo es
// UNA llamada HTTP a Firebase (ver apagado_remoto.encolar_comando_equipo),
// asi que con muchos equipos marcados el navegador se quedaba pegado varios
// segundos sin ningun aviso de que seguia trabajando (Andres lo pidio el
// 2026-08-24 despues de notar la demora real Firebase-al-agente).
// (movida al bloque de constantes en el template, ver arriba del <script src=admin_equipos.js>)
// (movida al bloque de constantes en el template, ver arriba del <script src=admin_equipos.js>)
// (movida al bloque de constantes en el template, ver arriba del <script src=admin_equipos.js>)
// (movida al bloque de constantes en el template, ver arriba del <script src=admin_equipos.js>)
let netwatchApagadoAccionPendiente = null;

function netwatchAbrirConfirmarApagado(accion) {
  const filas = Array.prototype.map.call(
    document.querySelectorAll('.equipo-check:checked'), function (cb) { return cb.closest('tr'); }
  );
  if (filas.length === 0) return;

  netwatchApagadoAccionPendiente = accion;
  const verbo = accion === 'reiniciar' ? 'REINICIO' : 'APAGADO';
  document.getElementById('confirmar-apagado-titulo').textContent = 'Confirmar ' + verbo.toLowerCase();
  document.getElementById('confirmar-apagado-texto').textContent =
    'Vas a dejar encolado el ' + verbo + ' de ' + filas.length + ' equipo(s) (aviso de 60 segundos):';

  const lista = document.getElementById('confirmar-apagado-lista');
  lista.innerHTML = '';
  filas.forEach(function (tr) {
    const celdas = tr.querySelectorAll('td');
    // celda 1 = nombre de equipo, celda 3 = IP (ver <thead> de la tabla)
    const nombre = celdas[1] ? celdas[1].textContent.trim() : '';
    const ip = celdas[3] ? celdas[3].textContent.trim() : '';
    const li = document.createElement('li');
    li.style.padding = '5px 10px';
    li.style.borderBottom = '1px solid #1c2333';
    li.textContent = (nombre || '(sin nombre)') + (ip ? ' -- ' + ip : '');
    lista.appendChild(li);
  });

  document.getElementById('confirmar-apagado-nota').textContent =
    'Solo se ejecuta en las PCs que ya tengan instalado el agente NetWatch (ver agente_apagado/) -- ' +
    'en el resto, la orden queda esperando sin hacer nada hasta que se instale ahi tambien. Cada equipo ' +
    'va a mostrar un aviso de Windows con cuenta regresiva antes de ' +
    (accion === 'reiniciar' ? 'reiniciarse.' : 'apagarse.');

  document.getElementById('confirmar-apagado-btn').onclick = netwatchIniciarApagadoMasivo;
  document.getElementById('modal-confirmar-apagado').classList.add('modal-open');
}

function netwatchCerrarConfirmarApagado() {
  document.getElementById('modal-confirmar-apagado').classList.remove('modal-open');
}

function netwatchIniciarApagadoMasivo() {
  const ids = Array.prototype.map.call(
    document.querySelectorAll('.equipo-check:checked'), function (cb) { return cb.value; }
  );
  const accion = netwatchApagadoAccionPendiente;
  netwatchCerrarConfirmarApagado();
  if (ids.length === 0 || !accion) return;

  const body = new URLSearchParams();
  body.append('accion', accion);
  ids.forEach(function (id) { body.append('equipo_ids', id); });

  const barra = document.getElementById('barra-apagado-progreso');
  const texto = document.getElementById('apagado-progreso-texto');
  const fill = document.getElementById('apagado-progreso-fill');
  barra.style.display = 'block';
  fill.style.width = '0%';
  texto.textContent = 'Encolando 0 / ' + ids.length + '...';

  fetch(NETWATCH_URL_APAGAR_MASIVO, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  })
    .then(function (r) { return r.json(); })
    .then(function (r) {
      if (!r.iniciado) {
        texto.textContent = 'Ya hay un lote de apagado/reinicio corriendo -- espera a que termine y vuelve a intentar.';
        return;
      }
      netwatchPollApagado(accion);
    })
    .catch(function () {
      texto.textContent = 'No se pudo conectar con el servidor local. Revisa que NetWatch siga corriendo.';
    });
}

// El encolado/confirmacion corre en un HILO DEL SERVIDOR (ver
// apagado_remoto.iniciar_encolado_async) -- sigue trabajando aunque el
// navegador pierda un fetch puntual (Wi-Fi, VPN, un timeout suelto). Antes,
// UN solo fetch fallido mostraba "se perdio la conexion" de una y dejaba de
// consultar, aunque el equipo se hubiera reiniciado bien igual -- Andres lo
// vio pasar justo despues de que un reinicio SI funciono. Ahora se
// reintenta varias veces con espera creciente antes de rendirse de verdad.
let netwatchApagadoReintentos = 0;
const NETWATCH_APAGADO_MAX_REINTENTOS = 8;

function netwatchPollApagado(accion) {
  fetch(NETWATCH_URL_APAGAR_MASIVO_ESTADO)
    .then(function (r) { return r.json(); })
    .then(function (estado) {
      netwatchApagadoReintentos = 0;
      const texto = document.getElementById('apagado-progreso-texto');
      const fill = document.getElementById('apagado-progreso-fill');

      if (estado.fase === 'esperando_vuelta') {
        // Fase 3 (solo reiniciar): ya se confirmo que el agente ejecuto la
        // orden -- ahora se espera a que la PC vuelva a bootear y su tarea
        // programada avise de nuevo a Firebase. El scanner normal casi
        // nunca alcanza a pillar un reinicio rapido (dura menos que su
        // intervalo de 60-120s), asi que esta es la unica senal confiable
        // de "ya volvio". Plazo maximo 8 min.
        const pct = estado.total_volver > 0
          ? Math.min(100, Math.round((estado.vueltos / estado.total_volver) * 100)) : 100;
        fill.style.width = pct + '%';
        fill.style.background = 'var(--teal, #2dd4bf)';
        texto.textContent = 'Esperando que vuelvan a conectarse... ' + estado.vueltos + ' / ' + estado.total_volver
          + ' (puede tardar unos minutos, hasta 8)';
      } else if (estado.fase === 'confirmando') {
        // Fase 2: ya se encolaron todas las ordenes -- ahora se espera a
        // que cada agente las recoja y las borre de Firebase (esa es la
        // senal de "se ejecuto de verdad"), con un plazo maximo de 90s para
        // que esto nunca se quede esperando para siempre.
        const pct = estado.total_confirmar > 0
          ? Math.min(100, Math.round((estado.confirmados / estado.total_confirmar) * 100)) : 100;
        fill.style.width = pct + '%';
        fill.style.background = 'var(--green)';
        texto.textContent = 'Confirmando ejecucion... ' + estado.confirmados + ' / ' + estado.total_confirmar
          + ' (esperando hasta 90s a que cada PC avise)';
      } else {
        const pct = estado.total > 0 ? Math.min(100, Math.round((estado.procesados / estado.total) * 100)) : 0;
        fill.style.width = pct + '%';
        texto.textContent = 'Encolando ' + estado.procesados + ' / ' + estado.total + '...';
      }

      if (estado.corriendo) {
        setTimeout(function () { netwatchPollApagado(accion); }, 400);
        return;
      }
      const resumen = estado.resumen || { ok: [], fallidos: [], confirmados: [], sin_confirmar: [] };
      let apDetalle = '';
      if (resumen.fallidos.length > 0) {
        const primero = resumen.fallidos[0];
        apDetalle = (primero.ip + ': ' + primero.motivo).slice(0, 200);
      }
      const params = new URLSearchParams();
      params.set('apagado', '1');
      params.set('ap_accion', estado.accion || accion);
      params.set('ap_ok', resumen.ok.length);
      params.set('ap_fallo', resumen.fallidos.length);
      params.set('ap_confirmados', (resumen.confirmados || []).length);
      params.set('ap_sin_confirmar', (resumen.sin_confirmar || []).length);
      if (resumen.vueltos) params.set('ap_vueltos', resumen.vueltos.length);
      if (resumen.sin_volver) params.set('ap_sin_volver', resumen.sin_volver.length);
      if (apDetalle) params.set('ap_detalle', apDetalle);
      window.location.href = NETWATCH_URL_ADMIN_EQUIPOS + '?' + params.toString();
    })
    .catch(function () {
      const texto = document.getElementById('apagado-progreso-texto');
      netwatchApagadoReintentos++;
      if (netwatchApagadoReintentos <= NETWATCH_APAGADO_MAX_REINTENTOS) {
        // El trabajo real sigue corriendo en el servidor aunque esta
        // consulta puntual haya fallado -- se avisa sin alarmar y se
        // reintenta con espera creciente (1s, 2s, 3s...) en vez de dejarlo
        // colgado o rendirse de una.
        texto.textContent = 'Conexion inestable, reintentando... (' + netwatchApagadoReintentos + '/' + NETWATCH_APAGADO_MAX_REINTENTOS + ')';
        setTimeout(function () { netwatchPollApagado(accion); }, 1000 * netwatchApagadoReintentos);
        return;
      }
      texto.textContent = 'Se perdio la conexion con el servidor local varias veces seguidas. El proceso puede seguir '
        + 'corriendo igual en el servidor -- revisa la columna "Agente" y "Estado Red" de la tabla (se actualizan solas) '
        + 'o recarga la pagina en unos minutos para ver el resultado final.';
    });
}

// --- seleccion multiple + eliminar en bloque ------------------------------
// Antes solo se notaba que un equipo estaba marcado mirando el checkbox
// suelto (facil de perder de vista en una tabla larga) -- ahora la FILA
// completa se tine de verde transparente (ver .fila-seleccionada en
// style.css) para que se note de un vistazo cual esta seleccionado.
function netwatchMarcarFilaSeleccionada(cb) {
  const fila = cb.closest('tr');
  if (fila) fila.classList.toggle('fila-seleccionada', cb.checked);
}

function netwatchActualizarSeleccion() {
  const marcados = document.querySelectorAll('.equipo-check:checked');
  const barra = document.getElementById('barra-seleccion');
  const contador = document.getElementById('contador-seleccion');
  document.querySelectorAll('.equipo-check').forEach(netwatchMarcarFilaSeleccionada);
  if (!barra || !contador) return;
  if (marcados.length > 0) {
    barra.style.display = 'flex';
    contador.textContent = marcados.length + ' seleccionado' + (marcados.length > 1 ? 's' : '');
  } else {
    barra.style.display = 'none';
  }
  const checkTodos = document.getElementById('check-todos');
  const visibles = Array.prototype.filter.call(
    document.querySelectorAll('.equipo-check'),
    function (cb) { const fila = cb.closest('tr'); return fila && fila.style.display !== 'none'; }
  );
  if (checkTodos) {
    checkTodos.checked = visibles.length > 0 && visibles.every(function (cb) { return cb.checked; });
  }
}

function netwatchToggleTodos(origen) {
  document.querySelectorAll('.equipo-check').forEach(function (cb) {
    const fila = cb.closest('tr');
    if (fila && fila.style.display !== 'none') cb.checked = origen.checked;
  });
  netwatchActualizarSeleccion();
}

// --- panel "Editar" inline (acordeon), igual que en Infraestructura --------
// Asi se puede editar un equipo sin salir de Inventario de Equipos y seguir
// de inmediato con el siguiente.
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
