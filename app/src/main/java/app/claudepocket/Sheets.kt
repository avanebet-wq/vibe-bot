package app.claudepocket

import android.app.Activity
import android.app.Dialog
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/** Нижние «листы» выбора модели и уровня мышления в стиле карточек приложения. */
object Sheets {

    class Row(val title: String, val subtitle: String, val selected: Boolean, val enabled: Boolean = true, val onClick: () -> Unit)

    fun show(act: Activity, heading: String, rows: List<Row>) {
        val d = Dialog(act)
        d.requestWindowFeature(Window.FEATURE_NO_TITLE)
        val dp = { v: Int -> (v * act.resources.displayMetrics.density).toInt() }

        val box = LinearLayout(act)
        box.orientation = LinearLayout.VERTICAL
        box.setPadding(dp(8), dp(14), dp(8), dp(14))
        box.background = GradientDrawable().apply {
            setColor(act.getColor(R.color.panel))
            cornerRadii = floatArrayOf(dp(24).toFloat(), dp(24).toFloat(), dp(24).toFloat(), dp(24).toFloat(), 0f, 0f, 0f, 0f)
        }
        box.addView(TextView(act).apply {
            text = heading
            setTextColor(act.getColor(R.color.text))
            textSize = 17f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setPadding(dp(14), dp(4), dp(14), dp(10))
        })

        val list = LinearLayout(act)
        list.orientation = LinearLayout.VERTICAL
        rows.forEach { r ->
            val row = LinearLayout(act)
            row.orientation = LinearLayout.HORIZONTAL
            row.gravity = Gravity.CENTER_VERTICAL
            row.setPadding(dp(14), dp(10), dp(14), dp(10))
            row.background = GradientDrawable().apply {
                cornerRadius = dp(14).toFloat()
                setColor(if (r.selected) act.getColor(R.color.key) else Color.TRANSPARENT)
            }
            val col = LinearLayout(act)
            col.orientation = LinearLayout.VERTICAL
            col.addView(TextView(act).apply {
                text = r.title
                setTextColor(if (r.enabled) act.getColor(R.color.text) else act.getColor(R.color.muted))
                textSize = 16f
                setTypeface(typeface, android.graphics.Typeface.BOLD)
            })
            if (r.subtitle.isNotEmpty()) {
                col.addView(TextView(act).apply {
                    text = r.subtitle
                    setTextColor(act.getColor(R.color.muted))
                    textSize = 13f
                })
            }
            row.addView(col, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            row.addView(TextView(act).apply {
                text = if (r.selected) "✓" else ""
                setTextColor(act.getColor(R.color.accent))
                textSize = 20f
            })
            if (r.enabled) row.setOnClickListener { d.dismiss(); r.onClick() }
            list.addView(row)
        }
        val scroll = ScrollView(act)
        scroll.addView(list)
        box.addView(scroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        d.setContentView(box)
        d.window?.apply {
            setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
            setGravity(Gravity.BOTTOM)
            val maxH = (act.resources.displayMetrics.heightPixels * 0.78f).toInt()
            setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            attributes = attributes.apply { dimAmount = 0.5f }
            addFlags(android.view.WindowManager.LayoutParams.FLAG_DIM_BEHIND)
            box.measure(
                View.MeasureSpec.makeMeasureSpec(act.resources.displayMetrics.widthPixels, View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED),
            )
            if (box.measuredHeight > maxH) setLayout(ViewGroup.LayoutParams.MATCH_PARENT, maxH)
        }
        d.show()
    }
}
