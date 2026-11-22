package app.claudepocket

import android.animation.ValueAnimator
import android.app.Activity
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.os.Build
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.animation.DecelerateInterpolator
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.PopupWindow
import android.widget.TextView
import androidx.core.graphics.ColorUtils

/** Всплывающее меню под круглой стеклянной кнопкой: скруглённая карточка, значок в круге и подпись. */
object GlassMenu {
    class Item(val icon: Int, val title: String, val onClick: () -> Unit)

    fun show(act: Activity, anchor: View, items: List<Item>) {
        val dp = { v: Int -> (v * act.resources.displayMetrics.density).toInt() }
        val panel = act.getColor(R.color.panel)
        val text = act.getColor(R.color.text)
        val accent = act.getColor(R.color.accent)

        val card = LinearLayout(act)
        card.orientation = LinearLayout.VERTICAL
        card.setPadding(dp(6), dp(6), dp(6), dp(6))
        card.background = GradientDrawable().apply {
            setColor(ColorUtils.setAlphaComponent(panel, 240))
            cornerRadius = dp(26).toFloat()
            setStroke(dp(1), ColorUtils.setAlphaComponent(text, 46))
        }
        card.elevation = dp(16).toFloat()

        val popup = PopupWindow(card, ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT, true)
        popup.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
        popup.elevation = dp(16).toFloat()

        items.forEach { it0 ->
            val row = LinearLayout(act)
            row.orientation = LinearLayout.HORIZONTAL
            row.gravity = Gravity.CENTER_VERTICAL
            row.minimumHeight = dp(56)
            row.setPadding(dp(10), dp(8), dp(22), dp(8))
            val mask = GradientDrawable().apply { setColor(Color.WHITE); cornerRadius = dp(20).toFloat() }
            row.background = RippleDrawable(ColorStateList.valueOf(ColorUtils.setAlphaComponent(accent, 70)), null, mask)

            val ic = ImageView(act)
            ic.setImageDrawable(Icons.get(act, it0.icon, text))
            ic.setBackgroundResource(R.drawable.bg_circle_glass)
            ic.setPadding(dp(9), dp(9), dp(9), dp(9))
            row.addView(ic, LinearLayout.LayoutParams(dp(40), dp(40)).apply { rightMargin = dp(14) })

            row.addView(TextView(act).apply {
                this.text = it0.title
                setTextColor(text)
                textSize = 17f
                typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
            })
            row.setOnClickListener { popup.dismiss(); it0.onClick() }
            card.addView(row)
        }

        card.measure(View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED), View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED))
        val w = maxOf(card.measuredWidth, dp(200))
        popup.width = w
        // правый край меню совпадает с правым краем кнопки
        popup.showAsDropDown(anchor, anchor.width - w, dp(6))

        val animate = if (Build.VERSION.SDK_INT >= 26) ValueAnimator.areAnimatorsEnabled() else true
        if (animate) {
            card.pivotX = w.toFloat()
            card.pivotY = 0f
            card.scaleX = 0.82f
            card.scaleY = 0.82f
            card.alpha = 0f
            card.animate().scaleX(1f).scaleY(1f).alpha(1f).setDuration(170).setInterpolator(DecelerateInterpolator()).start()
        }
    }
}
