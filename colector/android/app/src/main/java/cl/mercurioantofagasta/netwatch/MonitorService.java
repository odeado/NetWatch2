package cl.mercurioantofagasta.netwatch;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

import androidx.core.app.NotificationCompat;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Iterator;
import java.util.List;

/**
 * Servicio en primer plano: cada 60 s lee del colector (Firebase) el resumen y los
 * eventos nuevos, y manda una notificacion por equipo que cayo / volvio / aparecio.
 */
public class MonitorService extends Service {

    public static final String ACCION_ACTUALIZADO = "cl.mercurioantofagasta.netwatch.ACTUALIZADO";
    static final String CH_ESTADO = "nw_estado";
    static final String CH_EVENTOS = "nw_eventos";
    static final String CH_CRITICOS = "nw_criticos";
    static final int ID_FG = 1;
    static final long INTERVALO_MS = 60000L;
    static final long COLECTOR_CAIDO_MS = 5 * 60000L;
    static final int MAX_NOTIF_POR_CICLO = 15;

    private final Object espera = new Object();
    private volatile boolean corriendo = false;
    private Thread hilo;
    private ColectorApi api;
    private PowerManager.WakeLock wake;

    public static void iniciar(Context c) {
        Intent i = new Intent(c, MonitorService.class);
        if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(i); else c.startService(i);
    }

    public static void detener(Context c) {
        c.stopService(new Intent(c, MonitorService.class));
    }

