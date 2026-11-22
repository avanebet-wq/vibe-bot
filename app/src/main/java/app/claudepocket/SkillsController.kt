package app.claudepocket

import android.app.Activity
import android.app.AlertDialog
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView

/** Экран «Навыки»: установленные, установка по ссылке GitHub, удаление, создание через Claude. */
class SkillsController(
    private val act: Activity,
    private val root: View,
    private val onBack: () -> Unit,
    private val onCreateWithClaude: () -> Unit,
    private val onChanged: () -> Unit,
) {
    private val list: LinearLayout = root.findViewById(R.id.skills_list)
    private val urlField: EditText = root.findViewById(R.id.skills_url)
    private val installBtn: Button = root.findViewById(R.id.skills_install)
    private val status: TextView = root.findViewById(R.id.skills_status)
    private val main = Handler(Looper.getMainLooper())
    private var working = false

    init {
        root.findViewById<View>(R.id.skills_back).setOnClickListener { onBack() }
        root.findViewById<View>(R.id.skills_create).setOnClickListener { onCreateWithClaude() }
        installBtn.background = rounded(act.getColor(R.color.accent), dp(28))
        urlField.background = GradientDrawable().apply {
            setColor(act.getColor(R.color.panel))
            cornerRadius = dp(26).toFloat()
            setStroke(dp(1), act.getColor(R.color.line))
        }
        urlField.setPadding(dp(20), dp(14), dp(20), dp(14))
        installBtn.setOnClickListener { install() }
    }

    fun show() {
        status.text = ""
        refresh()
    }

    private fun refresh() {
        list.removeAllViews()
        val skills = Skills.list(act)
        if (skills.isEmpty()) {
            list.addView(text("Навыков пока нет. Вставь ссылку на GitHub выше или попроси Claude создать навык.", act.getColor(R.color.muted), 14f))
            return
        }
        skills.forEach { s ->
            val card = LinearLayout(act)
            card.orientation = LinearLayout.VERTICAL
            card.setPadding(dp(16), dp(12), dp(16), dp(12))
            card.background = GradientDrawable().apply {
                setColor(act.getColor(R.color.panel))
                cornerRadius = dp(20).toFloat()
                setStroke(dp(1), act.getColor(R.color.line))
            }
            val lp = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            lp.bottomMargin = dp(8)
            card.layoutParams = lp
            card.addView(text(s.name, act.getColor(R.color.text), 16f).apply { setTypeface(typeface, Typeface.BOLD) })
            if (s.description.isNotEmpty()) {
                card.addView(text(s.description, act.getColor(R.color.muted), 13f).apply { maxLines = 4; ellipsize = android.text.TextUtils.TruncateAt.END })
            }
            card.addView(text("Удалить", act.getColor(R.color.error), 13f).apply {
                setPadding(0, dp(8), 0, 0)
                setOnClickListener { confirmRemove(s) }
            })
            list.addView(card)
        }
    }

    private fun confirmRemove(s: Skills.Skill) {
        AlertDialog.Builder(act)
            .setTitle("Удалить навык?")
            .setMessage("«${s.name}» будет удалён.")
            .setPositiveButton("Удалить") { _, _ ->
                if (Skills.remove(act, s.dir)) {
                    onChanged()
                    refresh()
                } else {
                    status.text = "Не удалось удалить навык"
                }
            }
            .setNegativeButton("Отмена", null)
            .show()
    }

    private fun install() {
        if (working) return
        val src = Skills.parse(urlField.text.toString())
        if (src == null) {
            status.setTextColor(act.getColor(R.color.error))
            status.text = "Нужна ссылка вида https://github.com/автор/репозиторий (или автор/репозиторий)"
            return
        }
        working = true
        installBtn.isEnabled = false
        installBtn.text = "Устанавливаю…"
        status.setTextColor(act.getColor(R.color.muted))
        status.text = "Скачиваю ${src.url}…"
        Thread {
            val r = Skills.install(act.applicationContext, src) { }
            main.post {
                working = false
                installBtn.isEnabled = true
                installBtn.text = "Установить"
                if (r.error == null) {
                    status.setTextColor(act.getColor(R.color.success))
                    status.text = "Установлено: " + r.installed.joinToString(", ")
                    urlField.setText("")
                    onChanged()
                } else {
                    status.setTextColor(act.getColor(R.color.error))
                    status.text = r.error
                }
                refresh()
            }
        }.start()
    }

    private fun text(t: String, color: Int, size: Float) = TextView(act).apply {
        text = t
        setTextColor(color)
        textSize = size
        setPadding(0, dp(2), 0, dp(2))
    }

    private fun dp(v: Int) = (v * act.resources.displayMetrics.density).toInt()

    private fun rounded(color: Int, radius: Int) = GradientDrawable().apply {
        setColor(color)
        cornerRadius = radius.toFloat()
    }
}
