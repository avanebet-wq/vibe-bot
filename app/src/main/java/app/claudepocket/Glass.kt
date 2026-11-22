package app.claudepocket

import android.content.Context
import android.content.res.Configuration
import android.content.res.Resources
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Outline
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.RenderEffect
import android.graphics.RenderNode
import android.graphics.RuntimeShader
import android.graphics.Shader
import android.os.Build
import android.util.AttributeSet
import android.view.View
import android.view.ViewOutlineProvider
import android.widget.ImageView
import androidx.annotation.RequiresApi
import kotlin.math.max
import kotlin.math.min

/**
 * «Жидкое стекло» как в iOS 26.
 *
 * На Android 13 и новее стекло по-настоящему преломляет и размывает то, что лежит под ним (шейдер AGSL,
 * идея и сам шейдер из проекта AndroidLiquidGlassView, лицензия MIT, © QmDeve). На старых версиях
 * остаётся полупрозрачное стекло с теми же бликами и ободком, но без преломления.
 */
object GlassHub {
    /** Что лежит под стеклом: область чата. Пусто, пока чат не на экране. */
    @Volatile var source: View? = null
    private val views = java.util.Collections.newSetFromMap(java.util.WeakHashMap<View, Boolean>())

    fun add(v: View) { views.add(v) }
    fun remove(v: View) { views.remove(v) }

    /** Перерисовать всё стекло: под ним что-то сдвинулось. */
    fun refresh() {
        for (v in views.toList()) if (v.isAttachedToWindow && v.visibility == View.VISIBLE) v.invalidate()
    }
}

