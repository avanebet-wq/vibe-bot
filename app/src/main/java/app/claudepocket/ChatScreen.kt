package app.claudepocket

import android.app.Activity
import android.content.Context
import android.graphics.BitmapFactory
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Build
import android.os.SystemClock
import android.text.Spanned
import android.text.style.BackgroundColorSpan
import android.text.style.RelativeSizeSpan
import android.text.style.StyleSpan
import android.text.style.TypefaceSpan
import android.text.style.URLSpan
import android.text.util.Linkify
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.VectorConverter
import androidx.compose.animation.core.VisibilityThreshold
import androidx.compose.animation.core.spring
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.State
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.Shader
import androidx.compose.ui.graphics.ShaderBrush
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.util.lerp
import com.kyant.backdrop.Backdrop
import com.kyant.backdrop.backdrops.layerBackdrop
import com.kyant.backdrop.backdrops.rememberLayerBackdrop
import com.kyant.backdrop.drawBackdrop
import com.kyant.backdrop.effects.blur
import com.kyant.backdrop.effects.lens
import com.kyant.backdrop.effects.vibrancy
import com.kyant.backdrop.highlight.Highlight
import com.kyant.backdrop.highlight.HighlightStyle
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.sin
import kotlin.math.tanh

/*
 * Экран чата на Jetpack Compose. Стекло рисует библиотека Backdrop (Kyant, Apache-2.0):
 * https://github.com/Kyant0/AndroidLiquidGlass. Рецепт кнопки (линза, размытие, насыщенность,
 * подсветка края, упругая реакция на касание) повторяет её пример LiquidButton.
 */

/** Состояние, которое контроллер передаёт в Compose. */
class ChatUi {
    var tick by mutableIntStateOf(0)
    var busy by mutableStateOf(false)
    var forceScroll by mutableIntStateOf(0)
    val attachments = mutableStateListOf<Files.Attachment>()
    var draft by mutableStateOf(TextFieldValue(""))
    var focusTick by mutableIntStateOf(0)
    var blurTick by mutableIntStateOf(0)
}

class Pal(ctx: Context) {
    val bg = Color(ctx.getColor(R.color.bg))
    val panel = Color(ctx.getColor(R.color.panel))
    val key = Color(ctx.getColor(R.color.key))
    val line = Color(ctx.getColor(R.color.line))
    val accent = Color(ctx.getColor(R.color.accent))
    val onAccent = Color(ctx.getColor(R.color.on_accent))
    val text = Color(ctx.getColor(R.color.text))
    val muted = Color(ctx.getColor(R.color.muted))
    val error = Color(ctx.getColor(R.color.error))
    val success = Color(ctx.getColor(R.color.success))
}

private val Inter = FontFamily(
    Font(R.font.inter_regular, FontWeight.Normal),
    Font(R.font.inter_medium, FontWeight.Medium),
    Font(R.font.inter_semibold, FontWeight.SemiBold),
    Font(R.font.inter_bold, FontWeight.Bold),
)

private fun ts(size: Float, color: Color, weight: FontWeight = FontWeight.Normal, mono: Boolean = false) = TextStyle(
    fontFamily = if (mono) FontFamily.Monospace else Inter,
    fontSize = size.sp,
    color = color,
    fontWeight = weight,
    lineHeight = (size * 1.32f).sp,
)

// ---------------------------------------------------------------------------------------------
// Текст «что делает Claude»

private fun short(s: String, n: Int) = s.replace('\n', ' ').let { if (it.length > n) it.take(n) + "…" else it }

private fun describe(name: String, input: JSONObject): Pair<String, String> {
    fun s(k: String) = input.optString(k)
    return when (name) {
        "Bash" -> "Выполняет команду" to s("command")
        "Write" -> "Создаёт файл" to s("file_path")
        "Edit", "MultiEdit" -> "Правит файл" to s("file_path")
        "NotebookEdit" -> "Правит блокнот" to s("notebook_path")
        "Read" -> "Читает файл" to s("file_path")
        "Glob" -> "Ищет файлы" to s("pattern")
        "Grep" -> "Ищет в файлах" to s("pattern")
        "WebFetch" -> "Открывает страницу" to s("url")
        "WebSearch" -> "Ищет в интернете" to s("query")
        "Task", "Agent" -> "Запускает помощника" to s("description")
        else -> name to ""
    }
}

