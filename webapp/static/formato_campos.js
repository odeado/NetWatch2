// Autoformato de campos de equipo (MAC y N. de serie), compartido entre
// ficha.html, admin_equipos.html e index.html (panel lateral) -- pedido de
// Andres: en MAC quiere ver siempre "AA:BB:CC:DD:EE:FF" (mayuscula, dos
// puntos cada 2 digitos) sin tener que escribirlo asi el mismo, y el
// numero de serie siempre en mayuscula para no terminar con la misma serie
// cargada de dos formas distintas (ej. "abc123" vs "ABC123") entre equipos.
//
// Se aplica en "blur" (al salir del campo), no en cada tecla: reformatear
// mientras se escribe manda el cursor al final despues de cada letra, mas
// molesto que el problema que se esta arreglando.

function netwatchFormatearMac(input) {
  const hex = input.value.toUpperCase().replace(/[^0-9A-F]/g, "").slice(0, 12);
  const grupos = hex.match(/.{1,2}/g);
  input.value = grupos ? grupos.join(":") : hex;
}

function netwatchFormatearSerie(input) {
  input.value = input.value.toUpperCase();
}
