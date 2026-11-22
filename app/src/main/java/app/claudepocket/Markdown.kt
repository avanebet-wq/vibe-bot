package app.claudepocket

import android.graphics.Typeface
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.BackgroundColorSpan
import android.text.style.RelativeSizeSpan
import android.text.style.StyleSpan
import android.text.style.TypefaceSpan

/** Небольшой разборщик Markdown: заголовки, списки, **жирный**, `код` и блоки кода. */
object Markdown {
    private val HEADING = Regex("^(#{1,4})\\s+(.*)$")
    private val BULLET = Regex("^(\\s*)[-*]\\s+(.*)$")

    fun render(src: String, codeBg: Int): CharSequence {
        val out = SpannableStringBuilder()
        var inCode = false
        val lines = src.split("\n")
        for ((i, line) in lines.withIndex()) {
            if (line.trimStart().startsWith("```")) {
                inCode = !inCode
                continue
            }
            val start = out.length
            if (inCode) {
                out.append(line)
                out.setSpan(TypefaceSpan("monospace"), start, out.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                out.setSpan(BackgroundColorSpan(codeBg), start, out.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            } else {
                val h = HEADING.matchEntire(line)
                val b = BULLET.matchEntire(line)
                when {
                    h != null -> {
                        inline(h.groupValues[2], out)
                        out.setSpan(StyleSpan(Typeface.BOLD), start, out.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                        out.setSpan(RelativeSizeSpan(if (h.groupValues[1].length <= 2) 1.2f else 1.08f), start, out.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                    }
                    b != null -> {
                        out.append(b.groupValues[1]).append("• ")
                        inline(b.groupValues[2], out)
                    }
                    else -> inline(line, out)
                }
            }
            if (i < lines.size - 1) out.append("\n")
        }
        while (out.isNotEmpty() && out[out.length - 1] == '\n') out.delete(out.length - 1, out.length)
        return out
    }

    private fun inline(text: String, out: SpannableStringBuilder) {
        var i = 0
        while (i < text.length) {
            if (text.startsWith("**", i)) {
                val end = text.indexOf("**", i + 2)
                if (end > i + 2) {
                    val s = out.length
                    out.append(text, i + 2, end)
                    out.setSpan(StyleSpan(Typeface.BOLD), s, out.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                    i = end + 2
                    continue
                }
            }
            if (text[i] == '`') {
                val end = text.indexOf('`', i + 1)
                if (end > i + 1) {
                    val s = out.length
                    out.append(text, i + 1, end)
                    out.setSpan(TypefaceSpan("monospace"), s, out.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                    out.setSpan(BackgroundColorSpan(0x33808080), s, out.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                    i = end + 1
                    continue
                }
            }
            out.append(text[i])
            i++
        }
    }
}
