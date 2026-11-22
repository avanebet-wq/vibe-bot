package app.claudepocket

import android.animation.ValueAnimator
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
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.graphics.drawable.Drawable
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.MotionEvent
import android.view.animation.OvershootInterpolator
import androidx.core.graphics.ColorUtils
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

    /** Направление на «источник света» (единичный вектор от центра). Меняется от наклона телефона, как в iOS. */
    @Volatile var lx = -0.55f
    @Volatile var ly = -0.8f
    private var fx = 0f
    private var fy = 0f
    private var sm: SensorManager? = null
    private val tilt = object : SensorEventListener {
        override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
        override fun onSensorChanged(e: SensorEvent) {
            val roll = (e.values[0] / 9.8f).coerceIn(-1f, 1f)
            val pitch = (e.values[2] / 9.8f).coerceIn(-1f, 1f)
            fx += (roll - fx) * 0.18f
            fy += (pitch - fy) * 0.18f
            val nx = (-0.55f - fx * 0.9f).coerceIn(-1f, 0.8f)
            val ny = (-0.8f + fy * 0.7f).coerceIn(-1f, 0.2f)
            if (kotlin.math.abs(nx - lx) + kotlin.math.abs(ny - ly) > 0.02f) {
                lx = nx; ly = ny
                refresh()
            }
        }
    }

    fun startTilt(ctx: Context) {
        if (sm != null) return
        val m = ctx.getSystemService(Context.SENSOR_SERVICE) as? SensorManager ?: return
        val s = m.getDefaultSensor(Sensor.TYPE_ACCELEROMETER) ?: return
        m.registerListener(tilt, s, SensorManager.SENSOR_DELAY_UI)
        sm = m
    }

    fun stopTilt() {
        sm?.unregisterListener(tilt)
        sm = null
    }

    fun animationsEnabled(): Boolean = if (Build.VERSION.SDK_INT >= 26) ValueAnimator.areAnimatorsEnabled() else true

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
    private val touchP = Paint(Paint.ANTI_ALIAS_FLAG)
    private var tx = 0f
    private var ty = 0f
    private var press = 0f
    private var anim: ValueAnimator? = null

    /** Палец на стекле: оно «вздувается» с пружиной, под пальцем загорается свет. */
    fun touch(e: MotionEvent, grow: Float) {
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> { tx = e.x; ty = e.y; animate(1f, 260L, grow, true) }
            MotionEvent.ACTION_MOVE -> { tx = e.x; ty = e.y; host.invalidate() }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> animate(0f, 520L, grow, false)
        }
    }

    private var from = 0f
    private var target = 0f
    private var startAt = 0L
    private var dur = 1L
    private var spring: android.view.animation.Interpolator = OvershootInterpolator(2f)
    private var growBy = 0f
    private val step = object : Runnable {
        override fun run() {
            val t = ((SystemClock.uptimeMillis() - startAt).toFloat() / dur).coerceIn(0f, 1f)
            press = from + (target - from) * spring.getInterpolation(t)
            host.scaleX = 1f + growBy * press
            host.scaleY = host.scaleX
            host.invalidate()
            if (t < 1f) host.postOnAnimation(this)
        }
    }

    /** Своя покадровая анимация: не зависит от системной настройки «Анимация» (её часто выключают). */
    private fun animate(to: Float, ms: Long, grow: Float, springy: Boolean) {
        host.removeCallbacks(step)
        from = press
        target = to
        startAt = SystemClock.uptimeMillis()
        dur = ms
        growBy = grow
        spring = if (springy) OvershootInterpolator(2.2f) else OvershootInterpolator(1.4f)
        host.postOnAnimation(step)
    }

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
                    r.draw(c, src, rect, rad, dp, press)
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
        fill.color = if (n) Color.argb(if (refracted) 84 else 140, 66, 66, 74) else Color.argb(if (refracted) 78 else 140, 255, 255, 255)
        c.drawRoundRect(rect, rad, rad, fill)

        val cx = rect.centerX()
        val cy = rect.centerY()
        val lx = GlassHub.lx
        val ly = GlassHub.ly
        sheen.shader = RadialGradient(
            cx + lx * rect.width() * 0.55f, cy + ly * rect.height() * 0.9f, max(rect.width() * 0.6f, rect.height() * 1.2f),
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
        val rr = kotlin.math.hypot(rect.width(), rect.height()) / 2f
        rim.shader = LinearGradient(
            cx + lx * rr, cy + ly * rr, cx - lx * rr, cy - ly * rr,
            intArrayOf(Color.argb(a1, 255, 255, 255), Color.argb(0, 255, 255, 255), Color.argb(0, 255, 255, 255), Color.argb(a2, 255, 255, 255)),
            floatArrayOf(0f, 0.3f, 0.64f, 1f), Shader.TileMode.CLAMP,
        )
        c.drawRoundRect(inset, rad, rad, rim)
        hair.color = Color.argb(if (n) 60 else 150, 255, 255, 255)
        c.drawRoundRect(inset, rad, rad, hair)

        // свет под пальцем
        if (press > 0.01f) {
            touchP.shader = RadialGradient(tx, ty, max(rect.width(), rect.height()) * 0.8f,
                intArrayOf(Color.argb((110 * press).toInt().coerceIn(0, 255), 255, 255, 255), Color.TRANSPARENT), floatArrayOf(0f, 1f), Shader.TileMode.CLAMP)
            c.drawRoundRect(rect, rad, rad, touchP)
        }
    }

    @RequiresApi(33)
    private class Refractor(private val host: View) {
        private val node = RenderNode("pocket-glass")
        private val shader = RuntimeShader(load(host.resources))
        private val tp = IntArray(2)
        private val hp = IntArray(2)
        private var key = 0

        fun draw(c: Canvas, src: View, rect: RectF, rad: Float, dp: Float, press: Float) {
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
            val k = java.util.Objects.hash(w, h, rect.left, rect.top, rect.right, rect.bottom, rad, (press * 10).toInt())
            if (k != key) {
                key = k
                configure(rect, rad, dp, press)
            }
            c.drawRenderNode(node)
        }

        private fun configure(rect: RectF, rad: Float, dp: Float, press: Float) {
            val minDim = min(rect.width(), rect.height())
            val rh = min(13f * dp, minDim * 0.32f)
            shader.setFloatUniform("size", rect.width(), rect.height())
            shader.setFloatUniform("offset", -rect.left, -rect.top)
            shader.setFloatUniform("cornerRadii", rad, rad, rad, rad)
            shader.setFloatUniform("refractionHeight", rh)
            shader.setFloatUniform("refractionAmount", -rh * (2.1f + 1.2f * press))
            shader.setFloatUniform("depthEffect", 0.25f)
            shader.setFloatUniform("chromaticAberration", 0.1f + 0.15f * press)
            shader.setFloatUniform("contrast", 0f)
            shader.setFloatUniform("whitePoint", 0f)
            shader.setFloatUniform("chromaMultiplier", 1.35f)
            shader.setFloatUniform("tintColor", 1f, 1f, 1f)
            shader.setFloatUniform("tintAlpha", 0f)
            val sigma = 4f * dp
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

    override fun onTouchEvent(e: MotionEvent): Boolean {
        glass.touch(e, 0.08f)
        return super.onTouchEvent(e)
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


/** Живой фон чата: мягкие цветные пятна системного цвета медленно плывут. Стекло преломляет их, поэтому оно не выглядит плоским. */
class AuroraDrawable(private val ctx: Context) : Drawable() {
    private val handler = Handler(Looper.getMainLooper())
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val shaders = arrayOfNulls<RadialGradient>(3)
    private val m = android.graphics.Matrix()
    private var w = 0
    private var h = 0
    private var running = false
    private val t0 = SystemClock.uptimeMillis()

    /** Насколько прокручен чат: фон сдвигается вместе с текстом (с запаздыванием), и стекло видит движение. */
    var scrollY = 0

    private val loop = object : Runnable {
        override fun run() {
            if (!running) return
            invalidateSelf()
            GlassHub.refresh()
            handler.postDelayed(this, 50)
        }
    }

    fun start() {
        if (running) return
        running = true
        handler.post(loop)
    }

    fun stop() { running = false; handler.removeCallbacks(loop) }

    override fun onBoundsChange(b: android.graphics.Rect) {
        w = b.width(); h = b.height()
        val hsv = FloatArray(3)
        val accent = ctx.getColor(R.color.accent)
        Color.colorToHSV(accent, hsv)
        val night = (ctx.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES
        val a = if (night) 120 else 96
        val shifts = floatArrayOf(0f, 48f, -52f)
        for (i in 0..2) {
            val hh = floatArrayOf((hsv[0] + shifts[i] + 360f) % 360f, max(hsv[1], 0.55f), if (night) 0.85f else 0.95f)
            val col = Color.HSVToColor(a, hh)
            val r = max(w, h) * (if (i == 0) 0.62f else 0.5f)
            shaders[i] = RadialGradient(0f, 0f, r, intArrayOf(col, Color.TRANSPARENT), floatArrayOf(0f, 1f), Shader.TileMode.CLAMP)
        }
    }

    override fun draw(c: Canvas) {
        c.drawColor(ctx.getColor(R.color.bg))
        if (w == 0) return
        val t = (SystemClock.uptimeMillis() - t0) / 1000.0
        for (i in 0..2) {
            val sh = shaders[i] ?: continue
            val k = i + 1
            val x = w * (0.5 + 0.42 * kotlin.math.sin(t * 0.26 * k + i * 2.1)).toFloat()
            val span = h * 1.5f
            val y0 = h * (0.5 + 0.3 * kotlin.math.cos(t * 0.2 * k + i * 1.3)).toFloat() - scrollY * (0.25f + 0.1f * i)
            val y = ((y0 % span) + span) % span - h * 0.25f
            m.setTranslate(x, y)
            sh.setLocalMatrix(m)
            paint.shader = sh
            c.drawRect(0f, 0f, w.toFloat(), h.toFloat(), paint)
        }
    }

    override fun setAlpha(alpha: Int) {}
    override fun setColorFilter(cf: android.graphics.ColorFilter?) {}
    @Suppress("OVERRIDE_DEPRECATION")
    override fun getOpacity(): Int = android.graphics.PixelFormat.OPAQUE
}