private fun live(name: String, input: JSONObject): String {
    fun file(k: String) = short(input.optString(k).substringAfterLast('/'), 40)
    fun arg(k: String) = short(input.optString(k), 50)
    fun with(base: String, d: String) = if (d.isEmpty()) base else "$base $d"
    return when (name) {
        "Bash" -> with("Выполняет команду:", arg("command")).trimEnd(':', ' ')
        "Write" -> with("Создаёт файл", file("file_path"))
        "Edit", "MultiEdit" -> with("Правит файл", file("file_path"))
        "NotebookEdit" -> with("Правит блокнот", file("notebook_path"))
        "Read" -> with("Читает файл", file("file_path"))
        "Glob" -> with("Ищет файлы:", arg("pattern")).trimEnd(':', ' ')
        "Grep" -> with("Ищет в файлах:", arg("pattern")).trimEnd(':', ' ')
        "WebFetch" -> with("Открывает страницу", arg("url"))
        "WebSearch" -> with("Ищет в интернете:", arg("query")).trimEnd(':', ' ')
        "Task", "Agent" -> with("Запускает помощника:", arg("description")).trimEnd(':', ' ')
        "TodoWrite" -> "Обновляет план"
        else -> name
    }
}

private fun currentActivity(): String {
    val msgs = ChatEngine.messages
    val last = msgs.lastOrNull()
    if (last is ChatEngine.Msg.Shell && last.state == ChatEngine.RUNNING) return "Команда: " + short(last.cmd, 60)
    val perm = msgs.filterIsInstance<ChatEngine.Msg.Permission>().lastOrNull { it.decision == ChatEngine.PENDING }
    if (perm != null) return "Нужно твоё разрешение: " + live(perm.tool, perm.input).lowercase()
    val running = msgs.filterIsInstance<ChatEngine.Msg.Tool>().lastOrNull { it.state == ChatEngine.RUNNING }
    if (running != null) return live(running.name, running.input)
    val ph = ChatEngine.phase
    return when {
        ph == "thinking" -> "Размышляет над задачей"
        ph == "writing" -> "Пишет ответ"
        ph.startsWith("prep:") -> "Готовит действие: " + live(ph.removePrefix("prep:"), JSONObject()).lowercase()
        ph == "after_tool" -> "Разбирает результат"
        else -> "Читает запрос"
    }
}

private fun statusWord(): String {
    val last = ChatEngine.messages.lastOrNull()
    return when {
        last is ChatEngine.Msg.Shell && last.state == ChatEngine.RUNNING -> "Выполняется"
        last is ChatEngine.Msg.Permission && last.decision == ChatEngine.PENDING -> "Ждёт твоего ответа"
        else -> "Думает"
    }
}

private fun isRunningBash(): Boolean {
    val last = ChatEngine.messages.lastOrNull()
    return (last is ChatEngine.Msg.Shell && last.state == ChatEngine.RUNNING) ||
        ChatEngine.messages.filterIsInstance<ChatEngine.Msg.Tool>().lastOrNull { it.state == ChatEngine.RUNNING }?.name == "Bash"
}

// ---------------------------------------------------------------------------------------------
// Markdown (Spanned) -> AnnotatedString

private fun toAnnotated(src: String, pal: Pal): AnnotatedString {
    val sp = android.text.SpannableStringBuilder(Markdown.render(Files.strip(src), 0x33808080))
    Linkify.addLinks(sp, Linkify.WEB_URLS)
    return buildAnnotatedString {
        append(sp.toString())
        for (span in sp.getSpans(0, sp.length, Any::class.java)) {
            val s = sp.getSpanStart(span)
            val e = sp.getSpanEnd(span)
            if (s < 0 || e <= s) continue
            when (span) {
                is StyleSpan -> addStyle(
                    SpanStyle(
                        fontWeight = if (span.style and android.graphics.Typeface.BOLD != 0) FontWeight.Bold else null,
                        fontStyle = if (span.style and android.graphics.Typeface.ITALIC != 0) FontStyle.Italic else null,
                    ),
                    s, e,
                )
                is TypefaceSpan -> if (span.family == "monospace") addStyle(SpanStyle(fontFamily = FontFamily.Monospace), s, e)
                is BackgroundColorSpan -> addStyle(SpanStyle(background = Color(span.backgroundColor)), s, e)
                is RelativeSizeSpan -> addStyle(SpanStyle(fontSize = (16f * span.sizeChange).sp), s, e)
                is URLSpan -> addLink(
                    LinkAnnotation.Url(
                        span.url,
                        TextLinkStyles(SpanStyle(color = pal.accent, textDecoration = TextDecoration.Underline)),
                    ),
                    s, e,
                )
            }
        }
    }
}

