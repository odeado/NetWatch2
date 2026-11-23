package cl.mercurioantofagasta.netwatch;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.view.View;

/** Anillo de porcentaje como los gauges del panel NetWatch. pct < 0 = sin datos. */
public class GaugeView extends View {
    private final Paint fondo = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint arco = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint texto = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF caja = new RectF();
    private int pct = -1;

    public GaugeView(Context c, int colorArco, int colorFondo, int colorTexto) {
        super(c);
        float d = c.getResources().getDisplayMetrics().density;
        fondo.setStyle(Paint.Style.STROKE);
        fondo.setStrokeWidth(4 * d);
        fondo.setColor(colorFondo);
        arco.setStyle(Paint.Style.STROKE);
        arco.setStrokeWidth(4 * d);
        arco.setStrokeCap(Paint.Cap.ROUND);
        arco.setColor(colorArco);
        texto.setColor(colorTexto);
        texto.setTextAlign(Paint.Align.CENTER);
        texto.setTypeface(Typeface.DEFAULT_BOLD);
        texto.setTextSize(11 * d);
    }

    public void setPct(int p) { pct = p; invalidate(); }

    @Override
    protected void onDraw(Canvas c) {
        float pad = arco.getStrokeWidth() / 2f + 1;
        caja.set(pad, pad, getWidth() - pad, getHeight() - pad);
        c.drawOval(caja, fondo);
        if (pct > 0) c.drawArc(caja, -90, 360f * Math.min(pct, 100) / 100f, false, arco);
        String t = pct < 0 ? "-" : pct + "%";
        float y = getHeight() / 2f - (texto.descent() + texto.ascent()) / 2f;
        c.drawText(t, getWidth() / 2f, y, texto);
    }
}
