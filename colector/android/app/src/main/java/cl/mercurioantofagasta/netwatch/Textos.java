package cl.mercurioantofagasta.netwatch;

import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/** Redaccion de los avisos (compartida entre notificaciones y pantalla). */
public class Textos {

    public static final int ROJO = 0xFFF48891, VERDE = 0xFF4ADEA3, AZUL = 0xFF60A5FA, AMBAR = 0xFFF5C15C;

    /** Color de acento de un evento (para la notificacion). */
    public static int color(JSONObject e) {
        String tipo = e.optString("tipo");
        if ("cayo".equals(tipo)) return ROJO;
        if ("volvio".equals(tipo) || "sitio_recuperado".equals(tipo)) return VERDE;
        if ("nuevo".equals(tipo)) return AZUL;
        return AMBAR;
    }

    /**
     * Devuelve {titulo, detalle}. El detalle trae varias lineas; la primera es
     * "Usuario (hostname - IP)" para que se lea aun con la notificacion cerrada.
     */
    public static String[] evento(JSONObject e) {
        String tipo = e.optString("tipo");
        String sitio = e.optString("sitio", "");
        String ip = e.optString("ip", "");
        String host = e.optString("hostname", "");
        String resp = e.optString("responsable", "");
        boolean crit = e.optBoolean("critico", false);

        // Linea principal: responsable si existe, si no hostname, si no IP
        String lineaPrincipal;
        if (resp.length() > 0) {
            lineaPrincipal = resp;
        } else if (host.length() > 0) {
            lineaPrincipal = host;
        } else {
            lineaPrincipal = ip;
        }

        StringBuilder d = new StringBuilder();
        d.append(lineaPrincipal);
        // Segunda linea: hostname - IP (maquina y red)
        if (host.length() > 0 || ip.length() > 0) {
            StringBuilder maquina = new StringBuilder();
            if (host.length() > 0) maquina.append(host);
            if (host.length() > 0 && ip.length() > 0) maquina.append("  -  ");
            if (ip.length() > 0) maquina.append(ip);
            if (!maquina.toString().equals(lineaPrincipal)) lin(d, maquina.toString());
        }
        if (sitio.length() > 0) lin(d, "Sitio: " + sitio);
        if (crit) lin(d, "Equipo PRIORITARIO");

        String titulo;
        if ("cayo".equals(tipo)) {
            titulo = "\uD83D\uDD34 Pas\u00F3 a offline" + (crit ? " (prioritario)" : "");
        } else if ("volvio".equals(tipo)) {
            titulo = "\uD83D\uDFE2 Volvi\u00F3 online" + (crit ? " (prioritario)" : "");
            long seg = e.optLong("offline_seg", 0);
            if (seg > 0) lin(d, "Estuvo ca\u00EDdo: " + duracion(seg));
        } else if ("nuevo".equals(tipo)) {
            titulo = "\uD83D\uDD35 Nuevo equipo en la red";
        } else if ("sitio_sin_datos".equals(tipo)) {
            titulo = "\uD83D\uDFE0 Sitio sin reportar";
            d.append("Scanner de ").append(sitio);
            lin(d, "Sin publicar hace " + e.optInt("minutos", 0) + " min");
        } else if ("sitio_recuperado".equals(tipo)) {
            titulo = "\uD83D\uDFE2 Sitio de vuelta";
            d.append("Scanner de ").append(sitio);
            lin(d, "Volvi\u00F3 a publicar");
        } else {
            titulo = tipo;
        }
        long ts = e.optLong("ts", 0);
        if (ts > 0) lin(d, "Hora: " + new SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(new Date(ts)));
        return new String[]{titulo, d.toString()};
    }

    private static void lin(StringBuilder sb, String s) {
        if (sb.length() > 0) sb.append('\n');
        sb.append(s);
    }

    public static String duracion(long seg) {
        if (seg < 90) return seg + " s";
        long min = seg / 60;
        if (min < 90) return min + " min";
        long h = min / 60;
        if (h < 48) return h + " h " + (min % 60) + " min";
        return (h / 24) + " d";
    }
}