// ---------------------------------------------------------------------------------------------
// Время кадров (не зависит от системной настройки «Анимация»), датчик наклона

@Composable
private fun rememberFrameTime(): State<Long> {
    val t = remember { mutableLongStateOf(0L) }
    LaunchedEffect(Unit) {
        var last = 0L
        while (true) {
            androidx.compose.runtime.withFrameNanos { now ->
                if (now - last > 30_000_000L) {
                    last = now
                    t.longValue = now
                }
            }
        }
    }
    return t
}

/** Угол блика на краю стекла: меняется от наклона телефона, как у стекла в iOS. */
@Composable
private fun rememberTiltAngle(): State<Float> {
    val ctx = LocalContext.current
    val angle = remember { mutableFloatStateOf(45f) }
    DisposableEffect(Unit) {
        val sm = ctx.getSystemService(Context.SENSOR_SERVICE) as? SensorManager
        val sensor = sm?.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        var smooth = 0f
        val l = object : SensorEventListener {
            override fun onAccuracyChanged(s: Sensor?, a: Int) {}
            override fun onSensorChanged(e: SensorEvent) {
                val roll = (e.values[0] / 9.8f).coerceIn(-1f, 1f)
                smooth += (roll - smooth) * 0.15f
                val a = 45f - smooth * 50f
                if (abs(a - angle.floatValue) > 0.8f) angle.floatValue = a
            }
        }
        if (sm != null && sensor != null) sm.registerListener(l, sensor, SensorManager.SENSOR_DELAY_UI)
        onDispose { sm?.unregisterListener(l) }
    }
    return angle
}

// ---------------------------------------------------------------------------------------------
// Реакция на касание (по мотивам InteractiveHighlight из каталога Kyant)

private const val PRESS_SHADER = """
uniform float2 size;
layout(color) uniform half4 color;
uniform float radius;
uniform float2 position;

half4 main(float2 coord) {
    float dist = distance(coord, position);
    float intensity = smoothstep(radius, radius * 0.5, dist);
    return color * intensity;
}"""

private class Interactive(private val scope: CoroutineScope) {
    private val press = Animatable(0f)
    private val pos = Animatable(Offset.Zero, Offset.VectorConverter)
    private var start = Offset.Zero
    val progress: Float get() = press.value
    val offset: Offset get() = pos.value - start

    private val shader = if (Build.VERSION.SDK_INT >= 33) android.graphics.RuntimeShader(PRESS_SHADER) else null

    val drawModifier: Modifier = Modifier.drawWithContent {
        val p = press.value
        if (p > 0f) {
            if (shader != null && Build.VERSION.SDK_INT >= 33) {
                drawRect(Color.White.copy(0.08f * p), blendMode = androidx.compose.ui.graphics.BlendMode.Plus)
                val position = pos.value
                shader.setFloatUniform("size", size.width, size.height)
                shader.setColorUniform("color", Color.White.copy(0.18f * p).toArgb())
                shader.setFloatUniform("radius", size.minDimension * 1.5f)
                shader.setFloatUniform("position", position.x.coerceIn(0f, size.width), position.y.coerceIn(0f, size.height))
                drawRect(
                    object : ShaderBrush() {
                        override fun createShader(size: Size): Shader = shader
                    },
                    blendMode = androidx.compose.ui.graphics.BlendMode.Plus,
                )
            } else {
                drawRect(Color.White.copy(0.22f * p), blendMode = androidx.compose.ui.graphics.BlendMode.Plus)
            }
        }
        drawContent()
    }

    val gestureModifier: Modifier = Modifier.pointerInput(scope) {
        val snap = spring<Float>(0.5f, 300f, 0.001f)
        val snapPos = spring<Offset>(0.5f, 300f, Offset.VisibilityThreshold)
        awaitEachGesture {
            val down = awaitFirstDown(requireUnconsumed = false, pass = PointerEventPass.Initial)
            start = down.position
            scope.launch {
                launch { press.animateTo(1f, snap) }
                launch { pos.snapTo(start) }
            }
            while (true) {
                val ev = awaitPointerEvent(PointerEventPass.Initial)
                val ch = ev.changes.firstOrNull { it.id == down.id } ?: break
                if (!ch.pressed) break
                scope.launch { pos.snapTo(ch.position) }
            }
            scope.launch {
                launch { press.animateTo(0f, snap) }
                launch { pos.animateTo(start, snapPos) }
            }
        }
    }
}


