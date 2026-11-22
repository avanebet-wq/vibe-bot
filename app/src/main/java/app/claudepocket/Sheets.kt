package app.claudepocket

import android.app.Activity
import android.app.Dialog
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.graphics.drawable.ColorDrawable
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.core.graphics.ColorUtils

/** Нижние листы в стиле приложения: ручка, скруглённый верх, таблетки и строки с выбором-кружком. */
object Sheets {

    class Row(val title: String, val subtitle: String, val selected: Boolean, val enabled: Boolean = true, val onClick: () -> Unit)
    class Chip(val label: String, val selected: Boolean, val onClick: () -> Unit)
    class Section(val heading: String, val rows: List<Row> = emptyList(), val chips: List<Chip> = emptyList())

    fun show(act: Activity, heading: String, rows: List<Row>) = show(act, listOf(Section(heading, rows)))

    fun show(act: Activity, sections: List<Section>) {
        val d = Dialog(act)
        d.requestWindowFeature(Window.FEATURE_NO_TITLE)
        val dp = { v: Int -> (v * act.resources.displayMetrics.density).toInt() }
        val panel = act.getColor(R.color.panel)
        val key = act.getColor(R.color.key)
        val line = act.getColor(R.color.line)
        val text = act.getColor(R.color.text)
        val muted = act.getColor(R.color.muted)
        val accent = act.getColor(R.color.accent)
        val onAccent = act.getColor(R.color.on_accent)

        val box = LinearLayout(act)
        box.orientation = LinearLayout.VERTICAL
        box.setPadding(dp(12), dp(10), dp(12), dp(18))
        val top = dp(28).toFloat()
        box.background = GradientDrawable().apply {
            setColor(panel)
            cornerRadii = floatArrayOf(top, top, top, top, 0f, 0f, 0f, 0f)
            setStroke(dp(1), line)
        }
        // ручка
        val handle = View(act)
        handle.background = GradientDrawable().apply { setColor(ColorUtils.setAlphaComponent(text, 80)); cornerRadius = dp(2).toFloat() }
        box.addView(handle, LinearLayout.LayoutParams(dp(34), dp(4)).apply { gravity = Gravity.CENTER_HORIZONTAL; bottomMargin = dp(12) })

        val list = LinearLayout(act)
        list.orientation = LinearLayout.VERTICAL
        sections.forEach { s ->
            list.addView(TextView(act).apply {
                this.text = s.heading.uppercase()
                setTextColor(muted)
                textSize = 12f
                letterSpacing = 0.06f
                typeface = Typeface.DEFAULT_BOLD
                setPadding(dp(6), dp(6), dp(6), dp(8))
            })
            if (s.chips.isNotEmpty()) {
                val flow = Flow(act, dp(6), dp(6))
                s.chips.forEach { c ->
                    flow.addView(TextView(act).apply {
                        this.text = c.label
                        gravity = Gravity.CENTER
                        textSize = 13f
                        typeface = Typeface.DEFAULT_BOLD
                        setTextColor(if (c.selected) onAccent else text)
                        setPadding(dp(14), 0, dp(14), 0)
                        minHeight = dp(36)
                        background = GradientDrawable().apply { setColor(if (c.selected) accent else key); cornerRadius = dp(18).toFloat() }
                        setOnClickListener { d.dismiss(); c.onClick() }
                    })
                }
                list.addView(flow, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { bottomMargin = dp(12) })
            }
            s.rows.forEach { r ->
                val row = LinearLayout(act)
                row.orientation = LinearLayout.HORIZONTAL
                row.gravity = Gravity.CENTER_VERTICAL
                row.setPadding(dp(10), dp(10), dp(10), dp(10))
                row.background = GradientDrawable().apply { cornerRadius = dp(16).toFloat(); setColor(if (r.selected) key else Color.TRANSPARENT) }
                row.addView(Radio(act, r.selected, accent, muted), LinearLayout.LayoutParams(dp(22), dp(22)).apply { rightMargin = dp(12) })
                val col = LinearLayout(act)
                col.orientation = LinearLayout.VERTICAL
                col.addView(TextView(act).apply {
                    this.text = r.title
                    setTextColor(if (r.enabled) text else muted)
                    textSize = 14.5f
                    typeface = Typeface.DEFAULT_BOLD
                })
                if (r.subtitle.isNotEmpty()) {
                    col.addView(TextView(act).apply { this.text = r.subtitle; setTextColor(muted); textSize = 12f })
                }
                row.addView(col)
                if (r.enabled) row.setOnClickListener { d.dismiss(); r.onClick() }
                list.addView(row)
            }
        }
        val scroll = ScrollView(act)
        scroll.isVerticalScrollBarEnabled = false
        scroll.addView(list)
        box.addView(scroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        d.setContentView(box)
        d.window?.apply {
            setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
            setGravity(Gravity.BOTTOM)
            attributes = attributes.apply { dimAmount = 0.55f }
            addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND)
            val maxH = (act.resources.displayMetrics.heightPixels * 0.85f).toInt()
            setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            box.measure(
                View.MeasureSpec.makeMeasureSpec(act.resources.displayMetrics.widthPixels, View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED),
            )
            if (box.measuredHeight > maxH) setLayout(ViewGroup.LayoutParams.MATCH_PARENT, maxH)
        }
        d.show()
    }

    /** Кружок выбора: пустой контур или кружок с акцентной точкой внутри. */
    private class Radio(ctx: Context, private val on: Boolean, private val accent: Int, private val muted: Int) : View(ctx) {
        private val p = Paint(Paint.ANTI_ALIAS_FLAG)
        override fun onDraw(c: Canvas) {
            val d = resources.displayMetrics.density
            val cx = width / 2f
            val r = width / 2f - 2f * d
            p.style = Paint.Style.STROKE
            p.strokeWidth = 2f * d
            p.color = if (on) accent else muted
            c.drawCircle(cx, height / 2f, r, p)
            if (on) {
                p.style = Paint.Style.FILL
                c.drawCircle(cx, height / 2f, r * 0.5f, p)
            }
        }
    }

    /** Перенос по строкам для таблеток. */
    private class Flow(ctx: Context, private val hGap: Int, private val vGap: Int) : ViewGroup(ctx) {
        override fun onMeasure(wSpec: Int, hSpec: Int) {
            val maxW = MeasureSpec.getSize(wSpec)
            var x = 0
            var y = 0
            var rowH = 0
            for (i in 0 until childCount) {
                val c = getChildAt(i)
                c.measure(MeasureSpec.makeMeasureSpec(maxW, MeasureSpec.AT_MOST), MeasureSpec.UNSPECIFIED)
                if (x > 0 && x + c.measuredWidth > maxW) { x = 0; y += rowH + vGap; rowH = 0 }
                x += c.measuredWidth + hGap
                rowH = maxOf(rowH, c.measuredHeight)
            }
            setMeasuredDimension(maxW, y + rowH)
        }

        override fun onLayout(changed: Boolean, l: Int, t: Int, r: Int, b: Int) {
            val maxW = r - l
            var x = 0
            var y = 0
            var rowH = 0
            for (i in 0 until childCount) {
                val c = getChildAt(i)
                if (x > 0 && x + c.measuredWidth > maxW) { x = 0; y += rowH + vGap; rowH = 0 }
                c.layout(x, y, x + c.measuredWidth, y + c.measuredHeight)
                x += c.measuredWidth + hGap
                rowH = maxOf(rowH, c.measuredHeight)
            }
        }
    }
}
