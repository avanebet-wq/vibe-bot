package app.claudepocket

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.util.AttributeSet
import android.view.View
import android.view.animation.LinearInterpolator
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin

/** Крутящееся кольцо из маленьких кружков: «хвост» тускнеет за ведущим кружком. */
class DotsSpinner @JvmOverloads constructor(ctx: Context, attrs: AttributeSet? = null) : View(ctx, attrs) {

    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var angle = 0f
    private var anim: ValueAnimator? = null
    private val dots = 8

    init {
        paint.color = ctx.getColor(R.color.accent)
    }

    override fun onMeasure(w: Int, h: Int) {
        val s = (22 * resources.displayMetrics.density).toInt()
        setMeasuredDimension(s, s)
    }

    override fun onDraw(c: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        val r = min(width, height) / 2f
        val dotR = r * 0.17f
        val ring = r - dotR - 1f
        for (i in 0 until dots) {
            val a = i * 360f / dots
            val behind = ((angle - a) % 360f + 360f) % 360f / 360f // 0 у ведущего кружка, к 1 — хвост
            paint.alpha = (255 * (1f - 0.85f * behind)).toInt()
            val rad = Math.toRadians((a - 90f).toDouble())
            c.drawCircle(cx + ring * cos(rad).toFloat(), cy + ring * sin(rad).toFloat(), dotR * (1f - 0.35f * behind), paint)
        }
    }

    private fun start() {
        if (anim != null) return
        anim = ValueAnimator.ofFloat(0f, 360f).apply {
            duration = 1000
            repeatCount = ValueAnimator.INFINITE
            interpolator = LinearInterpolator()
            addUpdateListener {
                // шаг в 45° даёт «тиканье» как у обычных индикаторов; плавность оставляем промежуточными кадрами
                angle = it.animatedValue as Float
                invalidate()
            }
            start()
        }
    }

    private fun stop() {
        anim?.cancel()
        anim = null
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        if (visibility == VISIBLE) start()
    }

    override fun onDetachedFromWindow() {
        stop()
        super.onDetachedFromWindow()
    }

    override fun onVisibilityChanged(changedView: View, visibility: Int) {
        super.onVisibilityChanged(changedView, visibility)
        if (visibility == VISIBLE && isAttachedToWindow) start() else stop()
    }
}
