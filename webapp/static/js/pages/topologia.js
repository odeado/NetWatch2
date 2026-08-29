// static/js/pages/topologia.js
// JS de Topologia -- extraido de templates/topologia.html tal cual, sin
// cambios de logica (Fase 1 del reorden). Datos del servidor via los
// <script type="application/json"> netwatch-equipos-data /
// netwatch-dispositivos-data que quedaron en el template.

const NETWATCH_EQUIPOS = JSON.parse(document.getElementById('netwatch-equipos-data').textContent);
const NETWATCH_DISPOSITIVOS = JSON.parse(document.getElementById('netwatch-dispositivos-data').textContent);

function netwatchEscapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// Los primeros 3 octetos de una IP (su subred /24), usado para agrupar
// equipos/dispositivos por sucursal real en vez de confiar solo en el
// texto libre "sucursal" (que a veces viene mal cargado).
function netwatchSubred24(ip) {
  if (!ip) return null;
  const partes = String(ip).trim().split('.');
  return partes.length === 4 ? partes.slice(0, 3).join('.') : null;
}

const NETWATCH_FILTRO_TIPO_KEY = 'netwatchInfraFiltroTipo';
const NETWATCH_FILTRO_ESTADO_RED_KEY = 'netwatchInfraFiltroEstadoRed';
let netwatchTipoActivo = '';
let netwatchEstadoRedActivo = '';

function netwatchFiltrarPorTipo(tipo, btn) {
  netwatchTipoActivo = tipo;
  localStorage.setItem(NETWATCH_FILTRO_TIPO_KEY, tipo);
  document.querySelectorAll('#tipo-tabs .categoria-tab').forEach(function (b) {
    b.classList.toggle('activa', btn ? b === btn : b.dataset.tipoFiltro === tipo);
  });
  netwatchAplicarFiltrosInfraestructura();
}

function netwatchFiltrarPorEstadoRed(estado, btn) {
  netwatchEstadoRedActivo = estado;
  localStorage.setItem(NETWATCH_FILTRO_ESTADO_RED_KEY, estado);
  document.querySelectorAll('#estado-red-tabs .categoria-tab').forEach(function (b) {
    b.classList.toggle('activa', btn ? b === btn : b.dataset.estadoFiltro === estado);
  });
  netwatchAplicarFiltrosInfraestructura();
}

// Un dispositivo tiene que pasar AMBOS filtros a la vez (tipo Y estado de
// red) para quedar visible -- mismo patron que texto+categoria en
// admin_equipos.html.
function netwatchFilaCoincideEstadoRed(fila) {
  if (!netwatchEstadoRedActivo) return true;
  const valor = fila.dataset.enLinea; // '1' online, '0' offline, '' sin datos, undefined = sin IP
  if (valor === undefined) return false; // sin IP no entra en ningun filtro de estado
  if (netwatchEstadoRedActivo === 'online') return valor === '1';
  if (netwatchEstadoRedActivo === 'offline') return valor === '0';
  if (netwatchEstadoRedActivo === 'sin_datos') return valor === '';
  return true;
}

function netwatchAplicarFiltrosInfraestructura() {
  const tabla = document.getElementById('tabla-infraestructura');
  if (!tabla) return;

  const gruposConVisibles = new Set();
  tabla.querySelectorAll('tbody tr[data-tipo]').forEach(function (fila) {
    const coincideTipo = !netwatchTipoActivo || fila.dataset.tipo === netwatchTipoActivo;
    const coincide = coincideTipo && netwatchFilaCoincideEstadoRed(fila);
    fila.style.display = coincide ? '' : 'none';
    if (coincide) gruposConVisibles.add(fila.dataset.grupoSucursal);
  });

  // Banners de sucursal: se ocultan si NINGUNA de sus filas quedo visible.
  const ciudadesConVisibles = new Set();
  tabla.querySelectorAll('tbody tr.banner-sucursal-fila').forEach(function (banner) {
    const visible = gruposConVisibles.has(banner.id);
    banner.style.display = visible ? '' : 'none';
    if (visible) ciudadesConVisibles.add(banner.dataset.grupoCiudad);
  });

  // Banners de ciudad: se ocultan si ninguna de sus sucursales quedo visible.
  tabla.querySelectorAll('tbody tr.banner-ciudad-fila').forEach(function (banner) {
    banner.style.display = ciudadesConVisibles.has(banner.id) ? '' : 'none';
  });
}

