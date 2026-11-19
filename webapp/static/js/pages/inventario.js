// static/js/pages/inventario.js
// JS de la pantalla de Inicio (Inventario) -- extraido de templates/index.html
// tal cual, sin cambios de logica, como parte del reorden a 1 archivo de JS por
// pagina (ver plan de reorden). Los datos que antes venian embebidos via Jinja
// siguen llegando igual: se leen de los <script type="application/json"> con id
// netwatch-equipos-data / netwatch-summary-data / netwatch-eventos-data /
// netwatch-subredes-data / netwatch-cidr-sitio-data que quedaron en el template.

let NETWATCH_EQUIPOS = JSON.parse(document.getElementById('netwatch-equipos-data').textContent);
let NETWATCH_SUMMARY = JSON.parse(document.getElementById('netwatch-summary-data').textContent);
let NETWATCH_EVENTOS = JSON.parse(document.getElementById('netwatch-eventos-data').textContent);
let NETWATCH_AGENTES = [];
// Catalogo subred(CIDR) -> nombre de ciudad, editable en Administracion >
// Parametros y Red -- es la unica fuente de verdad para el nombre que se
// muestra en los gauges (antes se sacaba del campo "ciudad" del primer
// equipo de cada grupo, que se llenaba inconsistente y a veces hacia
// aparecer el mismo sitio duplicado con dos nombres distintos).
const NETWATCH_SUBREDES = JSON.parse(document.getElementById('netwatch-subredes-data').textContent);
const NETWATCH_CIUDAD_POR_CIDR = {};
NETWATCH_SUBREDES.forEach(function (s) { NETWATCH_CIUDAD_POR_CIDR[s.cidr] = s.ciudad; });

// nombre de sitio (tal cual lo publica el scanner remoto, ej. "iquique") ->
// CIDR, para que al hacer clic en su pill de estado se pueda filtrar la
// grilla por esa subred (ver netwatchFiltrarPorSubred).
const NETWATCH_CIDR_POR_SITIO = JSON.parse(document.getElementById('netwatch-cidr-sitio-data').textContent);
let NETWATCH_ULTIMO_EVENTO_ID = NETWATCH_EVENTOS.length ? Math.max.apply(null, NETWATCH_EVENTOS.map(function (e) { return e.id; })) : 0;

// Por defecto la pantalla arranca mostrando solo CONFIRMADOS -- los
// pendientes/descartados no se ven hasta que el usuario clickea esa
// tarjeta a proposito (ver tarjeta "Pendientes"/"Descartados" y el tile
// "Total en inventario" para ver el inventario sin descartados). Antes arrancaba sin
// filtro y mezclaba detecciones nunca confirmadas con el resto, lo que
// llenaba la pantalla de fichas de equipos que en la practica no existen
// (ver conversacion 2026-08-21, sitios caidos que dejaron cientos de
// detecciones "pendiente" sin revisar).
let netwatchFiltroTipo = 'estado';   // 'online' | 'estado' | 'tickets' | null (total)
let netwatchFiltroValor = 'confirmado';  // 'online'/'offline' o 'confirmado'/'descartado'/'pendiente'
let netwatchBusqueda = '';

function netwatchIcono(nombre, clase) {
  const sprite = document.body.dataset.iconSprite;
  return '<svg class="nw-icon ' + (clase || '') + '" aria-hidden="true" focusable="false"><use href="' +
    netwatchEscapeHtml(sprite) + '#' + nombre + '"></use></svg>';
}

// Al descartar una ficha desde una vista que no es "Descartados" (ej. mientras
// se revisan los offline uno por uno), se saca de la vista al toque -- si no,
// se queda mezclada ahi con tag gris y hay que seguir mirandola entre las que
// todavia faltan revisar. Solo dura la sesion (se limpia si se restaura o se
// recarga la pagina); el estado real ya quedo guardado en la base al tiro.
const NETWATCH_OCULTOS_TRAS_DESCARTE = new Set();
let netwatchSonidoActivo = localStorage.getItem('netwatch_sonido') !== 'off';

function netwatchEscapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

// --- resumen (tarjetas clickeables) -----------------------------------
function netwatchSetFiltro(tipo, valor) {
  if (tipo === 'total') {
    netwatchFiltroTipo = null; netwatchFiltroValor = null;
  } else if (netwatchFiltroTipo === tipo && netwatchFiltroValor === (valor || null)) {
    // clic de nuevo sobre el mismo filtro activo -> lo saca (toggle)
    netwatchFiltroTipo = null; netwatchFiltroValor = null;
  } else {
    netwatchFiltroTipo = tipo; netwatchFiltroValor = valor || null;
  }
  netwatchRenderResumen();
  netwatchRenderFichas();
}

function netwatchRenderResumen() {
  const criticos = NETWATCH_EQUIPOS.filter(function (e) { return e.critico && e.estado_deteccion !== 'descartado'; }).length;
  const mapa = {
    total: NETWATCH_SUMMARY.total, en_linea: NETWATCH_SUMMARY.en_linea, fuera_de_linea: NETWATCH_SUMMARY.fuera_de_linea,
    confirmados: NETWATCH_SUMMARY.confirmados, descartados: NETWATCH_SUMMARY.descartados,
    pendientes: NETWATCH_SUMMARY.pendientes, tickets_abiertos: NETWATCH_SUMMARY.tickets_abiertos,
    sin_datos: NETWATCH_SUMMARY.sin_datos,
    criticos: criticos,
  };
  document.querySelectorAll('#resumen-cards [data-num]').forEach(function (el) {
    el.textContent = mapa[el.dataset.num];
  });
  document.querySelectorAll('#resumen-cards .card-btn').forEach(function (btn) {
    const tipo = btn.dataset.filtroTipo;
    const valor = btn.dataset.filtroValor || null;
    const activo = (tipo === 'total' && !netwatchFiltroTipo) ||
                   (tipo === netwatchFiltroTipo && valor === netwatchFiltroValor);
    btn.classList.toggle('active', !!activo);
    btn.setAttribute('aria-pressed', String(!!activo));
  });
}

// --- filtro + busqueda --------------------------------------------------
function netwatchIpANumero(ip) {
  if (!ip) return 0;
  const partes = String(ip).split('.').map(function (n) { return parseInt(n, 10) || 0; });
  return ((partes[0] || 0) * 16777216) + ((partes[1] || 0) * 65536) + ((partes[2] || 0) * 256) + (partes[3] || 0);
}