// ---------------------------------------------------------------------------------------------
// Стекло

/** Прозрачная стеклянная плашка: линза, размытие, насыщенность, подсветка по краю. */
@Composable
private fun GlassBox(
    backdrop: Backdrop,
    shape: Shape,
    surface: Color,
    tilt: State<Float>,
    modifier: Modifier = Modifier,
    rubber: Boolean = true,
    onClick: (() -> Unit)? = null,
    front: (androidx.compose.ui.graphics.drawscope.DrawScope.() -> Unit)? = null,
    content: @Composable BoxScope.() -> Unit,
) {
    val scope = rememberCoroutineScope()
    val inter = remember(scope) { Interactive(scope) }
    Box(
        modifier
            .drawBackdrop(
                backdrop = backdrop,
                shape = { shape },
                effects = {
                    vibrancy()
                    blur(2f.dp.toPx())
                    lens(12f.dp.toPx(), 24f.dp.toPx())
                },
                highlight = { Highlight(style = HighlightStyle.Default(angle = tilt.value)) },
                layerBlock = {
                    val w = size.width
                    val h = size.height
                    val p = inter.progress
                    val scale = lerp(1f, 1f + 5f.dp.toPx() / size.height, p)
                    if (rubber) {
                        val maxOffset = size.minDimension
                        val off = inter.offset
                        translationX = maxOffset * tanh(0.05f * off.x / maxOffset)
                        translationY = maxOffset * tanh(0.05f * off.y / maxOffset)
                        val maxDrag = 4f.dp.toPx() / size.height
                        val ang = atan2(off.y, off.x)
                        scaleX = scale + maxDrag * abs(cos(ang) * off.x / size.maxDimension) * (w / h).coerceAtMost(1f)
                        scaleY = scale + maxDrag * abs(sin(ang) * off.y / size.maxDimension) * (h / w).coerceAtMost(1f)
                    } else {
                        scaleX = scale
                        scaleY = scale
                    }
                },
                onDrawSurface = { drawRect(surface) },
                onDrawFront = front,
            )
            .then(if (onClick != null) Modifier.clickable(interactionSource = null, indication = null, onClick = onClick) else Modifier)
            .then(inter.drawModifier)
            .then(inter.gestureModifier),
        content = content,
    )
}

@Composable
private fun Icon(res: Int, tint: Color, size: Int, modifier: Modifier = Modifier) {
    Image(painterResource(res), null, modifier.size(size.dp), colorFilter = ColorFilter.tint(tint))
}

// ---------------------------------------------------------------------------------------------
// Экран

@Composable
fun ChatScreen(
    ui: ChatUi,
    act: Activity,
    onMenu: () -> Unit,
    onModel: () -> Unit,
    onPlus: () -> Unit,
    onFile: (ChatEngine.Msg.File, String) -> Unit,
    onSend: () -> Unit,
) {
    val pal = Pal(act)
    val dark = isSystemInDarkTheme()
    val backdrop = rememberLayerBackdrop()
    val listState = rememberLazyListState()
    val frame = rememberFrameTime()
    val tilt = rememberTiltAngle()
    var composerH by remember { mutableIntStateOf(0) }
    val density = androidx.compose.ui.platform.LocalDensity.current
    val surface = if (dark) Color.White.copy(0.07f) else Color.White.copy(0.28f)

    // держим конец переписки над полем ввода, если пользователь был внизу
    var handled by remember { mutableIntStateOf(0) }
    LaunchedEffect(ui.tick, ui.forceScroll, composerH) {
        val force = ui.forceScroll != handled
        handled = ui.forceScroll
        val li = listState.layoutInfo
        val last = li.visibleItemsInfo.lastOrNull()
        val atEnd = last == null || (last.index >= li.totalItemsCount - 1 && last.offset + last.size - li.viewportEndOffset < 200)
        if (atEnd || force) {
            androidx.compose.runtime.withFrameNanos { }
            listState.scrollBy(1_000_000f)
        }
    }

    Box(Modifier.fillMaxSize().background(pal.bg)) {
        Box(Modifier.fillMaxSize().layerBackdrop(backdrop)) {
            Aurora(pal, dark, frame) { listState.firstVisibleItemIndex * 900f + listState.firstVisibleItemScrollOffset }
            MessageList(ui, pal, listState, with(density) { composerH.toDp() }, onFile)
        }
        // мягкий верх: лежит поверх, но вне слоя backdrop, поэтому стекло видит текст без затухания
        Box(
            Modifier.fillMaxWidth().height(40.dp).align(Alignment.TopCenter)
                .background(Brush.verticalGradient(listOf(pal.bg, pal.bg.copy(alpha = 0f)))),
        )
        GlassBox(
            backdrop, CircleShape, surface, tilt,
            Modifier.align(Alignment.TopEnd).padding(top = 10.dp, end = 14.dp).size(48.dp),
            onClick = onMenu,
        ) {
            Icon(R.drawable.ic_user, pal.text, 22, Modifier.align(Alignment.Center))
        }
        Composer(ui, pal, backdrop, surface, tilt, frame, onModel, onPlus, onSend, Modifier.align(Alignment.BottomCenter).onSizeChanged { composerH = it.height })
    }
}