document.addEventListener('DOMContentLoaded', function () {
  const tipoGuardado = localStorage.getItem(NETWATCH_FILTRO_TIPO_KEY);
  if (tipoGuardado) {
    netwatchTipoActivo = tipoGuardado;
    document.querySelectorAll('#tipo-tabs .categoria-tab').forEach(function (b) {
      b.classList.toggle('activa', b.dataset.tipoFiltro === tipoGuardado);
    });
  }
  const estadoGuardado = localStorage.getItem(NETWATCH_FILTRO_ESTADO_RED_KEY);
  if (estadoGuardado) {
    netwatchEstadoRedActivo = estadoGuardado;
    document.querySelectorAll('#estado-red-tabs .categoria-tab').forEach(function (b) {
      b.classList.toggle('activa', b.dataset.estadoFiltro === estadoGuardado);
    });
  }
  if (tipoGuardado || estadoGuardado) netwatchAplicarFiltrosInfraestructura();
});

function netwatchToggleAcordeon(key) {
  const panel = document.getElementById('panel-' + key);
  if (!panel) return;
  const abriendo = panel.style.display === 'none';
  // acordeon: cierra cualquier otro panel abierto antes de abrir este
  document.querySelectorAll('.acordeon-panel').forEach(function (p) {
    p.style.display = 'none';
  });
  panel.style.display = abriendo ? 'table-row' : 'none';
  if (abriendo) {
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

function netwatchSeleccionarPuerto(el, mostrarTodasCiudades) {
  const deviceId = el.dataset.device;
  const info = JSON.parse(el.dataset.info);

  el.closest('.puertos-row').querySelectorAll('.puerto-box').forEach(function (b) {
    b.classList.remove('puerto-selected');
  });
  el.classList.add('puerto-selected');

  const panel = document.getElementById('puerto-detalle-' + deviceId);
  const eq = info.equipo;
  const disp = info.dispositivo_destino;
  const ocupado = eq || disp;

  let html = '<div class="puerto-detalle-card">';
  html += '<p class="puerto-detalle-label">PUERTO SELECCIONADO</p>';
  html += '<h4>Interfaz: ' + netwatchEscapeHtml(info.label) +
          ' <span class="tag ' + (ocupado ? 'tag-confirmado' : 'tag-descartado') + '">' +
          (ocupado ? 'Ocupado' : 'Libre') + '</span></h4>';

  if (eq) {
    html += '<div class="puerto-equipo-card">';
    html += '<div class="puerto-equipo-nombre">' + netwatchEscapeHtml(eq.hostname || eq.ip) +
            ' <span class="tag ' + (eq.en_linea ? 'tag-confirmado' : 'tag-offline') + '">' +
            (eq.en_linea ? 'Online' : 'Offline') + '</span></div>';
    html += '<div class="muted">' + netwatchEscapeHtml(eq.ip) + '</div>';
    if (eq.responsable) {
      html += '<div class="kv"><span class="k">Responsable</span><span class="v">' + netwatchEscapeHtml(eq.responsable) + '</span></div>';
    }
    if (eq.marca || eq.modelo) {
      html += '<div class="kv"><span class="k">Marca / Modelo</span><span class="v">' +
              netwatchEscapeHtml((eq.marca || '-') + ' ' + (eq.modelo || '')) + '</span></div>';
    }
    if (eq.numero_serie) {
      html += '<div class="kv"><span class="k">N. Serie</span><span class="v">' + netwatchEscapeHtml(eq.numero_serie) + '</span></div>';
    }
    if (eq.mac) {
      html += '<div class="kv"><span class="k">MAC</span><span class="v">' + netwatchEscapeHtml(eq.mac) + '</span></div>';
    }
    if (eq.notas) {
      html += '<div class="kv"><span class="k">Notas</span><span class="v">' + netwatchEscapeHtml(eq.notas) + '</span></div>';
    }
    html += '<a href="/equipo/' + eq.id + '" class="ip-link">Ver ficha completa &rarr;</a>';
    html += '</div>';
  } else if (disp) {
    html += '<div class="puerto-equipo-card">';
    html += '<div class="puerto-equipo-nombre">' + netwatchEscapeHtml(disp.nombre) +
            ' <span class="tag tag-confirmado">' + netwatchEscapeHtml(disp.tipo) + '</span></div>';
    if (disp.marca || disp.modelo) {
      html += '<div class="kv"><span class="k">Marca / Modelo</span><span class="v">' +
              netwatchEscapeHtml((disp.marca || '-') + ' ' + (disp.modelo || '')) + '</span></div>';
    }
    if (disp.ip) {
      html += '<div class="kv"><span class="k">IP</span><span class="v">' + netwatchEscapeHtml(disp.ip) + '</span></div>';
    }
    if (disp.numero_serie) {
      html += '<div class="kv"><span class="k">N. Serie</span><span class="v">' + netwatchEscapeHtml(disp.numero_serie) + '</span></div>';
    }
    if (disp.mac) {
      html += '<div class="kv"><span class="k">MAC</span><span class="v">' + netwatchEscapeHtml(disp.mac) + '</span></div>';
    }
    if (disp.enlace) {
      html += '<div class="kv"><span class="k">Enlace ISP</span><span class="v"><span class="tag tag-confirmado">' + netwatchEscapeHtml(disp.enlace) + '</span></span></div>';
    }
    if (info.dispositivo_destino_puerto) {
      html += '<div class="kv"><span class="k">Boca en destino</span><span class="v"><a href="#" class="ip-link" onclick="netwatchToggleAcordeon(\'puertos-' + disp.id + '\'); return false;">' +
              netwatchEscapeHtml(info.dispositivo_destino_puerto) + ' &rarr;</a></span></div>';
    }
    html += '<button type="button" class="btn btn-reset" onclick="netwatchToggleAcordeon(\'puertos-' + disp.id + '\')">Ver este dispositivo &rarr;</button>';
    html += '</div>';
  } else {
    html += '<p class="empty small">Este puerto esta libre.</p>';
  }

  // Filtra la lista de equipos/dispositivos a los de la MISMA sucursal/subred
  // que este switch/fortinet -- filtrar solo por CIUDAD no alcanza porque
  // Antofagasta tiene 3 subredes distintas (Matta=172.30.102.x,
  // Rendic=172.30.100.x, Rendic2=172.30.101.x), asi que salian mezclados
  // equipos de sucursales que no tienen nada que ver. Se prioriza la subred
  // real (los primeros 3 octetos de la IP, ej. "172.30.100") por sobre el
  // texto libre de "sucursal", porque ese campo a veces viene mal cargado
  // (ej. un equipo con IP de la subred Rendic pero con sucursal="Matta" a
  // mano) -- la IP real del switch no miente. Si el switch no tiene IP propia
  // se usa la sucursal como respaldo.
  const dispositivoActual = NETWATCH_DISPOSITIVOS.find(function (d) { return String(d.id) === String(deviceId); });
  const subredActual = mostrarTodasCiudades ? null : netwatchSubred24(dispositivoActual && dispositivoActual.ip);
  const sucursalActual = mostrarTodasCiudades ? null :
    (((dispositivoActual && dispositivoActual.sucursal) || '').trim().toLowerCase() || null);
  const ciudadActual = mostrarTodasCiudades ? null :
    (((dispositivoActual && dispositivoActual.ciudad) || '').trim().toLowerCase() || null);
  const filtroActivo = !!(subredActual || sucursalActual || ciudadActual);

  // La subred es el filtro mas preciso (mismo /24 exacto), la sucursal es un
  // respaldo por si el switch no tiene IP propia, y la ciudad es el mas
  // amplio de los tres -- se agrega porque el usuario pidio explicitamente
  // poder ver TODOS los equipos/dispositivos de la ciudad (no solo los de la
  // subred exacta de este switch), ya que varios equipos legitimos de la
  // misma sucursal quedaban fuera si su IP/sucursal no calzaba exacto.
  function netwatchCoincideFiltroUbicacion(item) {
    const subredOk = subredActual && netwatchSubred24(item.ip) === subredActual;
    const sucursalOk = sucursalActual && ((item.sucursal || '').trim().toLowerCase()) === sucursalActual;
    const ciudadOk = ciudadActual && ((item.ciudad || '').trim().toLowerCase()) === ciudadActual;
    return subredOk || sucursalOk || ciudadOk;
  }

  const equiposFiltrados = NETWATCH_EQUIPOS.filter(function (e) {
    return !filtroActivo || netwatchCoincideFiltroUbicacion(e) || (eq && eq.id === e.id);
  });
  const dispositivosFiltrados = NETWATCH_DISPOSITIVOS.filter(function (d) {
    if (String(d.id) === String(deviceId)) return false;
    return !filtroActivo || netwatchCoincideFiltroUbicacion(d) || (disp && disp.id === d.id);
  });

  // Contexto para el buscador en vivo de este puerto (ver netwatchBuscarPuerto).
  NETWATCH_PUERTO_CTX[deviceId] = {
    filtroActivo: filtroActivo,
    equiposFiltrados: equiposFiltrados,
    dispositivosFiltrados: dispositivosFiltrados,
  };
  const seleccionActualTexto = eq ? (eq.hostname || eq.ip) :
    (disp ? (disp.nombre + (info.dispositivo_destino_puerto ? ' (boca ' + info.dispositivo_destino_puerto + ')' : '')) : '');
  const destinoActual = eq ? ('equipo:' + eq.id) : (disp ? ('dispositivo:' + disp.id) : '');
  const destinoPuertoActual = disp ? (info.dispositivo_destino_puerto || '') : '';

  html += '<form method="post" action="/topologia/dispositivos/' + deviceId + '/puertos" class="puerto-assign-form" ' +
          'onsubmit="return netwatchGuardarPuerto(event, \'' + deviceId + '\')">';
  html += '<input type="hidden" name="puerto" value="' + netwatchEscapeHtml(info.label) + '">';
  html += '<input type="hidden" name="destino" id="puerto-destino-' + deviceId + '" value="' + netwatchEscapeHtml(destinoActual) + '">';
  html += '<input type="hidden" name="destino_puerto" id="puerto-destino-boca-' + deviceId + '" value="' + netwatchEscapeHtml(destinoPuertoActual) + '">';
  html += '<div class="puerto-combo">';
  html += '<input type="text" class="puerto-buscador" id="puerto-input-' + deviceId + '" autocomplete="off" ' +
          'placeholder="Buscar equipo o dispositivo por nombre/IP..." value="' + netwatchEscapeHtml(seleccionActualTexto) + '" ' +
          'oninput="netwatchBuscarPuerto(\'' + deviceId + '\')" ' +
          'onfocus="netwatchBuscarPuerto(\'' + deviceId + '\')" ' +
          'onblur="netwatchOcultarResultadosPuerto(\'' + deviceId + '\')">';
  if (ocupado) {
    html += '<button type="button" class="btn-limpiar-puerto" title="Quitar seleccion" ' +
            'onmousedown="event.preventDefault(); netwatchLimpiarPuerto(\'' + deviceId + '\')">&times;</button>';
  }
  html += '<div class="puerto-resultados" id="puerto-resultados-' + deviceId + '" style="display:none;"></div>';
  html += '</div>';
  html += '<button type="submit" class="btn btn-confirm">Guardar</button>';
  if (ocupado) {
    html += '<button type="button" class="btn btn-discard" ' +
            'onclick="netwatchLimpiarPuerto(\'' + deviceId + '\'); netwatchGuardarPuerto(event, \'' + deviceId + '\', this.form);">Desconectar puerto</button>';
  }
  html += '<p class="puerto-guardado-aviso" style="display:none;color:#22c55e;font-size:0.85rem;margin-top:6px;">&#10003; Guardado</p>';
  html += '</form>';

  panel.innerHTML = html;
}

// Contexto (por dispositivo) para el buscador en vivo del puerto: lista de
// equipos/dispositivos ya filtrados por ciudad, para no tener que recalcular
// en cada tecla.
const NETWATCH_PUERTO_CTX = {};

// Pinta en vivo, debajo del input, los resultados que van coincidiendo con lo
// escrito -- sin necesitar abrir ningun <select> para verlos (como pidio el
// usuario: "un buscador de verdad", igual al de otros programas).
function netwatchBuscarPuerto(deviceId) {
  const ctx = NETWATCH_PUERTO_CTX[deviceId];
  const input = document.getElementById('puerto-input-' + deviceId);
  const cont = document.getElementById('puerto-resultados-' + deviceId);
  if (!ctx || !input || !cont) return;
  const q = input.value.trim().toLowerCase();

  // Los dispositivos de red (switches/routers/modems) van PRIMERO -- al
  // conectar una boca lo normal es buscar otro switch/dispositivo, no un PC,
  // y con muchos equipos en la misma ciudad/sucursal (algunas sucursales
  // tienen 20+) el tope de resultados se llenaba solo con PCs y dejaba
  // afuera dispositivos que si calzaban con el filtro (ej: "Emelnor Nuevo"
  // nunca aparecia porque quedaba mas alla del tope de 30 items).
  const items = [];
  items.push({ tipo: '__libre__', texto: '-- Dejar puerto libre / desconectar --' });
  ctx.dispositivosFiltrados.forEach(function (d) {
    items.push({ tipo: 'dispositivo', id: d.id, texto: d.nombre + ' (' + d.tipo + ')' });
  });
  ctx.equiposFiltrados.forEach(function (e) {
    items.push({ tipo: 'equipo', id: e.id, texto: (e.hostname || e.ip) + ' (' + e.ip + ')' });
  });

  const filtrados = !q ? items : items.filter(function (it) {
    return it.tipo === '__libre__' || it.texto.toLowerCase().indexOf(q) !== -1;
  });

  const TOPE_RESULTADOS = 60;
  let html = '';
  if (filtrados.length === 0) {
    html = '<div class="puerto-resultado-vacio">Sin coincidencias</div>';
  } else {
    filtrados.slice(0, TOPE_RESULTADOS).forEach(function (it) {
      const claseExtra = it.tipo === '__libre__' ? ' puerto-resultado-libre' : '';
      html += '<div class="puerto-resultado-item' + claseExtra + '" data-tipo="' + it.tipo + '" data-id="' + (it.id || '') + '">' +
              netwatchEscapeHtml(it.texto) + '</div>';
    });
    if (filtrados.length > TOPE_RESULTADOS) {
      html += '<div class="puerto-resultado-vacio">Hay ' + (filtrados.length - TOPE_RESULTADOS) +
              ' resultado(s) mas -- sigue escribiendo para filtrar</div>';
    }
  }
  if (ctx.filtroActivo) {
    html += '<div class="puerto-resultado-item puerto-resultado-todas" data-tipo="__todas__">Ver equipos de todas las sucursales/ciudades &rarr;</div>';
  }
  cont.innerHTML = html;
  cont.style.display = 'block';

  cont.onmousedown = function (ev) {
    const item = ev.target.closest('.puerto-resultado-item');
    if (!item) return;
    ev.preventDefault();
    const tipo = item.dataset.tipo;
    if (tipo === '__libre__') { netwatchLimpiarPuerto(deviceId); return; }
    if (tipo === '__todas__') { netwatchMostrarTodasCiudadesPuerto(deviceId); return; }
    netwatchElegirPuerto(deviceId, tipo, item.dataset.id, item.textContent);
  };
}

// El usuario clickeo un resultado de la lista en vivo. Si es un equipo, queda
// seleccionado altiro. Si es OTRO switch/dispositivo, todavia falta un paso:
// hay que elegir a que boca especifica de ese switch se conecta (no se puede
// dejar "todo el switch" como destino -- una fibra/cobre siempre llega a una
// boca puntual del otro lado).
function netwatchElegirPuerto(deviceId, tipo, id, texto) {
  if (tipo === 'dispositivo') {
    netwatchMostrarBocasDestino(deviceId, id, texto);
    return;
  }
  const destinoInput = document.getElementById('puerto-destino-' + deviceId);
  const destinoPuertoInput = document.getElementById('puerto-destino-boca-' + deviceId);
  const input = document.getElementById('puerto-input-' + deviceId);
  const cont = document.getElementById('puerto-resultados-' + deviceId);
  if (destinoInput) destinoInput.value = tipo + ':' + id;
  if (destinoPuertoInput) destinoPuertoInput.value = '';
  if (input) input.value = texto;
  if (cont) { cont.style.display = 'none'; cont.innerHTML = ''; }
}

// Segundo paso al elegir otro switch como destino: muestra sus bocas (con su
// estado libre/ocupado) para elegir la especifica a la que llega el cable.
function netwatchMostrarBocasDestino(deviceId, dispId, dispTexto) {
  const cont = document.getElementById('puerto-resultados-' + deviceId);
  const disp = NETWATCH_DISPOSITIVOS.find(function (d) { return String(d.id) === String(dispId); });
  if (!cont || !disp) return;

  let html = '<div class="puerto-resultado-item puerto-resultado-volver" data-accion="volver">&larr; Volver a buscar</div>';
  html += '<div class="puerto-resultado-subtitulo">Elige la boca de "' + netwatchEscapeHtml(disp.nombre) + '" a conectar:</div>';
  (disp.puertos || []).forEach(function (p) {
    const ocupado = !!(p.equipo || p.dispositivo_destino);
    const claseExtra = ocupado ? ' puerto-resultado-ocupado' : '';
    const detalle = p.equipo ? (p.equipo.hostname || p.equipo.ip) : (p.dispositivo_destino ? p.dispositivo_destino.nombre : '');
    html += '<div class="puerto-resultado-item' + claseExtra + '" data-accion="boca" data-disp-id="' + dispId + '" ' +
            'data-disp-texto="' + netwatchEscapeHtml(dispTexto) + '" data-puerto="' + netwatchEscapeHtml(p.label) + '">' +
            netwatchEscapeHtml(p.label) + (ocupado ? ' -- ocupado (' + netwatchEscapeHtml(detalle) + ')' : ' -- libre') + '</div>';
  });
  cont.innerHTML = html;
  cont.style.display = 'block';

  cont.onmousedown = function (ev) {
    const item = ev.target.closest('.puerto-resultado-item');
    if (!item) return;
    ev.preventDefault();
    if (item.dataset.accion === 'volver') { netwatchBuscarPuerto(deviceId); return; }
    if (item.dataset.accion === 'boca') {
      netwatchElegirBocaDestino(deviceId, item.dataset.dispId, item.dataset.dispTexto, item.dataset.puerto);
    }
  };
}

// Tercer paso: ya se eligio switch destino + boca especifica -- deja todo listo
// para Guardar (que es el que efectivamente envia y libera cualquier ocupante
// previo de esa boca en el otro switch).
function netwatchElegirBocaDestino(deviceId, dispId, dispTexto, puerto) {
  const destinoInput = document.getElementById('puerto-destino-' + deviceId);
  const destinoPuertoInput = document.getElementById('puerto-destino-boca-' + deviceId);
  const input = document.getElementById('puerto-input-' + deviceId);
  const cont = document.getElementById('puerto-resultados-' + deviceId);
  if (destinoInput) destinoInput.value = 'dispositivo:' + dispId;
  if (destinoPuertoInput) destinoPuertoInput.value = puerto;
  if (input) input.value = dispTexto + ' (boca ' + puerto + ')';
  if (cont) { cont.style.display = 'none'; cont.innerHTML = ''; }
}

// Limpia la seleccion actual (deja el puerto libre en el formulario, sin
// guardar todavia -- Guardar/Desconectar son los que efectivamente envian).
function netwatchLimpiarPuerto(deviceId) {
  const destinoInput = document.getElementById('puerto-destino-' + deviceId);
  const destinoPuertoInput = document.getElementById('puerto-destino-boca-' + deviceId);
  const input = document.getElementById('puerto-input-' + deviceId);
  const cont = document.getElementById('puerto-resultados-' + deviceId);
  if (destinoInput) destinoInput.value = '';
  if (destinoPuertoInput) destinoPuertoInput.value = '';
  if (input) input.value = '';
  if (cont) { cont.style.display = 'none'; cont.innerHTML = ''; }
}

function netwatchOcultarResultadosPuerto(deviceId) {
  // Pequeno delay para que el mousedown del item alcance a dispararse antes.
  setTimeout(function () {
    const cont = document.getElementById('puerto-resultados-' + deviceId);
    if (cont) cont.style.display = 'none';
  }, 150);
}

// "Ver equipos de todas las ciudades": vuelve a pintar el panel del mismo
// puerto pero sin el filtro por ciudad.
function netwatchMostrarTodasCiudadesPuerto(deviceId) {
  const box = document.querySelector('.puerto-box.puerto-selected[data-device="' + deviceId + '"]');
  if (box) netwatchSeleccionarPuerto(box, true);
}

// Guarda la asignacion de boca por AJAX (sin recargar la pagina), para poder
// seguir asignando otras bocas del mismo switch/fortinet sin perder el lugar
// -- antes cada "Guardar" hacia un POST normal que recargaba toda la pagina
// y cerraba el acordeon.
async function netwatchGuardarPuerto(ev, deviceId, formOverride) {
  ev.preventDefault();
  const form = formOverride || ev.target;
  const btns = form.querySelectorAll('button');
  btns.forEach(function (b) { b.disabled = true; });
  try {
    const resp = await fetch(form.action, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'XMLHttpRequest' },
      body: 'puerto=' + encodeURIComponent(form.puerto.value) + '&destino=' + encodeURIComponent(form.destino.value) +
            '&destino_puerto=' + encodeURIComponent(form.destino_puerto ? form.destino_puerto.value : ''),
    });
    const contentType = resp.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
      // El backend respondio pero no en JSON -- normalmente pasa cuando el
      // sistema local todavia no se reinicio despues de actualizar el
      // codigo, y por lo tanto sigue devolviendo la pagina completa (HTML).
      // El guardado en la base de datos SI ocurrio igual (el POST ya llego
      // al servidor); recargamos para mostrar el resultado en vez de dejar
      // al usuario pensando que "no guardo".
      window.location.reload();
      return false;
    }
    const data = await resp.json();
    if (data.ok) {
      netwatchActualizarPuertosDispositivo(deviceId, data.puertos, form.puerto.value);
      // El otro extremo de un enlace switch-switch (o el dispositivo que
      // quedo desconectado por la reasignacion) tambien puede tener sus
      // bocas visibles en pantalla -- refrescarlas sin recargar la pagina.
      (data.relacionados || []).forEach(function (rel) {
        netwatchActualizarSoloBoxes(rel.id, rel.puertos);
        netwatchActualizarPuertosEnMemoria(rel.id, rel.puertos);
      });
    }
  } catch (err) {
    // Fallo la conexion (ej. el servidor local no esta corriendo). Avisar en
    // vez de quedar en silencio.
    alert('No se pudo guardar: revisa que el sistema local (Flask) este corriendo.');
  } finally {
    btns.forEach(function (b) { b.disabled = false; });
  }
  return false;
}

