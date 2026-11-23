package cl.mercurioantofagasta.netwatch;

import android.content.Context;
import android.content.SharedPreferences;

/** Estado local de la app (ultimo evento visto, ultimo resumen, monitoreo on/off). */
public class Estado {
    private static final String PREFS = "netwatch_estado";

    public static SharedPreferences sp(Context c) {
        return c.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    public static boolean monitorActivo(Context c) { return sp(c).getBoolean("monitor", true); }
    public static void setMonitorActivo(Context c, boolean v) { sp(c).edit().putBoolean("monitor", v).apply(); }

    public static String ultimaClave(Context c) { return sp(c).getString("ultimaClave", null); }
    public static void setUltimaClave(Context c, String k) { sp(c).edit().putString("ultimaClave", k).apply(); }

    /** Ultimo JSON crudo de estado/criticos/eventos para pintar la pantalla sin esperar a la red. */
    public static String cache(Context c, String nombre) { return sp(c).getString("cache_" + nombre, null); }
    public static void setCache(Context c, String nombre, String json) { sp(c).edit().putString("cache_" + nombre, json).apply(); }

    public static boolean avisoColectorCaido(Context c) { return sp(c).getBoolean("colectorCaidoAvisado", false); }
    public static void setAvisoColectorCaido(Context c, boolean v) { sp(c).edit().putBoolean("colectorCaidoAvisado", v).apply(); }
}