@Composable
private fun Aurora(pal: Pal, dark: Boolean, frame: State<Long>, scroll: () -> Float) {
    val hsv = remember(pal.accent) {
        val a = FloatArray(3)
        android.graphics.Color.colorToHSV(pal.accent.toArgb(), a)
        a
    }
    Canvas(Modifier.fillMaxSize()) {
        drawRect(pal.bg)
        val t = frame.value / 1_000_000_000.0
        val sc = scroll()
        val shifts = floatArrayOf(0f, 48f, -52f)
        for (i in 0..2) {
            val col = Color(android.graphics.Color.HSVToColor(if (dark) 120 else 96, floatArrayOf((hsv[0] + shifts[i] + 360f) % 360f, max(hsv[1], 0.55f), if (dark) 0.85f else 0.95f)))
            val k = i + 1
            val x = size.width * (0.5 + 0.42 * sin(t * 0.26 * k + i * 2.1)).toFloat()
            val span = size.height * 1.5f
            val y0 = size.height * (0.5 + 0.3 * cos(t * 0.2 * k + i * 1.3)).toFloat() - sc * (0.25f + 0.1f * i)
            val y = ((y0 % span) + span) % span - size.height * 0.25f
            val r = max(size.width, size.height) * (if (i == 0) 0.62f else 0.5f)
            drawCircle(Brush.radialGradient(listOf(col, Color.Transparent), center = Offset(x, y), radius = r), radius = r, center = Offset(x, y))
        }
    }
}

@Composable
private fun MessageList(ui: ChatUi, pal: Pal, state: androidx.compose.foundation.lazy.LazyListState, composerH: androidx.compose.ui.unit.Dp, onFile: (ChatEngine.Msg.File, String) -> Unit) {
    val tick = ui.tick
    val msgs = remember(tick) { ArrayList(ChatEngine.messages) }
    LazyColumn(
        state = state,
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 64.dp, bottom = composerH + 10.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        if (msgs.isEmpty()) {
            item(key = "welcome") {
                BasicText(
                    "Привет! Я Claude Code.\n\nНапиши, что нужно сделать, например:\n• «Создай страницу-визитку и открой её»\n• «Объясни, что в этой папке»\n• «Исправь ошибку в main.py»\n\nЧтобы выполнить команду напрямую, начни сообщение с «!», например: !ls или !apt install nano\n\nФайлы создаются в папке ~/work.",
                    Modifier.fillMaxWidth().padding(16.dp, 20.dp, 16.dp, 8.dp),
                    style = ts(15f, pal.muted).copy(textAlign = androidx.compose.ui.text.style.TextAlign.Center),
                )
            }
        }
        items(msgs.size, key = { System.identityHashCode(msgs[it]) }) { i ->
            MsgItem(msgs[i], tick, pal, onFile)
        }
    }
}

