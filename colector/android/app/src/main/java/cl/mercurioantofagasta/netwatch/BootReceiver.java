package cl.mercurioantofagasta.netwatch;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context ctx, Intent intent) {
        if (!Estado.monitorActivo(ctx)) return;
        try {
            MonitorService.iniciar(ctx);
        } catch (Exception ignorar) {
            // Android puede negar el inicio en segundo plano; se reanuda al abrir la app.
        }
    }
}
