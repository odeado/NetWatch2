package cl.mercurioantofagasta.netwatch;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;

/** Acceso REST a Firebase Realtime Database (sin FCM, sin SDK). */
public class ColectorApi {

    private static final String PREFS = "netwatch_api";
    private final SharedPreferences sp;

    public ColectorApi(Context ctx) {
        sp = ctx.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private synchronized String token() throws Exception {
        long ahora = System.currentTimeMillis();
        String tok = sp.getString("idToken", null);
        if (tok != null && ahora < sp.getLong("expira", 0) - 60000) return tok;

        String refresh = sp.getString("refreshToken", null);
        if (refresh != null) {
            try {
                String body = "grant_type=refresh_token&refresh_token=" + URLEncoder.encode(refresh, "UTF-8");
                JSONObject r = new JSONObject(http("POST",
                        "https://securetoken.googleapis.com/v1/token?key=" + BuildConfig.API_KEY,
                        body, "application/x-www-form-urlencoded"));
                guardar(r.getString("id_token"), r.getString("refresh_token"), r.getString("expires_in"));
                return sp.getString("idToken", null);
            } catch (Exception e) {
                // refresh vencido: se hace login de nuevo
            }
        }
        JSONObject cred = new JSONObject();
        cred.put("email", BuildConfig.EMAIL);
        cred.put("password", BuildConfig.PASSWORD);
        cred.put("returnSecureToken", true);
        JSONObject r = new JSONObject(http("POST",
                "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=" + BuildConfig.API_KEY,
                cred.toString(), "application/json"));
        guardar(r.getString("idToken"), r.getString("refreshToken"), r.getString("expiresIn"));
        return sp.getString("idToken", null);
    }

    private void guardar(String id, String refresh, String expiraEn) {
        sp.edit().putString("idToken", id).putString("refreshToken", refresh)
                .putLong("expira", System.currentTimeMillis() + Long.parseLong(expiraEn) * 1000L).apply();
    }

    /** GET de un nodo. query ya viene con '&' inicial si lleva parametros (orderBy, etc.). Devuelve null si el nodo no existe. */
    public JSONObject get(String ruta, String query) throws Exception {
        String url = BuildConfig.DB_URL + "/" + ruta + ".json?auth=" + token() + (query == null ? "" : query);
        String txt;
        try {
            txt = http("GET", url, null, null);
        } catch (HttpError e) {
            if (e.codigo == 401) {
                sp.edit().remove("idToken").apply();
                txt = http("GET", BuildConfig.DB_URL + "/" + ruta + ".json?auth=" + token() + (query == null ? "" : query), null, null);
            } else {
                throw e;
            }
        }
        txt = txt.trim();
        if (txt.equals("null") || txt.isEmpty()) return null;
        return new JSONObject(txt);
    }

    static class HttpError extends Exception {
        final int codigo;
        HttpError(int codigo, String msg) { super("HTTP " + codigo + " " + msg); this.codigo = codigo; }
    }

    private String http(String metodo, String url, String cuerpo, String tipo) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(15000);
        c.setReadTimeout(20000);
        c.setRequestMethod(metodo);
        if (cuerpo != null) {
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", tipo);
            OutputStream os = c.getOutputStream();
            os.write(cuerpo.getBytes("UTF-8"));
            os.close();
        }
        int code = c.getResponseCode();
        InputStream is = code >= 400 ? c.getErrorStream() : c.getInputStream();
        StringBuilder sb = new StringBuilder();
        if (is != null) {
            BufferedReader br = new BufferedReader(new InputStreamReader(is, "UTF-8"));
            String l;
            while ((l = br.readLine()) != null) sb.append(l);
            br.close();
        }
        c.disconnect();
        if (code >= 400) throw new HttpError(code, sb.length() > 200 ? sb.substring(0, 200) : sb.toString());
        return sb.toString();
    }

    /** URL-encoding para valores de query (comillas de orderBy). */
    public static String enc(String s) {
        try { return URLEncoder.encode(s, "UTF-8"); } catch (Exception e) { return s; }
    }
}