@Composable
private fun MsgItem(m: ChatEngine.Msg, @Suppress("UNUSED_PARAMETER") tick: Int, pal: Pal, onFile: (ChatEngine.Msg.File, String) -> Unit) {
    when (m) {
        is ChatEngine.Msg.User -> Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            Box(
                Modifier.padding(start = 48.dp)
                    .background(pal.accent, RoundedCornerShape(20.dp, 20.dp, 6.dp, 20.dp))
                    .padding(14.dp, 10.dp),
            ) { SelectionContainer { BasicText(m.text, style = ts(16f, pal.onAccent)) } }
        }
        is ChatEngine.Msg.Assistant -> {
            val text = remember(m.text) { toAnnotated(m.text, pal) }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Start) {
                Box(
                    Modifier.padding(end = 28.dp)
                        .background(pal.panel, RoundedCornerShape(20.dp, 20.dp, 20.dp, 6.dp))
                        .padding(14.dp, 10.dp),
                ) { SelectionContainer { BasicText(text, style = ts(16f, pal.text)) } }
            }
        }
        is ChatEngine.Msg.Tool -> Card(pal) {
            val (title, detail) = describe(m.name, m.input)
            val mark = when (m.state) { ChatEngine.OK -> "✓ "; ChatEngine.FAILED -> "✗ "; else -> "… " }
            Lbl(mark + title, if (m.state == ChatEngine.FAILED) pal.error else pal.muted, 14f, bold = true)
            if (detail.isNotEmpty()) Lbl(detail, pal.muted, 13f, mono = true)
        }
        is ChatEngine.Msg.Info -> Card(pal) { Lbl(m.text, if (m.error) pal.error else pal.muted, 14f) }
        is ChatEngine.Msg.Shell -> Card(pal) {
            val head = when (m.state) { ChatEngine.OK -> "✓ "; ChatEngine.FAILED -> "✗ "; else -> "… " }
            Lbl(head + m.cmd, pal.text, 14f, bold = true, mono = true)
            if (m.output.isNotEmpty()) {
                SelectionContainer {
                    BasicText(
                        if (m.output.length > 4000) "…\n" + m.output.takeLast(4000) else m.output,
                        Modifier.padding(top = 4.dp, bottom = 2.dp),
                        style = ts(12f, pal.muted, mono = true),
                    )
                }
            }
            if (m.state == ChatEngine.FAILED && m.exit != 0) Lbl("Код завершения: ${m.exit}", pal.error, 12f)
        }
        is ChatEngine.Msg.Permission -> Card(pal, accentBorder = true) {
            val (title, detail) = describe(m.tool, m.input)
            Lbl("Claude просит разрешения:\n$title", pal.text, 15f, bold = true)
            if (detail.isNotEmpty()) Lbl(detail, pal.muted, 13f, mono = true)
            when (m.decision) {
                ChatEngine.ALLOWED -> Lbl("✓ Разрешено", pal.success, 14f)
                ChatEngine.DENIED -> Lbl("✗ Отклонено", pal.error, 14f)
                else -> {
                    Row(Modifier.padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Pill("Разрешить", true, pal, Modifier.weight(1f)) { ChatEngine.respond(m, true) }
                        Pill("Отклонить", false, pal, Modifier.weight(1f)) { ChatEngine.respond(m, false) }
                    }
                    if (m.suggestion != null) {
                        Pill("Разрешать правки файлов без вопросов", false, pal, Modifier.padding(top = 8.dp).fillMaxWidth()) {
                            ChatEngine.respond(m, true, rememberEdits = true)
                        }
                    }
                }
            }
        }
        is ChatEngine.Msg.File -> FileCard(m, pal, onFile)
    }
}

@Composable
private fun Card(pal: Pal, accentBorder: Boolean = false, content: @Composable () -> Unit) {
    Column(
        Modifier.fillMaxWidth()
            .background(pal.panel, RoundedCornerShape(18.dp))
            .then(if (accentBorder) Modifier.border(1.dp, pal.accent.copy(alpha = 0.55f), RoundedCornerShape(18.dp)) else Modifier)
            .padding(14.dp, 12.dp),
    ) { content() }
}

@Composable
private fun Lbl(text: String, color: Color, size: Float, bold: Boolean = false, mono: Boolean = false) {
    BasicText(
        if (text.length > 600) text.take(600) + "…" else text,
        Modifier.padding(vertical = 2.dp),
        style = ts(size, color, if (bold) FontWeight.Bold else FontWeight.Normal, mono),
    )
}

