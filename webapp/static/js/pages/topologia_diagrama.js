// static/js/pages/topologia_diagrama.js
// JS del diagrama de puertos de Topologia -- extraido de
// templates/topologia_diagrama.html. tipo_labels ahora viaja como
// <script type="application/json"> igual que el resto de los datos del
// servidor en las demas paginas, en vez de un const con Jinja embebido.

const NETWATCH_TIPO_LABELS = JSON.parse(document.getElementById('tipo-labels-data').textContent);

function netwatchEscapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str).replace(/[&<>"']/g, function (c) {
    return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
  });
}

function netwatchCerrarDetalle() {
  document.getElementById('detalle-panel').classList.remove('activo');
  document.querySelectorAll('.nodo-caja.nodo-seleccionado').forEach(function (r) { r.classList.remove('nodo-seleccionado'); });
}

function netwatchSeleccionarNodo(el) {
  const n = JSON.parse(el.dataset.info);

  document.querySelectorAll('.nodo-caja.nodo-seleccionado').forEach(function (r) { r.classList.remove('nodo-seleccionado'); });
  el.querySelector('.nodo-caja').classList.add('nodo-seleccionado');

  let html = '<div class="detalle-header">';
  html += '<div class="detalle-badge" style="background:' + n.color + '">' + netwatchEscapeHtml(n.sigla) + '</div>';
  html += '<div><div class="detalle-nombre">' + netwatchEscapeHtml(n.nombre) + '</div>';
  html += '<div class="detalle-tipo">' + netwatchEscapeHtml(NETWATCH_TIPO_LABELS[n.tipo] || n.tipo || '') + '</div></div></div>';

  html += '<div class="detalle-kv">';
  html += '<span class="k">IP</span><span class="v">' + netwatchEscapeHtml(n.ip || '-') + '</span>';
  html += '<span class="k">MAC</span><span class="v">' + netwatchEscapeHtml(n.mac || '-') + '</span>';
  html += '<span class="k">N. Serie</span><span class="v">' + netwatchEscapeHtml(n.numero_serie || '-') + '</span>';
  html += '<span class="k">Estado</span><span class="v">' + netwatchEscapeHtml(n.estado || '-') + '</span>';
  const ubicacion = [n.sucursal, n.piso].filter(Boolean).join(' - ') || n.ciudad || '-';
  html += '<span class="k">Ubicacion</span><span class="v">' + netwatchEscapeHtml(ubicacion) + '</span>';
  if (n.notas) {
    html += '<span class="k">Notas</span><span class="v">' + netwatchEscapeHtml(n.notas) + '</span>';
  }
  html += '</div>';

  html += '<div class="detalle-seccion-titulo">Enlaces de red (' + (n.enlaces_detalle ? n.enlaces_detalle.length : 0) + ')</div>';
  if (n.enlaces_detalle && n.enlaces_detalle.length) {
    n.enlaces_detalle.forEach(function (enl) {
      html += '<div class="detalle-enlace">';
      html += '<span class="detalle-enlace-puerto">' + netwatchEscapeHtml(enl.puerto_local) + '</span>';
      html += '<span>' + netwatchEscapeHtml(enl.otro_nombre) + (enl.otro_ip ? ' &middot; ' + netwatchEscapeHtml(enl.otro_ip) : '') + '</span>';
      html += '</div>';
    });
  } else {
    html += '<p class="detalle-empty">Sin enlaces a otros dispositivos de red.</p>';
  }

  html += '<div class="detalle-seccion-titulo">Equipos conectados (' + (n.equipos_detalle ? n.equipos_detalle.length : 0) + ')</div>';
  if (n.equipos_detalle && n.equipos_detalle.length) {
    n.equipos_detalle.forEach(function (eq) {
      html += '<div class="detalle-equipo">';
      html += '<span class="detalle-dot" style="background:' + (eq.en_linea ? '#34d399' : '#f87171') + '"></span>';
      html += '<span>' + netwatchEscapeHtml(eq.hostname) + ' &middot; ' + netwatchEscapeHtml(eq.ip) +
              (eq.responsable ? ' &middot; ' + netwatchEscapeHtml(eq.responsable) : '') + '</span>';
      html += '</div>';
    });
  } else {
    html += '<p class="detalle-empty">No hay equipos terminales (PCs, impresoras) conectados a este dispositivo.</p>';
  }

  document.getElementById('detalle-contenido').innerHTML = html;
  document.getElementById('detalle-panel').classList.add('activo');
}

function netwatchToggleEquipos() {
  const svg = document.getElementById('svg-diagrama');
  const btn = document.getElementById('btn-equipos');
  if (!svg) return;
  const mostrando = svg.classList.toggle('mostrar-equipos');
  btn.classList.toggle('activo', mostrando);
  btn.textContent = mostrando ? '- Ocultar equipos conectados' : '+ Ver equipos conectados (PC/impresora)';
}