function netwatchAplicarFiltros() {
  const q = netwatchBusqueda.trim().toLowerCase();
  const verDescartados = netwatchFiltroTipo === 'estado' && netwatchFiltroValor === 'descartado';
  const filtrados = NETWATCH_EQUIPOS.filter(function (e) {
    // Las fichas descartadas solo aparecen al elegir explícitamente esa tarjeta.
    // Los filtros por conectividad, tickets, críticos o el total no deben
    // volver a mezclarlas con el inventario activo.
    if (!verDescartados && (e.estado_deteccion === 'descartado' || NETWATCH_OCULTOS_TRAS_DESCARTE.has(e.id))) return false;
    if (netwatchFiltroTipo === 'online') {
      if (e.sin_datos) return false;
      const enLinea = !!e.en_linea;
      if (netwatchFiltroValor === 'online' && !enLinea) return false;
      if (netwatchFiltroValor === 'offline' && enLinea) return false;
    } else if (netwatchFiltroTipo === 'sin_datos') {
      if (!e.sin_datos) return false;
    } else if (netwatchFiltroTipo === 'estado') {
      if (e.estado_deteccion !== netwatchFiltroValor) return false;
    } else if (netwatchFiltroTipo === 'tickets') {
      if (!e.tickets_abiertos) return false;
    } else if (netwatchFiltroTipo === 'critico') {
      if (!e.critico) return false;
    }
    if (q) {
      const campos = [e.hostname, e.ip, e.sucursal, e.ciudad, e.ubicacion, e.responsable, e.subred];
      const encontrado = campos.some(function (c) { return c && String(c).toLowerCase().indexOf(q) !== -1; });
      if (!encontrado) return false;
    }
    return true;
  });

  // offline primero (para verlos sin tener que buscarlos), despues por IP ascendente
  function grupoOrden(e) {
    if (e.origen === 'manual') return 1; // los manuales no tienen deteccion real, van con los online
    return e.en_linea ? 1 : 0;
  }
  filtrados.sort(function (a, b) {
    const diff = grupoOrden(a) - grupoOrden(b);
    if (diff !== 0) return diff;
    return netwatchIpANumero(a.ip) - netwatchIpANumero(b.ip);
  });
  return filtrados;
}

// --- gauges de subred (nivel de agua = % de equipos caidos) ---------------
function netwatchAgruparPorSubred() {
  const grupos = {};
  NETWATCH_EQUIPOS.forEach(function (e) {
    if (e.origen === 'manual' || e.estado_deteccion === 'descartado') return;
    const clave = e.subred || 'Sin subred';
    if (!grupos[clave]) {
      // El nombre mostrado sale del catalogo de Parametros y Red (por CIDR
      // exacto) -- si esta subred no esta cargada ahi, se usa el CIDR crudo
      // en vez del campo "ciudad" suelto de un equipo cualquiera (ese campo
      // se llena inconsistente y antes hacia aparecer el mismo sitio dos
      // veces con nombres distintos).
      grupos[clave] = { subred: clave, total: 0, offline: 0, sin_datos: 0, ciudad: NETWATCH_CIUDAD_POR_CIDR[clave] || null };
    }
    grupos[clave].total++;
    if (e.sin_datos) grupos[clave].sin_datos++;
    else if (!e.en_linea) grupos[clave].offline++;
  });
  return Object.values(grupos).filter(function (g) { return g.total > 0; })
    .map(function (g) { g.observados = g.total-g.sin_datos; return Object.assign(g, { pct: g.observados ? Math.round((g.offline / g.observados) * 100) : null }); })
    .sort(function (a, b) { return b.pct - a.pct || b.total - a.total; });
}

function netwatchColorGauge(pct) {
  if (pct === null) return 'gauge-sin-datos';
  if (pct >= 50) return 'gauge-mal';
  if (pct >= 20) return 'gauge-alerta';
  return 'gauge-bien';
}

let netwatchSubredActiva = null;

