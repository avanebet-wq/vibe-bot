package app.claudepocket

import android.Manifest
import android.app.Activity
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import android.view.View
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Первоначальная настройка: квадратные карточки со скруглёнными углами, на каждой — одно разрешение
 * с пояснением, зачем оно нужно. После прохождения приложение больше ничего не просит.
 */
class Onboarding(
    private val act: Activity,
    private val panel: View,
    private val onDone: () -> Unit,
) {
    private enum class Badge(val label: String, val colorRes: Int) {
        REQUIRED("ОБЯЗАТЕЛЬНО", R.color.badge_red),
        RECOMMENDED("РЕКОМЕНДУЕТСЯ", R.color.accent),
        NONE("", R.color.accent),
    }

    private class Slide(
        val icon: Int,
        val title: String,
        val text: String,
        val badge: Badge,
        val primary: String,
        val granted: (() -> Boolean)? = null,
        val request: (() -> Unit)? = null,
    )

    private val card: FrameLayout = panel.findViewById(R.id.onb_card)
    private val dots: LinearLayout = panel.findViewById(R.id.onb_dots)
    private val emoji: android.widget.ImageView = panel.findViewById(R.id.onb_emoji)
    private val badge: TextView = panel.findViewById(R.id.onb_badge)
    private val title: TextView = panel.findViewById(R.id.onb_title)
    private val text: TextView = panel.findViewById(R.id.onb_text)
    private val state: TextView = panel.findViewById(R.id.onb_state)
    private val primary: Button = panel.findViewById(R.id.onb_primary)
    private val secondary: TextView = panel.findViewById(R.id.onb_secondary)

    private val slides = listOf(
        Slide(
            R.drawable.ic_spark, "Давай настроим", "Несколько секунд — и приложение больше ни о чём не попросит. Нажимай «Разрешить», а не нужное можно пропустить.",
            Badge.NONE, "Начать",
        ),
        Slide(
            R.drawable.ic_bell, "Уведомления",
            "Пока Claude работает, Android показывает одно постоянное уведомление. Без него система может остановить работу в фоне.",
            Badge.REQUIRED, "Разрешить", ::notificationsGranted, ::requestNotifications,
        ),
        Slide(
            R.drawable.ic_bolt, "Работа в фоне",
            "Чтобы Android не «усыплял» приложение: задачи Claude и вход в аккаунт не прервутся, когда ты свернёшь экран.",
            Badge.REQUIRED, "Разрешить", ::batteryGranted, ::requestBattery,
        ),
        Slide(
            R.drawable.ic_folder, "Память телефона",
            "Claude сможет читать и сохранять файлы в папке /sdcard: фото, документы, загрузки. Для работы только в ~/work не нужно.",
            Badge.RECOMMENDED, "Разрешить", ::storageGranted, ::requestStorage,
        ),
        Slide(
            R.drawable.ic_check, "Всё готово", "Больше разрешений мы просить не будем. Дальше — установка Claude Code в одно нажатие.",
            Badge.NONE, "Продолжить",
        ),
    )

    private var index = 0
    private var attempted = BooleanArray(slides.size)
    private var animating = false

    fun show() {
        panel.visibility = View.VISIBLE
        // квадрат: сторона зависит от экрана, но не больше 380dp
        val dm = act.resources.displayMetrics
        val side = minOf(dm.widthPixels - dp(40), (dm.heightPixels * 0.6f).toInt(), dp(380))
        card.layoutParams = LinearLayout.LayoutParams(side, side)
        card.background = GradientDrawable().apply {
            setColor(act.getColor(R.color.panel))
            cornerRadius = dp(28).toFloat()
            setStroke(dp(1), act.getColor(R.color.key))
        }
        primary.background = rounded(act.getColor(R.color.accent), dp(24))
        breathe()
        primary.setOnClickListener { onPrimary() }
        secondary.setOnClickListener { next() }
        bind()
    }

    /** Значок на карточке «дышит» — мягко пульсирует (отключается системной настройкой анимации). */
    private fun breathe() {
        val on = if (android.os.Build.VERSION.SDK_INT >= 26) android.animation.ValueAnimator.areAnimatorsEnabled() else true
        if (!on) return
        android.animation.ObjectAnimator.ofPropertyValuesHolder(
            emoji,
            android.animation.PropertyValuesHolder.ofFloat(View.SCALE_X, 1f, 1.1f),
            android.animation.PropertyValuesHolder.ofFloat(View.SCALE_Y, 1f, 1.1f),
        ).apply {
            duration = 1600
            repeatCount = android.animation.ValueAnimator.INFINITE
            repeatMode = android.animation.ValueAnimator.REVERSE
            start()
        }
    }

    val isVisible: Boolean get() = panel.visibility == View.VISIBLE

    /** Вызывается при возвращении из системного окна/настроек — обновляет состояние кнопок. */
    fun refresh() {
        if (isVisible) bind()
    }

    // ------------------------------------------------------------------ logic

    private fun onPrimary() {
        val s = slides[index]
        val g = s.granted
        when {
            g == null -> next()
            g() -> next()
            else -> {
                attempted[index] = true
                // повторное нажатие, если система уже не показывает окно, ведёт в настройки
                if (isPermanentlyDenied(index)) openAppSettings() else s.request?.invoke()
                bind()
            }
        }
    }

    private fun isPermanentlyDenied(i: Int): Boolean = attempted[i] && when (i) {
        1 -> Build.VERSION.SDK_INT >= 33 && !act.shouldShowRequestPermissionRationale(Manifest.permission.POST_NOTIFICATIONS) && attemptsMade[i] >= 1
        3 -> !act.shouldShowRequestPermissionRationale(Manifest.permission.READ_EXTERNAL_STORAGE) && attemptsMade[i] >= 1
        else -> false
    }

    private val attemptsMade = IntArray(slides.size)

    private fun next() {
        if (index >= slides.lastIndex) {
            finish()
            return
        }
        slide(+1)
    }

    private fun finish() {
        Paths(act).onboardingDone = true
        panel.visibility = View.GONE
        onDone()
    }

    private fun slide(dir: Int) {
        if (animating) return
        animating = true
        val w = card.width.toFloat()
        card.animate().translationX(-dir * w).alpha(0f).setDuration(180).withEndAction {
            index += dir
            bind()
            card.translationX = dir * w
            card.animate().translationX(0f).alpha(1f).setDuration(220).withEndAction { animating = false }.start()
        }.start()
    }

    private fun bind() {
        val s = slides[index]
        emoji.setImageDrawable(Icons.get(act, s.icon, act.getColor(R.color.accent)))
        emoji.background = GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(androidx.core.graphics.ColorUtils.setAlphaComponent(act.getColor(R.color.accent), 46))
        }
        title.text = s.title
        text.text = s.text
        if (s.badge == Badge.NONE) {
            badge.visibility = View.GONE
        } else {
            badge.visibility = View.VISIBLE
            badge.text = s.badge.label
            badge.background = rounded(act.getColor(s.badge.colorRes), dp(8))
        }
        val g = s.granted
        val ok = g?.invoke() == true
        state.text = when {
            g == null -> ""
            ok -> "✓ Разрешение выдано"
            attempted[index] -> "Разрешение пока не выдано"
            else -> ""
        }
        state.setTextColor(act.getColor(if (ok) R.color.success else R.color.warn))
        primary.text = when {
            g == null -> s.primary
            ok -> "Далее"
            isPermanentlyDenied(index) -> "Открыть настройки"
            else -> s.primary
        }
        secondary.visibility = when {
            g == null || ok -> View.GONE
            s.badge == Badge.RECOMMENDED -> View.VISIBLE
            attempted[index] -> View.VISIBLE // обязательное, но человек отказался — не запираем его
            else -> View.GONE
        }
        secondary.text = if (s.badge == Badge.RECOMMENDED) "Пропустить" else "Продолжить без этого"
        // точки прогресса
        dots.removeAllViews()
        for (i in slides.indices) {
            val d = View(act)
            val size = dp(if (i == index) 18 else 8)
            d.layoutParams = LinearLayout.LayoutParams(size, dp(8)).apply { setMargins(dp(3), 0, dp(3), 0) }
            d.background = rounded(if (i == index) act.getColor(R.color.accent) else act.getColor(R.color.key), dp(4))
            dots.addView(d)
        }
    }

    // ------------------------------------------------------------------ permissions

    private fun notificationsGranted(): Boolean =
        (act.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).areNotificationsEnabled()

    private fun requestNotifications() {
        attemptsMade[1]++
        KeepAliveService.ensureChannel(act) // на Android 13+ создание канала вызывает системное окно
        if (Build.VERSION.SDK_INT >= 33) {
            act.requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIFICATIONS)
        } else {
            // до Android 13 окна нет: уведомления можно только включить в настройках приложения
            try {
                act.startActivity(
                    Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, act.packageName),
                )
            } catch (_: Exception) {
                openAppSettings()
            }
        }
    }

    private fun batteryGranted(): Boolean =
        (act.getSystemService(Context.POWER_SERVICE) as PowerManager).isIgnoringBatteryOptimizations(act.packageName)

    private fun requestBattery() {
        try {
            act.startActivity(
                Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + act.packageName)),
            )
        } catch (e: Exception) {
            try {
                act.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
            } catch (_: Exception) {
                openAppSettings()
            }
        }
    }

    private fun storageGranted(): Boolean =
        act.checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE) == PackageManager.PERMISSION_GRANTED

    private fun requestStorage() {
        attemptsMade[3]++
        act.requestPermissions(
            arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE, Manifest.permission.WRITE_EXTERNAL_STORAGE),
            REQ_STORAGE_ONB,
        )
    }

    private fun openAppSettings() {
        try {
            act.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + act.packageName)))
        } catch (_: Exception) {
        }
    }

    // ------------------------------------------------------------------ helpers

    private fun dp(v: Int) = (v * act.resources.displayMetrics.density).toInt()

    private fun rounded(color: Int, radius: Int) = GradientDrawable().apply {
        setColor(color)
        cornerRadius = radius.toFloat()
    }

    companion object {
        const val REQ_NOTIFICATIONS = 11
        const val REQ_STORAGE_ONB = 12
    }
}
