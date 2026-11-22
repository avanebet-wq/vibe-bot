package app.claudepocket

import android.content.Context
import org.json.JSONArray

/** Список моделей: берётся из самого Claude Code (и обновляется при каждом запуске), до этого — встроенная копия. */
object ModelCatalog {

    class Model(
        val value: String,
        val resolved: String,
        val name: String,
        val description: String,
        val efforts: List<String>,
    )

    private var cache: List<Model>? = null

    fun all(ctx: Context): List<Model> {
        cache?.let { return it }
        val paths = Paths(ctx.applicationContext)
        val json = paths.modelsJson ?: ctx.assets.open("models.json").bufferedReader().use { it.readText() }
        val list = try { parse(json) } catch (_: Exception) { emptyList() }
        val result = if (list.isEmpty()) parse(ctx.assets.open("models.json").bufferedReader().use { it.readText() }) else list
        cache = result
        return result
    }

    /** Принимает свежий список от Claude Code и запоминает его. */
    fun update(ctx: Context, models: JSONArray) {
        val json = models.toString()
        val list = try { parse(json) } catch (_: Exception) { return }
        if (list.isEmpty()) return
        Paths(ctx.applicationContext).modelsJson = json
        cache = list
    }

    private fun parse(json: String): List<Model> {
        val arr = JSONArray(json)
        return (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            val levels = o.optJSONArray("supportedEffortLevels")
            Model(
                o.optString("value"),
                o.optString("resolvedModel"),
                o.optString("displayName"),
                o.optString("description"),
                if (o.optBoolean("supportsEffort") && levels != null) (0 until levels.length()).map { levels.getString(it) } else emptyList(),
            )
        }
    }

    /** Модель по сохранённому значению ("" — по умолчанию). */
    fun find(ctx: Context, value: String): Model? {
        val v = value.ifEmpty { "default" }
        return all(ctx).firstOrNull { it.value == v }
    }

    /** Короткое имя для кнопки: для «По умолчанию» подставляем настоящую модель, а не слово Default. */
    fun label(ctx: Context, value: String): String {
        val m = find(ctx, value) ?: return value.ifEmpty { "По умолчанию" }
        if (m.value != "default") return m.name
        return all(ctx).firstOrNull { it.value != "default" && it.resolved == m.resolved }?.name ?: "По умолчанию"
    }

    fun title(m: Model): String =
        if (m.value == "default") "По умолчанию (рекомендуется)" else m.name

    /** Описание по-русски; незнакомые тексты оставляем как есть. */
    fun descriptionRu(m: Model): String {
        val d = m.description
        return when {
            d.contains("Efficient for routine tasks") -> "Эффективная для повседневных задач"
            d.contains("Best for everyday, complex tasks") -> "Лучшая для сложных повседневных задач"
            d.contains("Most capable for your hardest and longest-running tasks") -> "Самая мощная: для самых сложных и долгих задач"
            d.contains("Fastest for quick answers") -> "Самая быстрая: для быстрых ответов"
            else -> d
        }
    }

    val EFFORT_NAMES = linkedMapOf(
        "low" to "Низкий",
        "medium" to "Средний",
        "high" to "Высокий",
        "xhigh" to "Очень высокий",
        "max" to "Максимум",
    )

    val EFFORT_HINTS = mapOf(
        "low" to "быстрее и дешевле, меньше размышлений",
        "medium" to "баланс скорости и качества",
        "high" to "глубже обдумывает задачу",
        "xhigh" to "очень глубокое мышление для сложного",
        "max" to "ультра: максимум размышлений, дольше и дороже",
    )

    fun effortName(level: String): String = EFFORT_NAMES[level] ?: "Авто"
}