function netwatchFiltrarPorSubred(subred) {
  if (netwatchSubredActiva === subred) {
    // clic de nuevo sobre la misma subred activa: la saca (toggle) y limpia la busqueda
    netwatchSubredActiva = null;
    netwatchBusqueda = '';
  } else {
    netwatchSubredActiva = subred;
    netwatchBusqueda = subred === 'Sin subred' ? '' : subred;
  }
  const buscador = document.getElementById('buscador');
  if (buscador) buscador.value = netwatchBusqueda;
  netwatchRenderSubredes();
  netwatchRenderFichas();
  const grid = document.getElementById('fichas-grid');
  if (grid) grid.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

const NETWATCH_GAUGE_RADIO = 18;
const NETWATCH_GAUGE_CIRCUNFERENCIA = 2 * Math.PI * NETWATCH_GAUGE_RADIO;

function netwatchRenderSubredes() {
  const cont = document.getElementById('subredes-gauges');
  if (!cont) return;
  const grupos = netwatchAgruparPorSubred();
  if (!grupos.length) { cont.innerHTML = ''; return; }

  cont.innerHTML = grupos.map(function (g) {
    const colorClase = netwatchColorGauge(g.pct);
    const activoClase = netwatchSubredActiva === g.subred ? ' gauge-activo' : '';
    const offset = NETWATCH_GAUGE_CIRCUNFERENCIA * (1 - g.pct / 100);
    return '<button type="button" class="gauge-card' + activoClase + '" onclick="netwatchFiltrarPorSubred(\'' + g.subred.replace(/'/g, "\\'") + '\')" title="' +
      netwatchEscapeHtml(g.offline + ' de ' + g.observados + ' observados fuera de línea; ' + g.sin_datos + ' sin datos en ' + g.subred + ' (clic de nuevo para quitar el filtro)') + '">' +
      '<span class="gauge-ring-wrap">' +
        '<svg viewBox="0 0 44 44" class="gauge-ring">' +
          '<circle class="gauge-ring-bg" cx="22" cy="22" r="' + NETWATCH_GAUGE_RADIO + '"></circle>' +
          '<circle class="gauge-ring-fg ' + colorClase + '" cx="22" cy="22" r="' + NETWATCH_GAUGE_RADIO + '" ' +
            'stroke-dasharray="' + NETWATCH_GAUGE_CIRCUNFERENCIA + '" stroke-dashoffset="' + offset + '"></circle>' +
        '</svg>' +
        '<span class="gauge-ring-valor">' + (g.pct === null ? '—' : g.pct + '%') + '</span>' +
      '</span>' +
      '<span class="gauge-label">' + netwatchEscapeHtml(g.ciudad || g.subred) + '</span>' +
      '<span class="gauge-sub">' + g.offline + '/' + g.observados + ' fuera de línea</span>' +
      '<span class="gauge-caption">' + (g.sin_datos ? g.sin_datos + ' sin información reciente' : 'Observaciones recientes') + '</span>' +
      '</button>';
  }).join('');
}

// --- fichas --------------------------------------------------------------
const NETWATCH_RDP_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
  'stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="13" rx="2"></rect>' +
  '<path d="M8 21h8"></path><path d="M12 17v4"></path></svg>';

function netwatchTiempoRelativo(iso) {
  if (!iso) return null;
  const entonces = new Date(iso.replace(' ', 'T'));
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

// Umbral para escalar de "Offline" a "Posible falla" -- mismo umbral que
// eligio Andres para el aviso por WhatsApp de equipos criticos (15 min),
// asi el badge visual y el aviso real cuentan la misma historia.
const NETWATCH_UMBRAL_POSIBLE_FALLA_MIN = 15;

// Equipos manuales (notebooks/torres en casa, VPN) a veces no tienen una IP
// de red real -- en ese caso el campo "ip" en realidad guarda un identificador
// de texto libre (ej. el mismo nombre del empleado). Esto detecta ese caso
// para no mostrar el mismo texto duplicado en la ficha-card.
const NETWATCH_IPV4_RE = /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/;

function netwatchMinutosDesde(iso) {
  if (!iso) return null;
  const entonces = new Date(iso.replace(' ', 'T'));
  if (isNaN(entonces.getTime())) return null;
  return (Date.now() - entonces.getTime()) / 60000;
}

function netwatchFichaHtml(e) {
  const online = !!e.en_linea;
  const esManual = e.origen === 'manual';
  const cardClass = e.sin_datos ? 'ficha-sin-datos' : esManual ? '' : (online ? 'ficha-online' : 'ficha-offline');
  const estadoClass = e.estado_deteccion === 'descartado' ? ' ficha-descartado' : '';
  const tieneResponsable = !!e.responsable;
  const titulo = tieneResponsable ? e.responsable : (e.hostname || e.ip);
  const visto = netwatchTiempoRelativo(e.ultima_deteccion || e.desde);

  let html = '<div class="ficha-card ' + cardClass + estadoClass + '" id="ficha-card-' + e.id + '" tabindex="0" role="button" aria-label="Abrir ficha de ' + netwatchEscapeHtml(titulo) + '" onclick="netwatchAbrirFicha(' + e.id + ')" onkeydown="if(event.target===this &amp;&amp; (event.key===\'Enter\' || event.key===\' \')){event.preventDefault();netwatchAbrirFicha(' + e.id + ')}">';

  if (e.tickets_abiertos) {
    html += '<span class="ficha-ticket-banner">' + e.tickets_abiertos + ' ticket' + (e.tickets_abiertos > 1 ? 's' : '') + '</span>';
  }

  // eyebrow: subred/ciudad a la izquierda, dispositivo de red asociado a la derecha
  html += '<div class="ficha-eyebrow"><span>' + netwatchEscapeHtml(e.subred || e.ciudad || '&nbsp;') + '</span>';
  if (e.dispositivo_nombre) {
    html += '<span class="ficha-eyebrow-empresa">' + netwatchEscapeHtml(e.dispositivo_nombre) + '</span>';
  }
  html += '</div>';

  // titulo: el responsable es lo que uno recuerda, no la maquina
  const iconoEquipo = e.categoria === 'Servidor' ? 'server' : (e.categoria === 'Notebook' ? 'laptop' : 'monitor');
  html += '<div class="nw-card-identity"><span class="nw-device-icon">' + netwatchIcono(iconoEquipo) + '</span><div class="nw-card-title"><div class="ficha-card-head">';
  html += '<span class="ficha-nombre">' + netwatchEscapeHtml(titulo) + '</span>';
  html += '<span class="tag tag-' + e.estado_deteccion + '">' + netwatchEscapeHtml(e.estado_deteccion) + '</span>';
  html += '</div>';
  // linea de identificacion: nombre del equipo (si tiene) en su propia linea,
  // y la IP debajo en otra -- antes iban en la misma linea separadas por "·"
  // y un hostname largo (ej. un FQDN completo) hacia que la IP se desbordara
  // hacia el costado, rompiendo la tarjeta (ver conversacion 2026-08-21). Si
  // el "ip" guardado no es una IP de red real (equipos manuales sin IP fija,
  // como notebooks/torres en casa), se usa como nombre en vez de duplicarlo.
  const esIpReal = NETWATCH_IPV4_RE.test(e.ip || '');
  const nombreEquipo = e.hostname || (esIpReal ? null : e.ip);
  if (nombreEquipo) {
    html += '<div class="ficha-equipo-sub"><span class="ficha-mono">' + netwatchEscapeHtml(nombreEquipo) + '</span></div>';
    html += '<div class="ficha-equipo-ip">' + (esIpReal
      ? '<span class="ficha-mono">' + netwatchEscapeHtml(e.ip) + '</span>'
      : '<span class="muted">IP dinamica</span>') + '</div>';
  } else if (esIpReal) {
    html += '<div class="ficha-equipo-ip"><span class="ficha-mono">' + netwatchEscapeHtml(e.ip) + '</span></div>';
  } else {
    html += '<div class="ficha-equipo-ip"><span class="muted">Sin datos</span></div>';
  }

  html += '</div></div><div class="ficha-divider"></div>';

  if (e.ubicacion) {
    html += '<div class="ficha-linea"><span class="ficha-k">' + netwatchIcono('location') + 'Ubicación</span><span class="ficha-v">' + netwatchEscapeHtml(e.ubicacion) + '</span></div>';
  }

  html += '<div class="ficha-status-row">';
  if (e.sin_datos) {
    html += '<span class="status-pill status-pill-sin-datos">Sin información reciente</span>' +
      '<span class="ficha-ultimo-estado">Último estado conocido: ' + (online ? 'en línea' : 'fuera de línea') + '</span>';
  } else if (esManual) {
    html += '<span class="status-pill status-pill-manual"><span class="status-dot"></span>Manual</span>';
  } else {
    html += '<span class="status-pill ' + (online ? 'status-pill-online' : 'status-pill-offline') + '">' +
      '<span class="status-dot"></span>' + (online ? 'En línea' : 'Fuera de línea') + '</span>';
    if (!online) {
      const minutosOffline = netwatchMinutosDesde(e.desde);
      if (minutosOffline !== null && minutosOffline >= NETWATCH_UMBRAL_POSIBLE_FALLA_MIN) {
        html += '<span class="status-pill status-pill-falla" title="Lleva offline hace rato, puede que no sea solo un corte">Posible falla</span>';
      }
    }
  }
  if (e.critico) {
    html += '<span class="status-pill status-pill-critico" title="Marcado como critico">Critico</span>';
  }
  if (e.ip_temporal) {
    html += '<span class="status-pill status-pill-temporal" title="Equipo bueno usando temporalmente la IP de otro que fallo -- sin alertas de WhatsApp mientras dure">IP temporal</span>';
  }
  if (e.has_rdp) {
    html += '<span class="status-pill status-pill-rdp">RDP habilitado</span>';
  }
  html += '</div>';

  html += '<div class="ficha-card-foot" onclick="event.stopPropagation()">';
  html += '<span class="ficha-visto">' + netwatchIcono('clock') + (visto ? 'Visto ' + visto : 'Sin detección registrada') + '</span>';
  html += '<div class="ficha-foot-acciones">';
  if (e.estado_deteccion === 'pendiente') {
    html += '<form onsubmit="return netwatchActualizarEstado(event, ' + e.id + ')">' +
      '<button type="submit" name="action" value="confirmado" class="btn btn-confirm btn-sm">Confirmar</button>' +
      '<button type="submit" name="action" value="descartado" class="btn btn-ghost btn-sm">Descartar</button>' +
      '</form>';
  } else if (e.estado_deteccion === 'descartado') {
    html += '<form onsubmit="return netwatchActualizarEstado(event, ' + e.id + ')">' +
      '<button type="submit" name="action" value="pendiente" class="btn btn-ghost btn-sm">Restaurar</button>' +
      '</form>';
  }
  html += '<button type="button" class="btn btn-sm ' + (e.critico ? 'btn-critico-on' : 'btn-ghost') + '" ' +
    'onclick="netwatchToggleCritico(event, ' + e.id + ')">' + netwatchIcono('star') + (e.critico ? 'Quitar crítico' : 'Marcar crítico') + '</button>';
  if (e.has_rdp) {
    html += '<a href="/equipo/' + e.id + '/rdp-open" class="rdp-icon-btn" title="Conectar por RDP">' + NETWATCH_RDP_ICON + '</a>';
  }
  html += '</div>';
  html += '</div>';
  html += '</div>';
  return html;
}

// Editar ya no navega a la ficha completa -- abre el panel lateral encima
// de la misma pantalla, asi al guardar o cancelar uno se queda exactamente
// donde estaba (mismo scroll, mismo filtro, mismo buscador).
function netwatchAbrirFicha(id) {
  netwatchAbrirPanelEdicion(id);
}

let netwatchFocoAntesDelDock = null;

async function netwatchAbrirPanelEdicion(id, queryExtra) {
  const overlay = document.getElementById('panel-editar-overlay');
  const contenido = document.getElementById('panel-editar-contenido');
  if (!overlay.classList.contains('panel-open')) netwatchFocoAntesDelDock = document.activeElement;
  overlay.classList.add('panel-open');
  document.body.classList.add('nw-dock-open');
  document.getElementById('panel-editar').scrollTop = 0;
  document.getElementById('panel-editar').focus({ preventScroll: true });
  contenido.innerHTML = '<p class="empty small">Cargando...</p>';
  try {
    const res = await fetch('/equipo/' + id + '/panel' + (queryExtra || ''), {
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    });
    if (!res.ok) throw new Error('No se pudo cargar la ficha');
    contenido.innerHTML = await res.text();
    netwatchInicializarPanelForm(id);
  } catch (err) {
    contenido.innerHTML = '<p class="empty small">No se pudo cargar la ficha del equipo.</p>';
  }
}

function netwatchCerrarPanelEdicion() {
  document.getElementById('panel-editar-overlay').classList.remove('panel-open');
  document.body.classList.remove('nw-dock-open');
  if (netwatchFocoAntesDelDock && netwatchFocoAntesDelDock.isConnected) netwatchFocoAntesDelDock.focus({ preventScroll: true });
}

document.addEventListener('keydown', function (ev) {
  const overlay = document.getElementById('panel-editar-overlay');
  if (!overlay.classList.contains('panel-open')) return;
  if (ev.key === 'Escape') { ev.preventDefault(); netwatchCerrarPanelEdicion(); return; }
  if (ev.key !== 'Tab') return;
  const controles = Array.from(overlay.querySelectorAll('a[href], button:not(:disabled), input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]'))
    .filter(function (el) { return el.getClientRects().length > 0; });
  if (!controles.length) { ev.preventDefault(); return; }
  const primero = controles[0], ultimo = controles[controles.length - 1];
  if (ev.shiftKey && (document.activeElement === primero || document.activeElement.id === 'panel-editar')) {
    ev.preventDefault(); ultimo.focus();
  } else if (!ev.shiftKey && (document.activeElement === ultimo || document.activeElement.id === 'panel-editar')) {
    ev.preventDefault(); primero.focus();
  }
});

function netwatchCerrarPanelEdicionClickFuera(ev) {
  if (ev.target && ev.target.id === 'panel-editar-overlay') netwatchCerrarPanelEdicion();
}

function netwatchInicializarPanelForm(id) {
  document.querySelectorAll('.panel-coincidencia-form').forEach(function(f){f.addEventListener('submit',netwatchResolverCoincidencia);});
  const form = document.getElementById('form-panel-editar');
  if (form) form.addEventListener('submit', function (ev) { netwatchGuardarPanelEdicion(ev, id); });
  const formFusion = form ? form.parentElement.querySelector('[id^="form-fusion-"]') : null;
  if (formFusion) formFusion.addEventListener('submit', function (ev) { netwatchGuardarFusionPanel(ev, id); });

  // Tickets de soporte dentro del panel: crear ticket nuevo y cambiar el
  // estado de uno existente tampoco deben navegar a Ficha completa -- se
  // intercepta igual que el formulario principal de arriba.
  const formTicket = document.getElementById('form-panel-crear-ticket');
  if (formTicket) formTicket.addEventListener('submit', function (ev) { netwatchGuardarPanelAjax(ev, id); });
  document.querySelectorAll('.panel-ticket-acciones').forEach(function (formEstado) {
    formEstado.addEventListener('submit', function (ev) { netwatchGuardarPanelAjax(ev, id); });
  });
}

// FormData(form) NO incluye el name/value del boton que disparo el submit
// -- el estandar solo lo agrega si se pasa ese boton como "submitter"
// explicito (segundo argumento de FormData, o construyendolo a mano como
// aca). Sin esto, los botones "Abierto/En progreso/Resolver" de un ticket
// (<button name="estado" value="...">, ver templates/_panel_editar_equipo.html)
// mandaban el POST sin el campo 'estado' y el servidor respondia 400
// BadRequestKeyError -- confirmado en produccion el 2026-09-02 al apretar
// "Resolver". ev.submitter (parte de SubmitEvent) es el boton que disparo
// este submit en particular.
function netwatchFormData(form, ev) {
  const data = new FormData(form);
  if (ev.submitter && ev.submitter.name) {
    data.append(ev.submitter.name, ev.submitter.value);
  }
  return data;
}

// Generico para cualquier formulario secundario del panel (crear ticket,
// cambiar estado de un ticket) que solo necesita refrescar el fragmento del
// panel con el resultado, sin cerrar el drawer como si fuera "Guardar
// cambios" del equipo.
async function netwatchGuardarPanelAjax(ev, id) {
  ev.preventDefault();
  const form = ev.target;
  const contenido = document.getElementById('panel-editar-contenido');
  try {
    const res = await fetch(form.action, {
      method: 'POST',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      body: netwatchFormData(form, ev),
    });
    contenido.innerHTML = await res.text();
    netwatchInicializarPanelForm(id);
    netwatchPoll(); // refresca el badge de tickets abiertos en la tarjeta
  } catch (err) {
    // red caida momentaneamente: se deja el panel tal cual para reintentar
  }
  return false;
}

async function netwatchGuardarPanelEdicion(ev, id) {
  ev.preventDefault();
  const form = ev.target;
  if (form.dataset.guardando === '1') return false;
  const contenido = document.getElementById('panel-editar-contenido');
  const boton = ev.submitter;
  const textoBoton = boton ? boton.textContent : '';
  const mensaje = document.getElementById('panel-guardar-error');
  if (mensaje) mensaje.hidden = true;
  const datos = netwatchFormData(form, ev);
  form.dataset.guardando = '1';
  if (boton) { boton.disabled = true; boton.textContent = 'Guardando…'; }
  try {
    const res = await fetch(form.action, {
      method: 'POST',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      body: datos,
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const guardado = res.headers.get('X-Netwatch-Guardado') === '1';
    if (guardado) {
      netwatchCerrarPanelEdicion();
      netwatchPoll(); // refresca la tarjeta con los datos nuevos sin recargar la pagina
    } else {
      contenido.innerHTML = await res.text();
      netwatchInicializarPanelForm(id);
    }
  } catch (err) {
    if (mensaje) {
      mensaje.textContent = 'No se pudo completar el guardado. Revisa la conexión e inténtalo de nuevo; tus cambios siguen en el formulario.';
      mensaje.hidden = false;
      mensaje.scrollIntoView({ block: 'nearest' });
    }
  } finally {
    delete form.dataset.guardando;
    if (boton) { boton.disabled = false; boton.textContent = textoBoton; }
  }
  return false;
}

async function netwatchGuardarFusionPanel(ev, id) {
  // El onsubmit del formulario (definido en la macro fusion_equipo) ya
  // muestra el confirm() -- si el usuario cancelo, el evento ya viene con
  // defaultPrevented y no hay que hacer nada mas aca.
  if (ev.defaultPrevented) return;
  ev.preventDefault();
  const form = ev.target;
  const contenido = document.getElementById('panel-editar-contenido');
  try {
    const res = await fetch(form.action, {
      method: 'POST',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      body: netwatchFormData(form, ev),
    });
    if (res.headers.get('X-Netwatch-Guardado') === '1') {
      netwatchCerrarPanelEdicion();
      netwatchPoll();
    } else {
      contenido.innerHTML = await res.text();
      netwatchInicializarPanelForm(id);
    }
  } catch (err) {
    // red caida momentaneamente: se reintenta manualmente
  }
  return false;
}

// Ocultar Hardware/Software cuando la categoria elegida es un dispositivo de
// red (switch, impresora, camara, etc.) -- esos campos no aplican y solo
// desordenaban el formulario. Ver comentario en _macros_equipo.html sobre por
// que la lista viaja separada por comas y no como tojson.
function netwatchAplicarCategoriaEquipo(sel) {
  const red = (sel.dataset.categoriasRed || '').split(',').filter(Boolean);
  const esRed = red.indexOf(sel.value) !== -1;
  const form = sel.closest('.ficha-form');
  if (!form) return;
  form.querySelectorAll('.fieldset-solo-pc').forEach(function (fs) { fs.style.display = esRed ? 'none' : ''; });
}

// Imagen del equipo dentro del panel (mismo patron que en ficha.html/admin_equipos.html).
function netwatchFotoEquipoSeleccionada(input, id) {
  netwatchPrepararFotoEquipo(input, id);
}

function netwatchEliminarFotoEquipo(id) {
  const img = document.getElementById('foto-equipo-img-' + id);
  img.style.display = 'none';
  img.removeAttribute('src');
  const placeholder = document.getElementById('foto-equipo-placeholder-' + id);
  if (placeholder) placeholder.style.display = 'flex';
  document.getElementById('foto-equipo-archivo-' + id).value = '';
  document.getElementById('foto-equipo-eliminar-' + id).value = '1';
  const estado = document.getElementById('foto-equipo-estado-' + id);
  if (estado) {
    estado.textContent = 'La imagen se eliminará al guardar los cambios.';
    estado.dataset.error = 'false';
  }
}

// Desde "Ultimos cambios"/toast: no editar, solo ubicar la ficha en la
// grilla (limpia filtro/busqueda si estaban tapando la tarjeta) y resaltarla.
function netwatchResaltarFicha(id) {
  netwatchFiltroTipo = null;
  netwatchFiltroValor = null;
  netwatchBusqueda = '';
  netwatchSubredActiva = null;
  const buscador = document.getElementById('buscador');
  if (buscador) buscador.value = '';
  netwatchRenderResumen();
  netwatchRenderSubredes();
  netwatchRenderFichas();
  requestAnimationFrame(function () {
    const card = document.getElementById('ficha-card-' + id);
    if (!card) return;
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    card.classList.add('ficha-resaltada');
    setTimeout(function () { card.classList.remove('ficha-resaltada'); }, 2200);
  });
}

// Confirmar/Descartar/Restaurar sin recargar la pagina, para no perder el
// scroll donde estabas mirando (antes era un <form> normal que navegaba).
const NETWATCH_CONTADOR_POR_ESTADO = { pendiente: 'pendientes', confirmado: 'confirmados', descartado: 'descartados' };

async function netwatchActualizarEstado(ev, id) {
  ev.preventDefault();
  ev.stopPropagation();
  const action = ev.submitter ? ev.submitter.value : null;
  if (!action) return false;

  const equipo = NETWATCH_EQUIPOS.find(function (e) { return e.id === id; });
  const anterior = equipo ? equipo.estado_deteccion : null;

  try {
    await fetch('/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'XMLHttpRequest' },
      body: 'id=' + encodeURIComponent(id) + '&action=' + encodeURIComponent(action),
    });
  } catch (err) {
    // red caida momentaneamente: el proximo poll (7s) va a corregir el estado igual
  }

  if (equipo) equipo.estado_deteccion = action;
  if (action === 'descartado') {
    NETWATCH_OCULTOS_TRAS_DESCARTE.add(id);
  } else {
    NETWATCH_OCULTOS_TRAS_DESCARTE.delete(id);
  }
  if (anterior && NETWATCH_CONTADOR_POR_ESTADO[anterior]) {
    NETWATCH_SUMMARY[NETWATCH_CONTADOR_POR_ESTADO[anterior]] = Math.max(0, (NETWATCH_SUMMARY[NETWATCH_CONTADOR_POR_ESTADO[anterior]] || 0) - 1);
  }
  if (NETWATCH_CONTADOR_POR_ESTADO[action]) {
    NETWATCH_SUMMARY[NETWATCH_CONTADOR_POR_ESTADO[action]] = (NETWATCH_SUMMARY[NETWATCH_CONTADOR_POR_ESTADO[action]] || 0) + 1;
  }
  netwatchRenderResumen();
  netwatchRenderFichas();
  return false;
}

// Marcar/quitar critico desde la tarjeta, sin abrir la ficha completa ni
// recargar la pagina (mismo patron que Confirmar/Descartar arriba).
async function netwatchToggleCritico(ev, id) {
  ev.preventDefault();
  ev.stopPropagation();

  const boton = ev.currentTarget;
  if (boton && boton.disabled) return; // ya hay un cambio en curso -- ignora clicks de mas

  const equipo = NETWATCH_EQUIPOS.find(function (e) { return e.id === id; });
  if (!equipo) return;
  const nuevoValor = equipo.critico ? 0 : 1;
  if (boton) boton.disabled = true;

  try {
    // Se manda el valor final explicito (no un "toggle" ciego): si el click
    // se llega a disparar dos veces, ambas peticiones piden lo mismo en vez
    // de cancelarse entre si y dejar el estado real distinto al que se ve
    // en pantalla hasta el proximo poll.
    await fetch('/toggle-critico', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'XMLHttpRequest' },
      body: 'id=' + encodeURIComponent(id) + '&critico=' + nuevoValor,
    });
    equipo.critico = nuevoValor;
  } catch (err) {
    // red caida momentaneamente: el proximo poll (7s) va a corregir el estado igual
  }

  netwatchRenderResumen();
  netwatchRenderFichas();
}

function netwatchRenderFichas() {
  const grid = document.getElementById('fichas-grid');
  const vacio = document.getElementById('fichas-vacio');
  const lista = netwatchAplicarFiltros();

  if (!NETWATCH_EQUIPOS.length) {
    grid.innerHTML = '';
    vacio.style.display = 'block';
    vacio.innerHTML = 'Todavia no hay equipos importados. Corre <code>python monitor.py</code> (recomendado, queda corriendo solo) o <code>python scanner.py</code> + boton "Importar" arriba.';
    return;
  }
  if (!lista.length) {
    grid.innerHTML = '';
    vacio.style.display = 'block';
    vacio.textContent = 'Ningun equipo coincide con el filtro o la busqueda actual.';
    return;
  }
  vacio.style.display = 'none';
  grid.innerHTML = lista.map(netwatchFichaHtml).join('');
}

// --- buscador ------------------------------------------------------------
document.getElementById('buscador').addEventListener('input', function (ev) {
  netwatchBusqueda = ev.target.value;
  if (netwatchSubredActiva !== null) {
    netwatchSubredActiva = null;
    netwatchRenderSubredes();
  }
  netwatchRenderFichas();
});

// --- sonido ----------------------------------------------------------------
let netwatchAudioCtx = null;
function netwatchToggleSonido() {
  netwatchSonidoActivo = !netwatchSonidoActivo;
  localStorage.setItem('netwatch_sonido', netwatchSonidoActivo ? 'on' : 'off');
  netwatchRenderBotonSonido();
}
function netwatchRenderBotonSonido() {
  const btn = document.getElementById('btn-sonido');
  btn.textContent = 'Sonido: ' + (netwatchSonidoActivo ? 'ON' : 'OFF');
  btn.classList.toggle('muted', !netwatchSonidoActivo);
}
function netwatchReproducirSonido(tipo) {
  if (!netwatchSonidoActivo) return;
  try {
    if (!netwatchAudioCtx) netwatchAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const ctx = netwatchAudioCtx;
    const freqs = tipo === 'online' ? [660, 880] : tipo === 'offline' ? [440, 300] : [520, 520];
    // Ping mas fuerte y mas largo que antes (subido de volumen 0.16 -> 0.35 y
    // de 0.4s a 0.55s) -- quedaba muy discreto y era facil no notarlo. Para
    // "offline" ademas se repite dos veces (como una alarma corta), ya que es
    // el aviso mas importante de no perderse.
    const pulsos = tipo === 'offline' ? 2 : 1;
    for (let i = 0; i < pulsos; i++) {
      const inicio = ctx.currentTime + i * 0.32;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain); gain.connect(ctx.destination);
      osc.type = 'sine';
      gain.gain.setValueAtTime(0.0001, inicio);
      gain.gain.exponentialRampToValueAtTime(0.35, inicio + 0.02);
      osc.frequency.setValueAtTime(freqs[0], inicio);
      osc.frequency.setValueAtTime(freqs[1], inicio + 0.15);
      gain.gain.exponentialRampToValueAtTime(0.0001, inicio + 0.5);
      osc.start(inicio);
      osc.stop(inicio + 0.55);
    }
  } catch (err) { /* audio no disponible en este navegador, se ignora */ }
}

// --- toasts ------------------------------------------------------------
// Se conserva la referencia al aviso critico para cerrarlo si el equipo
// vuelve online antes del cierre automatico a los 6 segundos.
let netwatchToastsCriticosActivos = {}; // equipo_id -> elemento <div> del aviso

function netwatchCerrarToast(div, equipoId) {
  div.classList.remove('toast-show');
  setTimeout(function () { div.remove(); }, 400);
  if (equipoId && netwatchToastsCriticosActivos[equipoId] === div) {
    delete netwatchToastsCriticosActivos[equipoId];
  }
}

function netwatchProbarAviso() {
  netwatchMostrarToast({ tipo: 'nuevo', prueba: true, hostname: 'Avisos de equipos activos' });
}

function netwatchMostrarToast(ev) {
  const cont = document.getElementById('toast-container');
  const tipo = ev.tipo;
  const label = ev.prueba ? 'Prueba de notificación' : tipo === 'online' ? 'Equipo conectado' : tipo === 'offline' ? 'Equipo desconectado' : 'Nuevo equipo detectado';
  const titulo = ev.responsable || ev.hostname || ev.ip;
  const equipo = ev.equipo_id ? NETWATCH_EQUIPOS.find(function (e) { return e.id === ev.equipo_id; }) : null;
  const esCriticoOffline = tipo === 'offline' && (!!(equipo && equipo.critico) || !!(ev.criticos && ev.criticos.length));

  // Si el equipo critico que estaba caido volvio online, cerramos su toast fijo
  if (tipo === 'online' && ev.equipo_id && netwatchToastsCriticosActivos[ev.equipo_id]) {
    netwatchCerrarToast(netwatchToastsCriticosActivos[ev.equipo_id], ev.equipo_id);
  }

  const div = document.createElement('div');
  div.className = 'toast toast-' + tipo + (esCriticoOffline ? ' toast-critico' : '');
  div.setAttribute('role', esCriticoOffline ? 'alert' : 'status');
  div.setAttribute('aria-atomic', 'true');
  if (ev.agrupado) {
    div.classList.add('toast-clickeable');
    div.tabIndex = 0;
    const abrirGrupo = function(){ netwatchFiltroTipo='online'; netwatchFiltroValor=tipo; netwatchSubredActiva=null; netwatchFiltrarPorSubred(ev.subred); netwatchRenderResumen(); };
    div.addEventListener('click',function(e){if(!e.target.closest('.toast-cerrar')) abrirGrupo();});
    div.addEventListener('keydown',function(e){if(e.target===div && (e.key==='Enter'||e.key===' ')){e.preventDefault();abrirGrupo();}});
  }
  if (ev.equipo_id) {
    div.classList.add('toast-clickeable');
    div.tabIndex = 0;
    div.addEventListener('click', function (evt) {
      if (evt.target.closest('.toast-cerrar')) return;
      netwatchResaltarFicha(ev.equipo_id);
    });
    div.addEventListener('keydown', function (evt) {
      if (evt.target === div && (evt.key === 'Enter' || evt.key === ' ')) {
        evt.preventDefault(); netwatchResaltarFicha(ev.equipo_id);
      }
    });
  }
  const etiquetaCritico = esCriticoOffline ? '<span class="toast-tag-critico">Crítico</span>' : '';
  const icono = esCriticoOffline ? 'alert' : tipo === 'online' ? 'pulse' : tipo === 'offline' ? 'monitor' : 'network';
  div.innerHTML = '<span class="toast-icono">' + netwatchIcono(icono) + '</span><div class="toast-texto">' +
    '<div class="toast-cabecera"><span class="toast-estado">' + label + '</span>' + etiquetaCritico + '</div>' +
    '<strong class="toast-nombre">' + netwatchEscapeHtml(titulo) + '</strong>' +
    '<span class="toast-sub">' + netwatchEscapeHtml(ev.prueba ? 'Los avisos de equipos aparecerán aquí.' : (ev.detalle || ev.ip)) + '</span></div>' +
    '<button type="button" class="toast-cerrar" title="Cerrar aviso" aria-label="Cerrar aviso">' + netwatchIcono('close') + '</button>';
  const btnCerrar = div.querySelector('.toast-cerrar');
  btnCerrar.addEventListener('click', function (evt) {
    evt.stopPropagation();
    netwatchCerrarToast(div, ev.equipo_id);
  });
  cont.appendChild(div);
  requestAnimationFrame(function () { div.classList.add('toast-show'); });

  if (esCriticoOffline && ev.equipo_id) {
    netwatchToastsCriticosActivos[ev.equipo_id] = div;
  }

  // Todos los toasts se cierran automáticamente después de 6 segundos,
  // incluyendo los críticos. Si el equipo vuelve online antes, se cierra
  // su toast critico de forma manual (ver arriba, en línea 709-710).
  setTimeout(function () { netwatchCerrarToast(div, ev.equipo_id); }, 6000);

  netwatchReproducirSonido(tipo);
}

// --- eventos recientes (panel lateral) ------------------------------------
function netwatchRenderEventos() {
  const ul = document.getElementById('eventos-lista');
  const vacio = document.getElementById('eventos-vacio');
  const items = NETWATCH_EVENTOS.slice(0, 20);
  if (!items.length) {
    ul.innerHTML = '';
    vacio.style.display = 'block';
    return;
  }
  vacio.style.display = 'none';
  ul.innerHTML = items.map(function (ev) {
    const label = ev.tipo === 'online' ? 'Volvio online' : ev.tipo === 'offline' ? 'Paso a offline' : 'Nuevo equipo';
    const titulo = ev.responsable || ev.hostname || ev.ip;
    const clickeable = !!ev.equipo_id;
    const onclick = clickeable ? ' onclick="netwatchResaltarFicha(' + ev.equipo_id + ')"' : '';
    return '<li class="evento evento-' + ev.tipo + (clickeable ? ' evento-clickeable' : '') + '"' + onclick + '>' +
      '<span class="evento-tipo">' + label + '</span>' +
      '<span class="evento-detalle">' + netwatchEscapeHtml(titulo) + ' (' + netwatchEscapeHtml(ev.ip) + ')</span>' +
      '<span class="evento-ts">' + netwatchEscapeHtml((ev.ts || '').slice(0, 16)) + '</span>' +
      '</li>';
  }).join('');
}

// --- polling en vivo -------------------------------------------------------
async function netwatchPoll() {
  try {
    const res = await fetch('/api/estado?despues=' + encodeURIComponent(NETWATCH_ULTIMO_EVENTO_ID), { cache: 'no-store' });
    if (!res.ok) return;
    const data = await res.json();
    NETWATCH_EQUIPOS = data.equipos;
    NETWATCH_SUMMARY = data.summary;

    const nuevos = (data.eventos || []).filter(function (e) { return e.id > NETWATCH_ULTIMO_EVENTO_ID; })
      .sort(function (a, b) { return a.id - b.id; });
    (data.notificaciones || nuevos).forEach(netwatchMostrarToast);
    if (data.eventos && data.eventos.length) {
      NETWATCH_ULTIMO_EVENTO_ID = Math.max(NETWATCH_ULTIMO_EVENTO_ID, Math.max.apply(null, data.eventos.map(function (e) { return e.id; })));
    }
    NETWATCH_EVENTOS = nuevos.concat(NETWATCH_EVENTOS).sort(function(a,b){return b.id-a.id;}).slice(0,30);
    NETWATCH_AGENTES = data.agentes || [];
    netwatchRenderAgentes();
    netwatchRenderCoincidencias(data.coincidencias || []);

    netwatchRenderResumen();
    netwatchRenderFichas();
    netwatchRenderEventos();
    netwatchRenderSubredes();
  } catch (err) {
    // red caida momentaneamente o servidor reiniciando: se reintenta en el siguiente ciclo
  }
}

// --- consola del monitor embebida (en vez de la ventana negra aparte) -----
let netwatchConsolaColapsada = localStorage.getItem('netwatch_consola_colapsada') === '1';

function netwatchToggleConsola() {
  netwatchConsolaColapsada = !netwatchConsolaColapsada;
  localStorage.setItem('netwatch_consola_colapsada', netwatchConsolaColapsada ? '1' : '0');
  netwatchAplicarColapsoConsola();
}

function netwatchAplicarColapsoConsola() {
  const body = document.getElementById('consola-body');
  const icono = document.getElementById('consola-toggle-icono');
  if (body) body.style.display = netwatchConsolaColapsada ? 'none' : 'block';
  if (icono) icono.innerHTML = netwatchConsolaColapsada ? '&#9656;' : '&#9662;';
}

async function netwatchPollConsola() {
  try {
    const res = await fetch('/api/monitor_log', { cache: 'no-store' });
    if (!res.ok) return;
    const data = await res.json();

    const dot = document.getElementById('consola-dot');
    if (dot) dot.className = 'consola-dot ' + (data.activo ? 'consola-dot-on' : 'consola-dot-off');

    const body = document.getElementById('consola-body');
    if (body) {
      const yaEstabaAbajo = body.scrollTop + body.clientHeight >= body.scrollHeight - 20;
      body.textContent = data.lineas && data.lineas.length ? data.lineas.join('\n') :
        (data.existe ? '' : 'Todavia no hay log del monitor. Deja monitor.py corriendo para verlo aca.');
      if (yaEstabaAbajo) body.scrollTop = body.scrollHeight;
    }

    // La salud de los agentes llega estructurada en /api/estado; el log
    // queda reservado para la consola, sin decidir colores ni estados.
  } catch (err) {
    // red caida momentaneamente: se reintenta en el proximo ciclo
  }
}

// Salud de cada colector calculada por antigüedad del reporte y su intervalo.
function netwatchRenderAgentes() {
  const cont = document.getElementById('firebase-status');
  if (!cont) return;
  cont.innerHTML = NETWATCH_AGENTES.map(function (a) {
    const clase = a.estado === 'activo' ? 'firebase-chip-ok' : a.estado === 'retrasado' ? 'firebase-chip-warn' : 'firebase-chip-off';
    const texto = (a.estado === 'activo' ? 'Activo' : a.estado === 'retrasado' ? 'Retrasado' : 'Sin información') +
      ' · ' + (a.ultimo_reporte ? netwatchTiempoRelativo(a.ultimo_reporte) : 'sin reportes');
    return '<button type="button" class="firebase-chip firebase-chip-clickeable ' + clase + '" data-subred="' + netwatchEscapeHtml(a.cidr) +
      '" title="' + netwatchEscapeHtml((a.ciudad || a.nombre) + ': ' + texto + (a.error ? ' · ' + a.error : '')) + '">' +
      '<span class="firebase-chip-dot"></span><span class="firebase-chip-nombre">' + netwatchEscapeHtml(a.nombre) +
      '</span><span class="firebase-chip-texto">' + netwatchEscapeHtml(texto) + '</span></button>';
  }).join('');
  cont.querySelectorAll('[data-subred]').forEach(function(btn){btn.addEventListener('click',function(){netwatchFiltrarPorSubred(btn.dataset.subred);});});
}

function netwatchRenderCoincidencias(lista) {
  const cont = document.getElementById('coincidencias-equipo');
  cont.hidden = !lista.length;
  cont.innerHTML = '<details><summary>' + lista.length + ' posibles cambios de IP para revisar</summary>' + lista.map(function(c){
    return '<div class="nw-coincidencia"><span>' + netwatchEscapeHtml(c.responsable || c.nombre_anterior || c.ip_anterior) + ': ' +
      netwatchEscapeHtml(c.ip_anterior) + ' → ' + netwatchEscapeHtml(c.ip_nueva) + ' · ' + netwatchEscapeHtml(c.motivos.join(', ')) +
      '</span><button type="button" class="btn btn-ghost" data-equipo="' + c.nuevo_id + '">Revisar coincidencia</button></div>';
  }).join('') + '</details>';
  cont.querySelectorAll('[data-equipo]').forEach(function(btn){btn.addEventListener('click',function(){netwatchAbrirFicha(Number(btn.dataset.equipo));});});
}

async function netwatchResolverCoincidencia(ev) {
  ev.preventDefault();
  const form = ev.target;
  try {
    const res = await fetch(form.action,{method:'POST',headers:{'X-Requested-With':'XMLHttpRequest'},body:netwatchFormData(form,ev)});
    if (!res.ok) throw new Error('No se pudo resolver la coincidencia');
    netwatchCerrarPanelEdicion();
    await netwatchPoll();
  } catch(err) {
    const aviso = document.getElementById('panel-guardar-error');
    if (aviso) { aviso.hidden=false; aviso.textContent='No se pudo completar la operación. La coincidencia puede haber cambiado; vuelve a abrir la ficha.'; }
  }
}

netwatchAplicarColapsoConsola();
netwatchPollConsola();
setInterval(netwatchPollConsola, 5000);

netwatchRenderBotonSonido();
netwatchRenderResumen();
netwatchRenderFichas();
netwatchRenderEventos();
netwatchRenderSubredes();
netwatchPoll();
setInterval(netwatchPoll, 7000);
