package app.claudepocket

import android.content.Context
import android.graphics.drawable.Drawable

/** Векторные значки приложения (один стиль: контур, скруглённые концы). Цвет задаётся при создании. */
object Icons {
    fun get(ctx: Context, res: Int, color: Int): Drawable {
        val d = ctx.getDrawable(res)!!.mutate()
        d.setTint(color)
        return d
    }
}
