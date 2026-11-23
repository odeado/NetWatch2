package cl.mercurioantofagasta.netwatch;

import android.content.Context;
import android.view.View;
import android.view.ViewGroup;

/** Reparte los hijos en filas y salta de linea cuando no caben (para los chips de agentes). */
public class FlowLayout extends ViewGroup {
    private final int sep;

    public FlowLayout(Context c) {
        super(c);
        sep = (int) (8 * c.getResources().getDisplayMetrics().density);
    }

    @Override
    protected void onMeasure(int wSpec, int hSpec) {
        int ancho = MeasureSpec.getSize(wSpec);
        int x = 0, y = 0, fila = 0;
        for (int i = 0; i < getChildCount(); i++) {
            View v = getChildAt(i);
            v.measure(MeasureSpec.makeMeasureSpec(ancho, MeasureSpec.AT_MOST), MeasureSpec.UNSPECIFIED);
            int w = v.getMeasuredWidth(), h = v.getMeasuredHeight();
            if (x > 0 && x + w > ancho) { x = 0; y += fila + sep; fila = 0; }
            x += w + sep;
            fila = Math.max(fila, h);
        }
        setMeasuredDimension(ancho, y + fila);
    }

    @Override
    protected void onLayout(boolean ch, int l, int t, int r, int b) {
        int ancho = r - l;
        int x = 0, y = 0, fila = 0;
        for (int i = 0; i < getChildCount(); i++) {
            View v = getChildAt(i);
            int w = v.getMeasuredWidth(), h = v.getMeasuredHeight();
            if (x > 0 && x + w > ancho) { x = 0; y += fila + sep; fila = 0; }
            v.layout(x, y, x + w, y + h);
            x += w + sep;
            fila = Math.max(fila, h);
        }
    }
}
