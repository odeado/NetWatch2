// Autoformato de campos de equipo (MAC y N. de serie), compartido entre
// ficha.html, admin_equipos.html e index.html (panel lateral) -- pedido de
// Andres: en MAC quiere ver siempre "AA:BB:CC:DD:EE:FF" (mayuscula, dos
// puntos cada 2 digitos) sin tener que escribirlo asi el mismo, y el
// numero de serie siempre en mayuscula para no terminar con la misma serie
// cargada de dos formas distintas (ej. "abc123" vs "ABC123") entre equipos.
//
// La MAC se formatea EN VIVO mientras se escribe (evento "input"), cuidando
// la posicion del cursor para que escribir/pegar/borrar en medio del campo
// no lo mande al final. Se deja tambien en "blur" por si acaso. El N. de
// serie sigue formateandose al salir del campo.

function netwatchFormatearMac(input, evento) {
  const valor = input.value;
  const esHex = /[^0-9A-F]/g;
  const cursor = input.selectionStart == null ? valor.length : input.selectionStart;
  let hexAntesCursor = valor.slice(0, cursor).toUpperCase().replace(esHex, "").length;
  let hex = valor.toUpperCase().replace(esHex, "");

  // Borrar un ":" (Backspace/Delete justo al lado) no cambia los digitos, y
  // el reformateo lo volveria a poner -- el cursor quedaria atascado. En ese
  // caso se borra tambien el digito vecino, que es lo que el usuario espera.
  const previo = (input.dataset.macPrevio || "").replace(esHex, "");
  if (evento && hex === previo) {
    if (evento.inputType === "deleteContentBackward" && hexAntesCursor > 0) {
      hex = hex.slice(0, hexAntesCursor - 1) + hex.slice(hexAntesCursor);
      hexAntesCursor--;
    } else if (evento.inputType === "deleteContentForward" && hexAntesCursor < hex.length) {
      hex = hex.slice(0, hexAntesCursor) + hex.slice(hexAntesCursor + 1);
    }
  }

  hex = hex.slice(0, 12);
  if (hexAntesCursor > hex.length) hexAntesCursor = hex.length;
  const grupos = hex.match(/.{1,2}/g);
  const formateado = grupos ? grupos.join(":") : hex;
  input.value = formateado;
  input.dataset.macPrevio = formateado;

  if (document.activeElement === input && input.setSelectionRange) {
    const pos = hexAntesCursor === 0 ? 0 : hexAntesCursor + Math.floor((hexAntesCursor - 1) / 2);
    input.setSelectionRange(pos, pos);
  }
}

function netwatchFormatearSerie(input) {
  input.value = input.value.toUpperCase();
}

// Compartido por la ficha completa, el acordeon y el dock. El archivo viaja
// en FormData; la vista previa no significa que el servidor ya lo guardo.
function netwatchPrepararFotoEquipo(input, id) {
  const archivo = input.files && input.files[0];
  if (!archivo) return;
  const estado = document.getElementById('foto-equipo-estado-' + id);
  input.setCustomValidity('');
  if (!/\.(jpe?g|jfif|png|gif|webp|bmp|avif)$/i.test(archivo.name) || !archivo.size) {
    input.value = '';
    if (estado) {
      estado.textContent = 'No se seleccionó una imagen compatible. Usa JPG, PNG, GIF, WebP, BMP o AVIF.';
      estado.dataset.error = 'true';
    }
    return;
  }
  // Se actualiza antes de leer: guardar rapido despues de cambiar una foto
  // eliminada no debe mandar todavia eliminar_foto=1.
  document.getElementById('foto-equipo-eliminar-' + id).value = '0';
  const reader = new FileReader();
  reader.onload = function (ev) {
    const img = document.getElementById('foto-equipo-img-' + id);
    if (!img || input.files[0] !== archivo) return;
    img.src = ev.target.result;
    img.style.display = 'block';
    const placeholder = document.getElementById('foto-equipo-placeholder-' + id);
    if (placeholder) placeholder.style.display = 'none';
    if (estado) {
      estado.textContent = 'Imagen seleccionada. Pulsa Guardar cambios para conservarla.';
      estado.dataset.error = 'false';
    }
  };
  reader.onerror = function () {
    input.value = '';
    if (estado) {
      estado.textContent = 'No se pudo leer el archivo. Vuelve a seleccionarlo.';
      estado.dataset.error = 'true';
    }
  };
  reader.readAsDataURL(archivo);
}

function netwatchFotoEquipoError(img, id) {
  if (!img.getAttribute('src')) return;
  img.style.display = 'none';
  const placeholder = document.getElementById('foto-equipo-placeholder-' + id);
  if (placeholder) { placeholder.style.display = 'flex'; placeholder.textContent = 'Imagen no disponible'; }
  const estado = document.getElementById('foto-equipo-estado-' + id);
  if (estado) {
    estado.textContent = 'No se pudo cargar la imagen. Intenta cambiarla y guardar de nuevo.';
    estado.dataset.error = 'true';
  }
  const input = document.getElementById('foto-equipo-archivo-' + id);
  if (input && input.files.length) {
    input.value = '';
    if (estado) estado.textContent = 'El archivo no se pudo abrir como imagen. Selecciona otra imagen antes de guardar.';
  }
}
