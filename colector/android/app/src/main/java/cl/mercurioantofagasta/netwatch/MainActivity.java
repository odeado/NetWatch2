package cl.mercurioantofagasta.netwatch;

import android.Manifest;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.ContextCompat;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Iterator;
import java.util.List;

/** Pantalla en el estilo de NetWatch: tarjetas oscuras con puntos verde/ambar/rojo. */
public class MainActivity extends AppCompatActivity {

    private LinearLayout contenido;
    private SwipeRefreshLayout swipe;
    private TextView btnMonitor;
    private final BroadcastReceiver rx = new BroadcastReceiver() {
        @Override public void onReceive(Context c, Intent i) { pintar(); swipe.setRefreshing(false); }
    };

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        setContentView(R.layout.activity_main);
        contenido = findViewById(R.id.contenido);
        swipe = findViewById(R.id.swipeRefresh);
        btnMonitor = findViewById(R.id.btnMonitor);

        swipe.setColorSchemeColors(color(R.color.primary));
        swipe.setProgressBackgroundColorSchemeColor(color(R.color.panel));
        swipe.setOnRefreshListener(new SwipeRefreshLayout.OnRefreshListener() {
            @Override public void onRefresh() {
                // Reinicia el servicio: hace un ciclo inmediato.
                MonitorService.detener(MainActivity.this);
                if (Estado.monitorActivo(MainActivity.this)) MonitorService.iniciar(MainActivity.this);
                swipe.postDelayed(new Runnable() { @Override public void run() { swipe.setRefreshing(false); pintar(); } }, 4000);
            }
        });
        btnMonitor.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                boolean nuevo = !Estado.monitorActivo(MainActivity.this);
                Estado.setMonitorActivo(MainActivity.this, nuevo);
                if (nuevo) MonitorService.iniciar(MainActivity.this); else MonitorService.detener(MainActivity.this);
                pintar();
            }
        });

        pedirPermisos();
    }

    @Override
    protected void onStart() {
        super.onStart();
        IntentFilter f = new IntentFilter(MonitorService.ACCION_ACTUALIZADO);
        ContextCompat.registerReceiver(this, rx, f, ContextCompat.RECEIVER_NOT_EXPORTED);
        if (Estado.monitorActivo(this)) {
            try { MonitorService.iniciar(this); } catch (Exception ignorar) { }
        }
        pintar();
    }

    @Override
    protected void onStop() {
        super.onStop();
        try { unregisterReceiver(rx); } catch (Exception ignorar) { }
    }

    // ---------------- permisos ----------------
    private void pedirPermisos() {
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 10);
        } else {
            pedirBateria();
        }
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] res) {
        super.onRequestPermissionsResult(code, perms, res);
        pedirBateria();
    }

    private void pedirBateria() {
        if (Build.VERSION.SDK_INT < 23) return;
        final PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        if (pm.isIgnoringBatteryOptimizations(getPackageName())) return;
        if (Estado.sp(this).getBoolean("bateriaPreguntada", false)) return;
        Estado.sp(this).edit().putBoolean("bateriaPreguntada", true).apply();
        new AlertDialog.Builder(this)
                .setTitle("Mantener el monitoreo activo")
                .setMessage("Para recibir avisos con la pantalla apagada, excluye NetWatch Colector de la optimizacion de bateria.")
                .setPositiveButton("Configurar", (d, w) -> {
                    try {
                        startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                                Uri.parse("package:" + getPackageName())));
                    } catch (Exception e) {
                        startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
                    }
                })
                .setNegativeButton("Ahora no", null)
                .show();
    }

    // ---------------- pantalla ----------------
    private void pintar() {
        btnMonitor.setText(Estado.monitorActivo(this) ? "Pausar" : "Reanudar");
        contenido.removeAllViews();

        JSONObject estado = leer("estado");
        JSONObject criticos = leer("criticos");
        JSONObject eventos = leer("eventos");

        // --- tarjeta de estado general ---
        LinearLayout c1 = tarjeta("ESTADO DEL COLECTOR");
        if (!Estado.monitorActivo(this)) {
            fila(c1, color(R.color.warn), "Monitoreo en pausa", "No llegaran avisos hasta reanudar");
        } else if (estado == null) {
            String err = Estado.cache(this, "error");
            if (err != null && err.length() > 0) {
                fila(c1, color(R.color.err), "No se pudo leer Firebase", err);
            } else {
                fila(c1, color(R.color.warn), "El colector aun no publica nada",
                        "Firebase responde, pero no hay datos en colector/estado. Revisa que el PC colector este corriendo (no en modo prueba).");
            }
        } else {
            long ult = estado.optLong("ultima_conexion", 0);
            long edad = System.currentTimeMillis() - ult;
            boolean caido = ult > 0 && edad > MonitorService.COLECTOR_CAIDO_MS;
            if (caido) {
                fila(c1, color(R.color.err), "Colector sin reportar",
                        "Ultimo latido hace " + Textos.duracion(edad / 1000));
            } else {
                fila(c1, color(R.color.ok), "Colector en linea",
                        "Ciclo #" + estado.optInt("ciclo") + " - hace " + Textos.duracion(Math.max(0, edad / 1000)));
            }
            int off = estado.optInt("offline_total", 0);
            fila(c1, off > 0 ? color(R.color.err) : color(R.color.ok), off + " equipos caidos",
                    off > 0 ? "Revisa la lista de cambios" : "Toda la red responde");
        }

        // --- prioritarios ---
        LinearLayout c2 = tarjeta("EQUIPOS PRIORITARIOS");
        List<JSONObject> lista = new ArrayList<JSONObject>();
        if (criticos != null) {
            Iterator<String> it = criticos.keys();
            while (it.hasNext()) { JSONObject o = criticos.optJSONObject(it.next()); if (o != null) lista.add(o); }
        }
        if (lista.isEmpty()) {
            fila(c2, color(R.color.muted), "Sin equipos prioritarios", "Marcalos como criticos en NetWatch");
        } else {
            Collections.sort(lista, (a, bb) -> {
                int ra = rango(a), rb = rango(bb);
                return ra != rb ? ra - rb : a.optString("ip").compareTo(bb.optString("ip"));
            });
            for (JSONObject o : lista) {
                String nombre = o.optString("hostname", "");
                if (nombre.length() == 0) nombre = o.optString("ip");
                String det = o.optString("ip") + (o.optString("sitio").length() > 0 ? " - " + o.optString("sitio") : "");
                if (o.isNull("online")) {
                    fila(c2, color(R.color.muted), nombre, det + " - sin datos");
                } else if (o.optBoolean("online")) {
                    fila(c2, color(R.color.ok), nombre, det + " - en linea");
                } else {
                    long desde = o.optLong("desde_ms", 0);
                    String t = desde > 0 ? " - caido hace " + Textos.duracion(Math.max(0, (System.currentTimeMillis() - desde) / 1000)) : " - caido";
                    fila(c2, color(R.color.err), nombre, det + t);
                }
            }
        }

        // --- agentes de escaneo (chips) ---
        JSONObject sit = estado == null ? null : estado.optJSONObject("sitios");
        List<String> nombres = new ArrayList<String>();
        if (sit != null) {
            Iterator<String> it = sit.keys();
            while (it.hasNext()) nombres.add(it.next());
            Collections.sort(nombres);
        }
        LinearLayout c3 = tarjeta("AGENTES DE ESCANEO");
        if (nombres.isEmpty()) {
            fila(c3, color(R.color.muted), "Sin agentes", "El colector aun no lee escaneos");
        } else {
            FlowLayout flow = new FlowLayout(this);
            for (String n : nombres) {
                JSONObject s = sit.optJSONObject(n);
                if (s != null) flow.addView(chip(s, n));
            }
            c3.setPadding(dp(14), dp(12), dp(14), dp(12));
            c3.addView(flow, new LinearLayout.LayoutParams(-1, -2));
        }

        // --- gauges por sitio (% fuera de linea), de peor a mejor ---
        if (!nombres.isEmpty()) {
            List<JSONObject> gs = new ArrayList<JSONObject>();
            for (String n : nombres) {
                JSONObject s = sit.optJSONObject(n);
                if (s != null) gs.add(s);
            }
            Collections.sort(gs, (x, y) -> pctOffline(y) - pctOffline(x));
            tituloSeccion("FUERA DE LINEA POR RED");
            LinearLayout filaG = null;
            for (int i = 0; i < gs.size(); i++) {
                if (i % 2 == 0) {
                    filaG = new LinearLayout(this);
                    filaG.setOrientation(LinearLayout.HORIZONTAL);
                    contenido.addView(filaG, new LinearLayout.LayoutParams(-1, -2));
                }
                filaG.addView(gauge(gs.get(i)), cardParams(i % 2 == 0));
            }
        }

        // --- ultimos cambios ---
        LinearLayout c4 = tarjeta("ULTIMOS CAMBIOS");
        List<String> claves = new ArrayList<String>();
        if (eventos != null) {
            Iterator<String> it = eventos.keys();
            while (it.hasNext()) claves.add(it.next());
            Collections.sort(claves, Collections.reverseOrder());
        }
        if (claves.isEmpty()) {
            fila(c4, color(R.color.muted), "Sin cambios registrados", "");
        } else {
            int max = Math.min(claves.size(), 25);
            for (int i = 0; i < max; i++) {
                JSONObject e = eventos.optJSONObject(claves.get(i));
                if (e == null) continue;
                String tipo = e.optString("tipo");
                int col = "cayo".equals(tipo) || "sitio_sin_datos".equals(tipo) ? color(R.color.err)
                        : "nuevo".equals(tipo) ? color(R.color.primary) : color(R.color.ok);
                String[] tt = Textos.evento(e);
                long ts = e.optLong("ts", 0);
                String cuando = ts > 0 ? "hace " + Textos.duracion(Math.max(0, (System.currentTimeMillis() - ts) / 1000)) : "";
                fila(c4, col, tt[0], cuando + (cuando.length() > 0 ? " - " : "") + tt[1].replace("\n", " - "));
            }
        }
    }

    private int pctOffline(JSONObject s) {
        int obs = s.optInt("total", 0);
        if (s.optBoolean("sin_datos") || obs == 0) return -1;
        return Math.round(100f * s.optInt("offline", 0) / obs);
    }

    /** Mismas reglas que estado_agente() del panel. */
    private String estadoAgente(JSONObject s) {
        long ult = s.optLong("ultimo_escaneo_ms", 0);
        if (ult <= 0) return "sin_datos";
        long inter = s.optLong("intervalo_sitio_seg", 120);
        long edad = (System.currentTimeMillis() - ult) / 1000;
        if (edad > Math.max(inter * 3, 600)) return "sin_datos";
        return edad <= Math.max(inter * 1.5, 60) ? "activo" : "retrasado";
    }

    private View chip(JSONObject s, String nombre) {
        String est = estadoAgente(s);
        int col = "activo".equals(est) ? color(R.color.ok) : "retrasado".equals(est) ? color(R.color.warn) : color(R.color.muted);
        long ult = s.optLong("ultimo_escaneo_ms", 0);
        String txt = ("activo".equals(est) ? "Activo" : "retrasado".equals(est) ? "Retrasado" : "Sin informacion")
                + " - " + (ult > 0 ? "hace " + Textos.duracion(Math.max(0, (System.currentTimeMillis() - ult) / 1000)) : "sin reportes");
        LinearLayout ch = new LinearLayout(this);
        ch.setOrientation(LinearLayout.HORIZONTAL);
        ch.setGravity(Gravity.CENTER_VERTICAL);
        ch.setPadding(dp(10), dp(6), dp(12), dp(6));
        android.graphics.drawable.GradientDrawable bg = new android.graphics.drawable.GradientDrawable();
        bg.setCornerRadius(dp(20));
        bg.setColor(color(R.color.background));
        bg.setStroke(dp(1), color(R.color.border));
        ch.setBackground(bg);
        View punto = new View(this);
        android.graphics.drawable.GradientDrawable g = new android.graphics.drawable.GradientDrawable();
        g.setShape(android.graphics.drawable.GradientDrawable.OVAL);
        g.setColor(col);
        punto.setBackground(g);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(8), dp(8));
        lp.rightMargin = dp(8);
        ch.addView(punto, lp);
        TextView n = new TextView(this);
        String et = s.optString("etiqueta", nombre);
        n.setText(nombre.length() > 0 ? Character.toUpperCase(nombre.charAt(0)) + nombre.substring(1) : et);
        n.setTextColor(color(R.color.text));
        n.setTextSize(12);
        n.setTypeface(Typeface.DEFAULT_BOLD);
        ch.addView(n);
        TextView t = new TextView(this);
        t.setText("  " + txt);
        t.setTextColor(col);
        t.setTextSize(11);
        ch.addView(t);
        return ch;
    }

    private View gauge(JSONObject s) {
        int pct = pctOffline(s);
        int col = pct < 0 ? color(R.color.muted) : pct >= 50 ? color(R.color.err) : pct >= 20 ? color(R.color.warn) : color(R.color.ok);
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.HORIZONTAL);
        card.setGravity(Gravity.CENTER_VERTICAL);
        card.setBackgroundResource(R.drawable.bg_card);
        card.setPadding(dp(12), dp(12), dp(12), dp(12));
        GaugeView gv = new GaugeView(this, col, color(R.color.border), color(R.color.text));
        gv.setPct(pct);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(48), dp(48));
        lp.rightMargin = dp(10);
        card.addView(gv, lp);
        LinearLayout col2 = new LinearLayout(this);
        col2.setOrientation(LinearLayout.VERTICAL);
        TextView t = new TextView(this);
        t.setText(s.optString("etiqueta", "-"));
        t.setTextColor(color(R.color.text));
        t.setTextSize(13);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        col2.addView(t);
        TextView d = new TextView(this);
        d.setText(pct < 0 ? "Sin informacion reciente" : s.optInt("offline") + "/" + s.optInt("total") + " fuera de linea");
        d.setTextColor(color(R.color.muted));
        d.setTextSize(11);
        col2.addView(d);
        card.addView(col2, new LinearLayout.LayoutParams(0, -2, 1f));
        return card;
    }

    private LinearLayout.LayoutParams cardParams(boolean izq) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, -2, 1f);
        lp.topMargin = dp(8);
        if (izq) lp.rightMargin = dp(4); else lp.leftMargin = dp(4);
        return lp;
    }

    private void tituloSeccion(String titulo) {
        TextView t = new TextView(this);
        t.setText(titulo);
        t.setTextColor(color(R.color.muted));
        t.setTextSize(11);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setLetterSpacing(0.08f);
        LinearLayout.LayoutParams lpT = new LinearLayout.LayoutParams(-1, -2);
        lpT.topMargin = dp(14);
        contenido.addView(t, lpT);
    }

    private int rango(JSONObject o) {
        if (o.isNull("online")) return 1;
        return o.optBoolean("online") ? 2 : 0;
    }

    private JSONObject leer(String nombre) {
        String s = Estado.cache(this, nombre);
        if (s == null || s.length() == 0) return null;
        try { return new JSONObject(s); } catch (Exception e) { return null; }
    }

    private int color(int id) { return ContextCompat.getColor(this, id); }
    private int dp(int v) { return (int) (v * getResources().getDisplayMetrics().density + 0.5f); }

    private LinearLayout tarjeta(String titulo) {
        TextView t = new TextView(this);
        t.setText(titulo);
        t.setTextColor(color(R.color.muted));
        t.setTextSize(11);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setLetterSpacing(0.08f);
        LinearLayout.LayoutParams lpT = new LinearLayout.LayoutParams(-1, -2);
        lpT.topMargin = dp(14);
        lpT.bottomMargin = dp(6);
        contenido.addView(t, lpT);

        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackgroundResource(R.drawable.bg_card);
        card.setPadding(dp(14), dp(6), dp(14), dp(6));
        contenido.addView(card, new LinearLayout.LayoutParams(-1, -2));
        return card;
    }

    private void fila(LinearLayout card, int colorPunto, String titulo, String detalle) {
        LinearLayout f = new LinearLayout(this);
        f.setOrientation(LinearLayout.HORIZONTAL);
        f.setGravity(Gravity.CENTER_VERTICAL);
        f.setPadding(0, dp(8), 0, dp(8));

        View punto = new View(this);
        android.graphics.drawable.GradientDrawable g = new android.graphics.drawable.GradientDrawable();
        g.setShape(android.graphics.drawable.GradientDrawable.OVAL);
        g.setColor(colorPunto);
        punto.setBackground(g);
        LinearLayout.LayoutParams lpP = new LinearLayout.LayoutParams(dp(10), dp(10));
        lpP.rightMargin = dp(12);
        f.addView(punto, lpP);

        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        TextView t = new TextView(this);
        t.setText(titulo);
        t.setTextColor(color(R.color.text));
        t.setTextSize(14);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        col.addView(t);
        if (detalle != null && detalle.length() > 0) {
            TextView d = new TextView(this);
            d.setText(detalle);
            d.setTextColor(color(R.color.muted));
            d.setTextSize(12);
            col.addView(d);
        }
        f.addView(col, new LinearLayout.LayoutParams(0, -2, 1f));
        card.addView(f, new LinearLayout.LayoutParams(-1, -2));
    }
}