class GlassSurface(private val host: View) {
    private val dp = host.resources.displayMetrics.density
    private var refractor: Any? = null
    private var failed = Build.VERSION.SDK_INT < 33
    private val path = Path()
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val sheen = Paint(Paint.ANTI_ALIAS_FLAG)
    private val glow = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val rim = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 1.3f * dp }
    private val hair = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 0.6f * dp }
    private val inset = RectF()

    private fun night() =
        (host.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES

    /** Рисует стекло в прямоугольнике [rect] с радиусом [rad]. */
    fun draw(c: Canvas, rect: RectF, rad: Float) {
        var refracted = false
        val src = GlassHub.source
        if (!failed && src != null && src.isAttachedToWindow && src.width > 0 && src.visibility == View.VISIBLE && c.isHardwareAccelerated) {
            try {
                if (Build.VERSION.SDK_INT >= 33) {
                    val r = (refractor as? Refractor) ?: Refractor(host).also { refractor = it }
                    path.reset()
                    path.addRoundRect(rect, rad, rad, Path.Direction.CW)
                    c.save()
                    c.clipPath(path)
                    r.draw(c, src, rect, rad, dp)
                    c.restore()
                    refracted = true
                }
            } catch (t: Throwable) {
                failed = true // шейдер не поддержан этим телефоном: дальше только плоское стекло
            }
        }
        finish(c, rect, rad, refracted)
    }

    /** Заливка, блик, внутреннее свечение и ободок, как у стекла iOS. */
    private fun finish(c: Canvas, rect: RectF, rad: Float, refracted: Boolean) {
        val n = night()
        fill.color = if (n) Color.argb(if (refracted) 128 else 160, 66, 66, 74) else Color.argb(if (refracted) 120 else 160, 255, 255, 255)
        c.drawRoundRect(rect, rad, rad, fill)

        sheen.shader = RadialGradient(
            rect.left + rect.width() * 0.18f, rect.top - rect.height() * 0.08f, max(rect.width() * 0.6f, rect.height() * 1.2f),
            intArrayOf(Color.argb(if (n) 52 else 135, 255, 255, 255), Color.TRANSPARENT), floatArrayOf(0f, 1f), Shader.TileMode.CLAMP,
        )
        c.drawRoundRect(rect, rad, rad, sheen)

        // внутреннее свечение у края
        path.reset()
        path.addRoundRect(rect, rad, rad, Path.Direction.CW)
        c.save()
        c.clipPath(path)
        glow.strokeWidth = 14f * dp
        glow.color = Color.argb(if (n) 22 else 60, 255, 255, 255)
        c.drawRoundRect(rect, rad, rad, glow)
        c.restore()

        // ободок: ярко там, где свет входит (сверху слева) и выходит (снизу справа)
        inset.set(rect.left + 0.65f * dp, rect.top + 0.65f * dp, rect.right - 0.65f * dp, rect.bottom - 0.65f * dp)
        val a1 = if (n) 190 else 250
        val a2 = if (n) 80 else 190
        rim.shader = LinearGradient(
            rect.left, rect.top, rect.right, rect.bottom,
            intArrayOf(Color.argb(a1, 255, 255, 255), Color.argb(0, 255, 255, 255), Color.argb(0, 255, 255, 255), Color.argb(a2, 255, 255, 255)),
            floatArrayOf(0f, 0.3f, 0.64f, 1f), Shader.TileMode.CLAMP,
        )
        c.drawRoundRect(inset, rad, rad, rim)
        hair.color = Color.argb(if (n) 60 else 150, 255, 255, 255)
        c.drawRoundRect(inset, rad, rad, hair)
    }

    @RequiresApi(33)
    private class Refractor(private val host: View) {
        private val node = RenderNode("pocket-glass")
        private val shader = RuntimeShader(load(host.resources))
        private val tp = IntArray(2)
        private val hp = IntArray(2)
        private var key = 0

        fun draw(c: Canvas, src: View, rect: RectF, rad: Float, dp: Float) {
            val w = host.width
            val h = host.height
            if (w <= 0 || h <= 0) return
            node.setPosition(0, 0, w, h)
            val rec = node.beginRecording(w, h)
            try {
                src.getLocationInWindow(tp)
                host.getLocationInWindow(hp)
                rec.translate((tp[0] - hp[0]).toFloat(), (tp[1] - hp[1]).toFloat())
                src.draw(rec)
            } finally {
                node.endRecording()
            }
            val k = java.util.Objects.hash(w, h, rect.left, rect.top, rect.right, rect.bottom, rad)
            if (k != key) {
                key = k
                configure(rect, rad, dp)
            }
            c.drawRenderNode(node)
        }

        private fun configure(rect: RectF, rad: Float, dp: Float) {
            val minDim = min(rect.width(), rect.height())
            val rh = min(13f * dp, minDim * 0.32f)
            shader.setFloatUniform("size", rect.width(), rect.height())
            shader.setFloatUniform("offset", -rect.left, -rect.top)
            shader.setFloatUniform("cornerRadii", rad, rad, rad, rad)
            shader.setFloatUniform("refractionHeight", rh)
            shader.setFloatUniform("refractionAmount", -rh * 1.7f)
            shader.setFloatUniform("depthEffect", 0.25f)
            shader.setFloatUniform("chromaticAberration", 0.05f)
            shader.setFloatUniform("contrast", 0f)
            shader.setFloatUniform("whitePoint", 0f)
            shader.setFloatUniform("chromaMultiplier", 1.35f)
            shader.setFloatUniform("tintColor", 1f, 1f, 1f)
            shader.setFloatUniform("tintAlpha", 0f)
            val sigma = 8f * dp
            val blur = RenderEffect.createBlurEffect(sigma, sigma, Shader.TileMode.CLAMP)
            val refract = RenderEffect.createRuntimeShaderEffect(shader, "content")
            node.setRenderEffect(RenderEffect.createChainEffect(refract, blur))
        }

        private fun load(r: Resources): String =
            r.openRawResource(R.raw.liquidglass_effect).bufferedReader().use { it.readText() }
    }
}

/** Круглая кнопка-стекло с иконкой (скрепка слева от поля ввода). */
class GlassCircle @JvmOverloads constructor(ctx: Context, attrs: AttributeSet? = null) : ImageView(ctx, attrs) {
    private val glass = GlassSurface(this)
    private val rect = RectF()

    init {
        val dp = resources.displayMetrics.density
        elevation = 6f * dp
        outlineProvider = object : ViewOutlineProvider() {
            override fun getOutline(v: View, o: Outline) {
                o.setOval(0, 0, v.width, v.height)
                o.alpha = 0.3f
            }
        }
    }

    override fun drawableStateChanged() {
        super.drawableStateChanged()
        val s = if (isPressed) 0.93f else 1f
        animate().scaleX(s).scaleY(s).setDuration(140).start()
    }

    override fun onAttachedToWindow() { super.onAttachedToWindow(); GlassHub.add(this) }
    override fun onDetachedFromWindow() { GlassHub.remove(this); super.onDetachedFromWindow() }

    override fun onDraw(c: Canvas) {
        val i = resources.displayMetrics.density
        rect.set(i, i, width - i, height - i)
        glass.draw(c, rect, rect.height() / 2f)
        super.onDraw(c)
    }
}