    @Override
    public void onCreate() {
        super.onCreate();
        api = new ColectorApi(this);
        crearCanales();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && intent.getBooleanExtra("refrescar", false)) {
            synchronized (espera) { espera.notifyAll(); }
        }
        Notification n = notifEstado("\uD83D\uDFE0 NetWatch \u00B7 conectando", "Conectando con el colector...", Textos.AMBAR);
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(ID_FG, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(ID_FG, n);
        }
        if (!corriendo) {
            corriendo = true;
            PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
            wake = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "netwatch:monitor");
            hilo = new Thread(new Runnable() {
                @Override public void run() { bucle(); }
            }, "netwatch-monitor");
            hilo.start();
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        corriendo = false;
        if (hilo != null) hilo.interrupt();
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) { return null; }

    private void bucle() {
        while (corriendo) {
            try {
                wake.acquire(60000L);
                ciclo();
            } catch (Exception e) {
                Estado.setCache(this, "error", String.valueOf(e.getMessage()));
                sendBroadcast(new Intent(ACCION_ACTUALIZADO).setPackage(getPackageName()));
                actualizarFg("\uD83D\uDD34 NetWatch \u00B7 sin conexi\u00F3n", "No se pudo leer Firebase: " + corto(e.getMessage()), Textos.ROJO);
            } finally {
                try { if (wake.isHeld()) wake.release(); } catch (Exception ignorar) { }
            }
            try { synchronized (espera) { espera.wait(INTERVALO_MS); } } catch (InterruptedException e) { return; }
        }
    }

    private void ciclo() throws Exception {
        Estado.setCache(this, "error", "");
        JSONObject estado = api.get("colector/estado", null);
        JSONObject criticos = api.get("colector/criticos", null);
        Estado.setCache(this, "estado", estado == null ? "" : estado.toString());
        Estado.setCache(this, "criticos", criticos == null ? "" : criticos.toString());

        // --- eventos nuevos ---
        String ultima = Estado.ultimaClave(this);
        List<String> claves = new ArrayList<String>();
        JSONObject evs;
        if (ultima == null) {
            // Primera vez: linea base silenciosa (no notificar el historial).
            evs = api.get("colector/eventos", "&orderBy=" + ColectorApi.enc("\"$key\"") + "&limitToLast=1");
            if (evs != null) {
                Iterator<String> it = evs.keys();
                while (it.hasNext()) claves.add(it.next());
                Collections.sort(claves);
                if (!claves.isEmpty()) Estado.setUltimaClave(this, claves.get(claves.size() - 1));
            } else {
                Estado.setUltimaClave(this, "-");
            }
        } else {
            evs = api.get("colector/eventos", "&orderBy=" + ColectorApi.enc("\"$key\"")
                    + "&startAt=" + ColectorApi.enc("\"" + ultima + "\""));
            if (evs != null) {
                Iterator<String> it = evs.keys();
                while (it.hasNext()) {
                    String k = it.next();
                    if (k.compareTo(ultima) > 0) claves.add(k);
                }
                Collections.sort(claves);
            }
            if (!claves.isEmpty()) {
                notificar(evs, claves);
                Estado.setUltimaClave(this, claves.get(claves.size() - 1));
            }
        }
        // Historial reciente para la pantalla
        JSONObject recientes = api.get("colector/eventos", "&orderBy=" + ColectorApi.enc("\"$key\"") + "&limitToLast=40");
        Estado.setCache(this, "eventos", recientes == null ? "" : recientes.toString());

        // --- latido del colector + notificacion fija de estado ---
        boolean caido = false;
        String titulo, texto;
        int color;
        java.text.SimpleDateFormat hora = new java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.getDefault());
        if (estado == null) {
            titulo = "\uD83D\uDFE0 NetWatch \u00B7 esperando al colector";
            texto = "Firebase responde, pero el colector aun no publica datos.";
            color = Textos.AMBAR;
        } else {
            long ult = estado.optLong("ultima_conexion", 0);
            long edad = System.currentTimeMillis() - ult;
            caido = ult > 0 && edad > COLECTOR_CAIDO_MS;
            int offline = estado.optInt("offline_total", 0);
            int cOff = estado.optInt("criticos_offline", 0);
            int cTot = estado.optInt("criticos_total", 0);
            int activos = 0, agentes = 0;
            JSONObject sit = estado.optJSONObject("sitios");
            if (sit != null) {
                Iterator<String> it = sit.keys();
                while (it.hasNext()) {
                    JSONObject s = sit.optJSONObject(it.next());
                    if (s == null) continue;
                    agentes++;
                    if (!s.optBoolean("sin_datos")) activos++;
                }
            }
            StringBuilder sb = new StringBuilder();
            sb.append("Equipos ca\u00EDdos: ").append(offline);
            if (cTot > 0) sb.append("\nPrioritarios: ").append(cTot - cOff).append("/").append(cTot).append(" en l\u00EDnea");
            if (agentes > 0) sb.append("\nAgentes de escaneo: ").append(activos).append("/").append(agentes).append(" activos");
            if (ult > 0) sb.append("\n\u00DAltimo latido del colector: ").append(hora.format(new java.util.Date(ult)))
                    .append(" (hace ").append(Textos.duracion(Math.max(0, edad / 1000))).append(")");
            texto = sb.toString();
            if (caido) {
                titulo = "\uD83D\uDD34 NetWatch \u00B7 colector sin reportar";
                color = Textos.ROJO;
            } else if (cOff > 0) {
                titulo = "\uD83D\uDD34 NetWatch \u00B7 " + cOff + " prioritario(s) ca\u00EDdo(s)";
                color = Textos.ROJO;
            } else if (offline > 0) {
                titulo = "\uD83D\uDFE0 NetWatch \u00B7 " + offline + " equipos ca\u00EDdos";
                color = Textos.AMBAR;
            } else {
                titulo = "\uD83D\uDFE2 NetWatch \u00B7 red estable";
                color = Textos.VERDE;
            }
        }
        if (caido && !Estado.avisoColectorCaido(this)) {
            Estado.setAvisoColectorCaido(this, true);
            alerta(900001, CH_CRITICOS, "\uD83D\uDD34 Colector sin reportar",
                    "El PC colector dej\u00F3 de actualizar.\nNo llegar\u00E1n avisos de la red hasta que vuelva.", Textos.ROJO);
        } else if (!caido && Estado.avisoColectorCaido(this)) {
            Estado.setAvisoColectorCaido(this, false);
            alerta(900002, CH_EVENTOS, "\uD83D\uDFE2 Colector de vuelta", "El colector volvi\u00F3 a reportar.", Textos.VERDE);
        }
        actualizarFg(titulo, texto, color);
        sendBroadcast(new Intent(ACCION_ACTUALIZADO).setPackage(getPackageName()));
    }

    /**
     * Igual que el panel (agrupar_eventos): si en un mismo ciclo 3 o mas equipos de un
     * sitio caen (o vuelven), sale UN aviso del sitio con los prioritarios nombrados.
     * Los prioritarios ademas siguen avisando solos, con alerta fuerte.
     */
    private void notificar(JSONObject evs, List<String> claves) {
        java.util.Map<String, List<String>> grupos = new java.util.LinkedHashMap<String, List<String>>();
        for (String k : claves) {
            JSONObject e = evs.optJSONObject(k);
            if (e == null) continue;
            String tipo = e.optString("tipo");
            if (!"cayo".equals(tipo) && !"volvio".equals(tipo)) continue;
            String g = e.optString("sitio") + "|" + tipo;
            if (!grupos.containsKey(g)) grupos.put(g, new ArrayList<String>());
            grupos.get(g).add(k);
        }
        java.util.Set<String> agrupadas = new java.util.HashSet<String>();
        int n = 0;
        for (java.util.Map.Entry<String, List<String>> en : grupos.entrySet()) {
            List<String> ks = en.getValue();
            if (ks.size() < 3) continue;
            JSONObject primero = evs.optJSONObject(ks.get(0));
            String sitio = primero.optString("sitio");
            boolean cae = "cayo".equals(primero.optString("tipo"));
            List<String> crit = new ArrayList<String>();
            for (String k : ks) {
                agrupadas.add(k);
                JSONObject e = evs.optJSONObject(k);
                if (e.optBoolean("critico", false)) {
                    String h = e.optString("hostname", "");
                    crit.add(h.length() > 0 ? h : e.optString("ip"));
                }
            }
            String detalle = crit.isEmpty() ? "Abre la app para ver los equipos de esta red"
                    : crit.size() + " prioritarios: " + join(crit, 4);
            alerta(("g" + en.getKey() + ks.get(0)).hashCode(), crit.isEmpty() ? CH_EVENTOS : CH_CRITICOS,
                    (cae ? "\uD83D\uDD34 " : "\uD83D\uDFE2 ") + sitio + ": " + ks.size()
                            + (cae ? " equipos pasaron a offline" : " equipos volvieron online"), detalle,
                    cae ? Textos.ROJO : Textos.VERDE);
            n++;
        }
        int resto = 0;
        for (String k : claves) {
            JSONObject e = evs.optJSONObject(k);
            if (e == null) continue;
            boolean crit = e.optBoolean("critico", false) || "sitio_sin_datos".equals(e.optString("tipo"));
            if (agrupadas.contains(k) && !e.optBoolean("critico", false)) continue;
            if (n >= MAX_NOTIF_POR_CICLO) { resto++; continue; }
            String[] tt = Textos.evento(e);
            alerta(k.hashCode(), crit ? CH_CRITICOS : CH_EVENTOS, tt[0], tt[1], Textos.color(e));
            n++;
        }
        if (resto > 0) {
            alerta(900003, CH_EVENTOS, "Y " + resto + " cambios mas", "Abre la app para ver el detalle.");
        }
    }

    private static String join(List<String> l, int max) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < l.size() && i < max; i++) { if (i > 0) sb.append(", "); sb.append(l.get(i)); }
        if (l.size() > max) sb.append(" y ").append(l.size() - max).append(" mas");
        return sb.toString();
    }

    // ---------------- notificaciones ----------------
    private void crearCanales() {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager nm = getSystemService(NotificationManager.class);
        NotificationChannel est = new NotificationChannel(CH_ESTADO, "Estado del monitoreo", NotificationManager.IMPORTANCE_LOW);
        est.setShowBadge(false);
        nm.createNotificationChannel(est);
        NotificationChannel ev = new NotificationChannel(CH_EVENTOS, "Cambios en la red", NotificationManager.IMPORTANCE_DEFAULT);
        nm.createNotificationChannel(ev);
        NotificationChannel cr = new NotificationChannel(CH_CRITICOS, "Equipos prioritarios y alertas", NotificationManager.IMPORTANCE_HIGH);
        cr.enableVibration(true);
        cr.setVibrationPattern(new long[]{0, 400, 200, 400, 200, 600});
        nm.createNotificationChannel(cr);
    }

    private PendingIntent abrirApp() {
        Intent i = new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        int fl = PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0);
        return PendingIntent.getActivity(this, 0, i, fl);
    }

    private Notification notifEstado(String titulo, String texto, int color) {
        Intent r = new Intent(this, MonitorService.class).putExtra("refrescar", true);
        int fl = PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0);
        PendingIntent refrescar = PendingIntent.getService(this, 1, r, fl);
        String primera = texto.contains("\n") ? texto.substring(0, texto.indexOf('\n')) : texto;
        NotificationCompat.Builder b = new NotificationCompat.Builder(this, CH_ESTADO)
                .setSmallIcon(R.drawable.ic_stat_netwatch)
                .setContentTitle(titulo)
                .setContentText(primera)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(texto))
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setContentIntent(abrirApp())
                .addAction(0, "Actualizar", refrescar);
        if (color != 0) b.setColor(color);
        return b.build();
    }

    private void actualizarFg(String titulo, String texto, int color) {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        nm.notify(ID_FG, notifEstado(titulo, texto, color));
    }

    private void alerta(int id, String canal, String titulo, String texto) {
        alerta(id, canal, titulo, texto, 0);
    }

    private void alerta(int id, String canal, String titulo, String texto, int color) {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        boolean alta = CH_CRITICOS.equals(canal);
        String primera = texto.contains("\n") ? texto.substring(0, texto.indexOf('\n')) : texto;
        NotificationCompat.Builder b = new NotificationCompat.Builder(this, canal)
                .setSmallIcon(R.drawable.ic_stat_netwatch)
                .setContentTitle(titulo)
                .setContentText(primera)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(texto))
                .setAutoCancel(true)
                .setWhen(System.currentTimeMillis())
                .setShowWhen(true)
                .setContentIntent(abrirApp())
                .setPriority(alta ? NotificationCompat.PRIORITY_HIGH : NotificationCompat.PRIORITY_DEFAULT);
        if (color != 0) b.setColor(color);
        if (alta) b.setVibrate(new long[]{0, 400, 200, 400, 200, 600});
        try { nm.notify(id, b.build()); } catch (SecurityException ignorar) { }
    }

    private static String corto(String s) {
        if (s == null) return "";
        return s.length() > 80 ? s.substring(0, 80) : s;
    }
}