@Composable
private fun Pill(text: String, primary: Boolean, pal: Pal, modifier: Modifier, onClick: () -> Unit) {
    Box(
        modifier.height(44.dp).clip(RoundedCornerShape(22.dp)).background(if (primary) pal.accent else pal.key)
            .clickable(onClick = onClick).padding(horizontal = 14.dp),
        contentAlignment = Alignment.Center,
    ) {
        BasicText(text, style = ts(14f, if (primary) pal.onAccent else pal.text, FontWeight.Bold), maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
private fun FileCard(m: ChatEngine.Msg.File, pal: Pal, onFile: (ChatEngine.Msg.File, String) -> Unit) {
    val ctx = LocalContext.current
    val f = remember(m.guestPath) { Files.resolveGuest(ctx, m.guestPath) }
    Card(pal) {
        if (f == null) {
            Lbl("Файл больше недоступен: " + m.guestPath.substringAfterLast('/'), pal.muted, 14f)
            return@Card
        }
        val mime = Files.mimeOf(f.name)
        if (mime.startsWith("image/")) {
            val bmp = remember(f.path) {
                try {
                    BitmapFactory.decodeFile(f.path, BitmapFactory.Options().apply { inSampleSize = 4 })
                } catch (_: Throwable) {
                    null
                }
            }
            if (bmp != null) {
                Image(bmp.asImageBitmap(), null, Modifier.heightIn(max = 260.dp).padding(bottom = 10.dp))
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(44.dp).background(pal.accent, CircleShape), contentAlignment = Alignment.Center) {
                Icon(R.drawable.ic_file, pal.onAccent, 22)
            }
            Column(Modifier.padding(start = 12.dp)) {
                Lbl(f.name, pal.text, 15f, bold = true)
                Lbl(Files.humanSize(f.length()) + " · " + mime, pal.muted, 12f)
            }
        }
        Row(Modifier.padding(top = 12.dp).fillMaxWidth()) {
            ActionItem(R.drawable.ic_open, "Открыть", pal, Modifier.weight(1f)) { onFile(m, "open") }
            ActionItem(R.drawable.ic_dl, "Сохранить", pal, Modifier.weight(1f)) { onFile(m, "save") }
            ActionItem(R.drawable.ic_share, "Отправить", pal, Modifier.weight(1f)) { onFile(m, "share") }
        }
    }
}

@Composable
private fun ActionItem(icon: Int, caption: String, pal: Pal, modifier: Modifier, onClick: () -> Unit) {
    Column(modifier.clickable(interactionSource = null, indication = null, onClick = onClick), horizontalAlignment = Alignment.CenterHorizontally) {
        Box(
            Modifier.size(46.dp).background(pal.panel, CircleShape).border(1.dp, pal.line, CircleShape),
            contentAlignment = Alignment.Center,
        ) { Icon(icon, pal.text, 22) }
        BasicText(caption, Modifier.padding(top = 4.dp), style = ts(11f, pal.muted))
    }
}

// ---------------------------------------------------------------------------------------------
// Поле ввода

@Composable
private fun Composer(
    ui: ChatUi,
    pal: Pal,
    backdrop: Backdrop,
    surface: Color,
    tilt: State<Float>,
    frame: State<Long>,
    onModel: () -> Unit,
    onPlus: () -> Unit,
    onSend: () -> Unit,
    modifier: Modifier,
) {
    val busy = ui.busy
    var now by remember { mutableLongStateOf(SystemClock.elapsedRealtime()) }
    LaunchedEffect(busy) {
        while (busy) {
            now = SystemClock.elapsedRealtime()
            delay(500)
        }
    }
    val focus = remember { FocusRequester() }
    val focusManager = LocalFocusManager.current
    LaunchedEffect(ui.focusTick) { if (ui.focusTick > 0) runCatching { focus.requestFocus() } }
    LaunchedEffect(ui.blurTick) { if (ui.blurTick > 0) focusManager.clearFocus() }

    Column(modifier.fillMaxWidth().padding(start = 10.dp, end = 10.dp, top = 6.dp, bottom = 8.dp)) {
        if (busy) {
            val tip = remember(now) {
                val base = currentActivity()
                val liveLine = if (isRunningBash()) ChatEngine.liveLine else ""
                base to liveLine
            }
            Column(
                Modifier.padding(start = 52.dp, bottom = 8.dp)
                    .background(pal.panel, RoundedCornerShape(16.dp)).border(1.dp, pal.line, RoundedCornerShape(16.dp))
                    .padding(13.dp, 7.dp),
            ) {
                BasicText(tip.first, style = ts(12.5f, pal.text), maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (tip.second.isNotEmpty()) BasicText(tip.second, style = ts(11f, pal.muted), maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        if (ui.attachments.isNotEmpty()) {
            LazyRow(Modifier.padding(bottom = 6.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                items(ui.attachments.toList()) { a ->
                    Box(
                        Modifier.background(pal.panel, RoundedCornerShape(18.dp)).border(1.dp, pal.line, RoundedCornerShape(18.dp))
                            .clickable { ui.attachments.remove(a) }.padding(14.dp, 9.dp),
                    ) { BasicText(a.name.take(26) + "   ✕", style = ts(13f, pal.text), maxLines = 1) }
                }
            }
        }
        Row(verticalAlignment = Alignment.Bottom) {
            GlassBox(
                backdrop, CircleShape, surface, tilt, Modifier.size(54.dp),
                onClick = { if (busy) ChatEngine.stop() else onPlus() },
            ) {
                Icon(if (busy) R.drawable.ic_stop else R.drawable.ic_clip, pal.text, 24, Modifier.align(Alignment.Center))
            }
            Spacer(Modifier.width(6.dp))
            val pillShape = RoundedCornerShape(27.dp)
            GlassBox(
                backdrop, pillShape, surface, tilt, Modifier.weight(1f).heightIn(min = 54.dp), rubber = false,
                front = if (busy) ({ drawGlow(pal.accent, frame.value) }) else null,
            ) {
                Row(Modifier.fillMaxWidth().heightIn(min = 54.dp), verticalAlignment = Alignment.Bottom) {
                    if (busy) {
                        Row(Modifier.weight(1f).height(54.dp).padding(start = 20.dp), verticalAlignment = Alignment.CenterVertically) {
                            Spinner(pal.accent, frame)
                            val sec = ((now - ChatEngine.busySince) / 1000).coerceAtLeast(0)
                            val time = if (sec < 60) "$sec с" else "${sec / 60} мин ${"%02d".format(sec % 60)} с"
                            BasicText("${statusWord()}… $time", Modifier.padding(start = 10.dp), style = ts(16f, pal.text), maxLines = 1)
                        }
                    } else {
                        BasicTextField(
                            value = ui.draft,
                            onValueChange = { ui.draft = it },
                            modifier = Modifier.weight(1f).focusRequester(focus).padding(start = 20.dp, top = 14.dp, bottom = 14.dp),
                            textStyle = ts(16.5f, pal.text),
                            cursorBrush = SolidColor(pal.accent),
                            maxLines = 5,
                            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
                            decorationBox = { inner ->
                                Box {
                                    if (ui.draft.text.isEmpty()) BasicText("Что сделать?", style = ts(16.5f, pal.muted))
                                    inner()
                                }
                            },
                        )
                        Box(
                            Modifier.size(40.dp).padding(bottom = 0.dp).clickable(interactionSource = null, indication = null, onClick = onModel),
                            contentAlignment = Alignment.Center,
                        ) { Icon(R.drawable.ic_cpu, pal.muted, 22) }
                    }
                    Box(
                        Modifier.padding(start = 2.dp, end = 8.dp, bottom = 8.dp).size(38.dp).alpha(if (busy) 0.45f else 1f)
                            .background(pal.accent, CircleShape).clickable(interactionSource = null, indication = null) { if (!busy) onSend() },
                        contentAlignment = Alignment.Center,
                    ) { Icon(R.drawable.ic_send, pal.onAccent, 20) }
                }
            }
        }
    }
}

private fun androidx.compose.ui.graphics.drawscope.DrawScope.drawGlow(accent: Color, frameNanos: Long) {
    val pad = 1.dp.toPx()
    val tl = Offset(pad, pad)
    val sz = Size(size.width - 2 * pad, size.height - 2 * pad)
    val cr = CornerRadius(sz.height / 2f)
    for ((w, a) in listOf(10f to 0.09f, 6.5f to 0.16f, 3.5f to 0.28f)) {
        drawRoundRect(accent.copy(alpha = a), tl, sz, cr, style = Stroke(w.dp.toPx()))
    }
    drawRoundRect(accent.copy(alpha = 0.47f), tl, sz, cr, style = Stroke(1.dp.toPx()))
    val angle = (frameNanos / 1_000_000L % 2200L) / 2200f * 360f
    rotate(angle, center) {
        drawRoundRect(
            Brush.sweepGradient(0f to Color.Transparent, 0.15f to accent, 0.3f to Color.Transparent, 1f to Color.Transparent, center = center),
            tl, sz, cr, style = Stroke(1.8f.dp.toPx()),
        )
    }
}

@Composable
private fun Spinner(color: Color, frame: State<Long>) {
    Canvas(Modifier.size(22.dp)) {
        val angle = (frame.value / 1_000_000L % 900L) / 900f * 360f
        rotate(angle) {
            drawArc(color, 0f, 270f, false, style = Stroke(2.4f.dp.toPx(), cap = androidx.compose.ui.graphics.StrokeCap.Round), topLeft = Offset(2.dp.toPx(), 2.dp.toPx()), size = Size(size.width - 4.dp.toPx(), size.height - 4.dp.toPx()))
        }
    }
}