// Actualiza en el DOM las bocas de un dispositivo con datos frescos del
// servidor (colores/ocupacion), y vuelve a pintar el panel de la boca que
// se acaba de guardar -- sin tocar el resto de la pagina.
function netwatchActualizarPuertosDispositivo(deviceId, puertos, puertoGuardado) {
  const porLabel = {};
  puertos.forEach(function (p) { porLabel[p.label] = p; });

  let boxGuardada = null;
  document.querySelectorAll('.puerto-box[data-device="' + deviceId + '"]').forEach(function (box) {
    const label = box.textContent.trim();
    const p = porLabel[label];
    if (!p) return;
    box.dataset.info = JSON.stringify(p);
    const ocupado = !!(p.equipo || p.dispositivo_destino);
    const offline = !!(p.equipo && !p.equipo.en_linea);
    const seleccionada = box.classList.contains('puerto-selected');
    box.className = 'puerto-box puerto-tipo-' + p.tipo + ' ' +
      (ocupado ? 'puerto-ocupado' : 'puerto-libre') + (offline ? ' puerto-offline' : '') +
      (seleccionada ? ' puerto-selected' : '');
    if (label === puertoGuardado) boxGuardada = box;
  });

  if (boxGuardada) {
    netwatchSeleccionarPuerto(boxGuardada);
    const aviso = document.querySelector('#puerto-detalle-' + deviceId + ' .puerto-guardado-aviso');
    if (aviso) {
      aviso.style.display = 'block';
      setTimeout(function () { aviso.style.display = 'none'; }, 2000);
    }
  }
}

