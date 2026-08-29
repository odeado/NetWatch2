// static/js/pages/ficha.js
// JS de la Ficha de equipo -- extraido de templates/ficha.html tal cual,
// sin Jinja adentro.

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
