package app.claudepocket

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapShader
import android.graphics.BlurMaskFilter
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.SweepGradient
import android.graphics.Typeface
import android.os.Build
import android.os.SystemClock
import android.util.AttributeSet
import android.view.View
import android.widget.FrameLayout
import androidx.core.graphics.ColorUtils
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin

/** Системная настройка «убрать анимацию» (масштаб анимаций = 0) выключает все наши движения. */
private fun animationsOn(): Boolean =
    if (Build.VERSION.SDK_INT >= 26) ValueAnimator.areAnimatorsEnabled() else true

/** Круглый аватар: фото пользователя или градиент с «>_», со свечением вокруг. */
class AvatarView @JvmOverloads constructor(ctx: Context, attrs: AttributeSet? = null) : View(ctx, attrs) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
        textAlign = Paint.Align.CENTER
    }
    private var bitmap: Bitmap? = null
    private var shader: BitmapShader? = null

    /** С свечением: сам аватар занимает 55% вида, остальное — ореол. Без свечения — весь вид. */
    var glow = true
        set(v) { field = v; invalidate() }

    fun setBitmap(b: Bitmap?) {
        bitmap = b
        shader = b?.let { BitmapShader(it, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP) }
        invalidate()
    }

    override fun onDraw(c: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        val full = min(width, height) / 2f
        val r = if (glow) full * 0.55f else full
        val accent = context.getColor(R.color.accent)
        val tx = context.getColor(R.color.text)
        if (glow) {
            // ореол: мягкое свечение цвета акцента и тонкое кольцо
            paint.shader = RadialGradient(cx, cy, full, intArrayOf(ColorUtils.setAlphaComponent(accent, 105), Color.TRANSPARENT), floatArrayOf(r / full, 1f), Shader.TileMode.CLAMP)
            c.drawCircle(cx, cy, full, paint)
            paint.shader = null
            paint.color = ColorUtils.setAlphaComponent(tx, 20)
            c.drawCircle(cx, cy, r + 8f * resources.displayMetrics.density, paint)
        }
        val sh = shader
        val bmp = bitmap
        if (sh != null && bmp != null) {
            val m = Matrix()
            val s = (2 * r) / min(bmp.width, bmp.height)
            m.setScale(s, s)
            m.postTranslate(cx - bmp.width * s / 2f, cy - bmp.height * s / 2f)
            sh.setLocalMatrix(m)
            paint.shader = sh
            c.drawCircle(cx, cy, r, paint)
            paint.shader = null
        } else {
            val light = ColorUtils.blendARGB(accent, Color.WHITE, 0.45f)
            val dark = ColorUtils.blendARGB(accent, Color.BLACK, 0.5f)
            paint.shader = RadialGradient(cx - r * 0.36f, cy - r * 0.48f, r * 2f, intArrayOf(light, accent, dark), floatArrayOf(0f, 0.5f, 1f), Shader.TileMode.CLAMP)
            c.drawCircle(cx, cy, r, paint)
            paint.shader = null
            text.color = context.getColor(R.color.on_accent)
            text.textSize = r * 0.62f
            c.drawText(">_", cx, cy - (text.ascent() + text.descent()) / 2f, text)
        }
    }
}

/** Стеклянные пузыри с эмодзи, плавно дрейфующие вокруг центра (профиль и шапка чата). */
class BubblesView @JvmOverloads constructor(ctx: Context, attrs: AttributeSet? = null) : View(ctx, attrs) {
    private val emojis = listOf("🤖", "✨", "🚀", "💡", "🔧", "🎨", "📱", "⚡", "🧠", "💬", "🛠", "📦")
    private val dp = resources.displayMetrics.density
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val shine = Paint(Paint.ANTI_ALIAS_FLAG)
    private val depth = Paint(Paint.ANTI_ALIAS_FLAG)
    private val edge = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = dp }
    private val emo = Paint(Paint.ANTI_ALIAS_FLAG).apply { textAlign = Paint.Align.CENTER }
    private val m = Matrix()
    private var shineShader: RadialGradient? = null
    private var depthShader: RadialGradient? = null
    private var running = false

    var count = 12
    /** Положение центра по вертикали в dp от верха; отрицательное — центр вида. */
    var centerYdp = -1f
    var radiiDp = floatArrayOf(112f, 138f, 160f)
    var sizesDp = floatArrayOf(44f, 36f, 50f)
    var xk = 0.95f
    var yk = 1.28f

    private fun ensureShaders() {
        if (shineShader != null) return
        val accent = context.getColor(R.color.accent)
        shineShader = RadialGradient(0f, 0f, 1f, intArrayOf(Color.argb(107, 255, 255, 255), Color.argb(18, 255, 255, 255), Color.TRANSPARENT), floatArrayOf(0f, 0.52f, 0.8f), Shader.TileMode.CLAMP)
        depthShader = RadialGradient(0f, 0f, 1f, intArrayOf(ColorUtils.setAlphaComponent(accent, 70), Color.TRANSPARENT), floatArrayOf(0f, 1f), Shader.TileMode.CLAMP)
    }

    override fun onDraw(c: Canvas) {
        ensureShaders()
        val tx = context.getColor(R.color.text)
        val anim = animationsOn()
        val t = if (anim) SystemClock.uptimeMillis() / 1000f else 0f
        val cx = width / 2f
        val cy = if (centerYdp >= 0) centerYdp * dp else height / 2f
        val n = count.coerceIn(1, emojis.size)
        fill.color = ColorUtils.setAlphaComponent(tx, 20)
        edge.color = ColorUtils.setAlphaComponent(tx, 61)
        for (i in 0 until n) {
            val ring = i % 3
            val ang = (i.toFloat() / n) * (2 * Math.PI).toFloat() + ring * 0.55f
            val rad = radiiDp[ring] * dp
            val speed = 0.45f + (i % 5) * 0.09f
            val ph = i * 0.9f
            val x = cx + cos(ang) * rad * xk + sin(t * speed + ph) * 12f * dp
            val y = cy + sin(ang) * rad * yk + cos(t * speed * 0.8f + ph) * 14f * dp
            val r = sizesDp[ring] * dp / 2f * (1f + 0.04f * sin(t * speed + ph))
            c.drawCircle(x, y, r, fill)
            shineShader?.let {
                m.setScale(r * 1.2f, r * 1.2f); m.postTranslate(x - r * 0.35f, y - r * 0.4f); it.setLocalMatrix(m)
                shine.shader = it
                c.drawCircle(x, y, r, shine)
            }
            depthShader?.let {
                m.setScale(r, r); m.postTranslate(x, y + r * 0.8f); it.setLocalMatrix(m)
                depth.shader = it
                c.drawCircle(x, y, r, depth)
            }
            c.drawCircle(x, y, r, edge)
            emo.textSize = r * 0.95f
            c.drawText(emojis[i], x, y - (emo.ascent() + emo.descent()) / 2f, emo)
        }
        if (running && anim) postInvalidateOnAnimation()
    }

    override fun onAttachedToWindow() { super.onAttachedToWindow(); running = visibility == VISIBLE; invalidate() }
    override fun onDetachedFromWindow() { running = false; super.onDetachedFromWindow() }
    override fun onVisibilityChanged(changedView: View, visibility: Int) {
        super.onVisibilityChanged(changedView, visibility)
        running = visibility == VISIBLE && isAttachedToWindow
        if (running) invalidate()
    }
}