// Igual que netwatchActualizarPuertosDispositivo pero solo repinta las bocas
// (color/ocupado) del OTRO dispositivo afectado por el guardado (el switch al
// que se conecto, o el que quedo libre por una reasignacion) -- sin tocar su
// panel de detalle, que puede no estar abierto en pantalla.
function netwatchActualizarSoloBoxes(deviceId, puertos) {
  const porLabel = {};
  puertos.forEach(function (p) { porLabel[p.label] = p; });

  document.querySelectorAll('.puerto-box[data-device="' + deviceId + '"]').forEach(function (box) {
    const label = box.textContent.trim();
    const p = porLabel[label];
    if (!p) return;
    box.dataset.info = JSON.stringify(p);
    const ocupado = !!(p.equipo || p.dispositivo_destino);
    const offline = !!(p.equipo && !p.equipo.en_linea);
    const seleccionada = box.classList.contains('puerto-selected');
    box.className = 'puerto-box puerto-tipo-' + p.tipo + ' ' +
      (ocupado ? 'puerto-ocupado' : 'puerto-libre') + (offline ? ' puerto-offline' : '') +
      (seleccionada ? ' puerto-selected' : '');
  });

  // Si el panel de detalle de ese dispositivo esta abierto mostrando una boca
  // que cambio, tambien hay que refrescar el detalle (no solo el color del
  // cuadro), para que no quede mostrando datos viejos ("Libre") en pantalla.
  const boxSeleccionada = document.querySelector('.puerto-box.puerto-selected[data-device="' + deviceId + '"]');
  if (boxSeleccionada) netwatchSeleccionarPuerto(boxSeleccionada);
}

// Actualiza la copia en memoria (NETWATCH_DISPOSITIVOS) de las bocas de un
// dispositivo afectado indirectamente por el guardado, para que los selectores
// "elegir boca destino" que se abran despues reflejen el estado fresco sin
// necesidad de recargar la pagina.
function netwatchActualizarPuertosEnMemoria(deviceId, puertos) {
  const disp = NETWATCH_DISPOSITIVOS.find(function (d) { return String(d.id) === String(deviceId); });
  if (disp) disp.puertos = puertos;
}
