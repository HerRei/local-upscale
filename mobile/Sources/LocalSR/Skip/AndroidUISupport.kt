package local.sr

import android.app.Activity
import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import android.net.Uri
import android.os.StatFs
import android.provider.DocumentsContract
import android.provider.OpenableColumns
import android.util.Log
import android.view.View
import androidx.activity.compose.BackHandler
import androidx.exifinterface.media.ExifInterface
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.ui.text.ExperimentalTextApi
import androidx.compose.foundation.Image
import androidx.compose.foundation.draganddrop.dragAndDropSource
import androidx.compose.foundation.draganddrop.dragAndDropTarget
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draganddrop.DragAndDropEvent
import androidx.compose.ui.draganddrop.DragAndDropTarget
import androidx.compose.ui.draganddrop.DragAndDropTransferData
import androidx.compose.ui.draganddrop.mimeTypes
import androidx.compose.ui.draganddrop.toAndroidDragEvent
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.isAltPressed
import androidx.compose.ui.input.key.isCtrlPressed
import androidx.compose.ui.input.key.isShiftPressed
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.PointerEventType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.layout
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.io.File
import java.util.UUID
import kotlin.math.abs

object AndroidUISupport {
    private const val TAG = "LocalSR"
    @Volatile private var cachedIbmPlexFamily: FontFamily? = null

    @OptIn(ExperimentalTextApi::class)
    fun ibmPlexFontFamily(context: Context): FontFamily {
        cachedIbmPlexFamily?.let { return it }
        val resId = context.resources.getIdentifier("ibm_plex_sans_var", "font", context.packageName)
        if (resId == 0) {
            Log.w(TAG, "ibm_plex_sans_var font resource not found; falling back to default")
            return FontFamily.Default
        }
        val family = try {
            FontFamily(
                Font(resId = resId, weight = FontWeight.Normal, variationSettings = FontVariation.Settings(FontVariation.weight(400))),
                Font(resId = resId, weight = FontWeight.Medium, variationSettings = FontVariation.Settings(FontVariation.weight(500))),
                Font(resId = resId, weight = FontWeight.SemiBold, variationSettings = FontVariation.Settings(FontVariation.weight(600))),
                Font(resId = resId, weight = FontWeight.Bold, variationSettings = FontVariation.Settings(FontVariation.weight(700))),
            )
        } catch (t: Throwable) {
            Log.w(TAG, "Failed to load ibm_plex_sans_var FontFamily: ${t.message}")
            FontFamily.Default
        }
        cachedIbmPlexFamily = family
        return family
    }

    fun ibmPlexTextStyle(context: Context, sizeSp: Float, weightCode: Int): androidx.compose.ui.text.TextStyle {
        val weight = when {
            weightCode >= 700 -> FontWeight.Bold
            weightCode >= 600 -> FontWeight.SemiBold
            weightCode >= 500 -> FontWeight.Medium
            else -> FontWeight.Normal
        }
        return androidx.compose.ui.text.TextStyle(
            fontFamily = ibmPlexFontFamily(context),
            fontSize = androidx.compose.ui.unit.TextUnit(sizeSp, androidx.compose.ui.unit.TextUnitType.Sp),
            fontWeight = weight,
        )
    }

    fun availableStorageBytes(context: Context): Long {
        return try {
            StatFs(context.cacheDir.absolutePath).availableBytes
        } catch (_: Throwable) {
            Long.MAX_VALUE
        }
    }

    fun readNoticeText(context: Context): String {
        return try {
            context.assets.open("NOTICE.txt").bufferedReader().use { it.readText() }
        } catch (t: Throwable) {
            Log.w(TAG, "Cannot read NOTICE.txt: ${t.message}")
            ""
        }
    }

    fun appVersionString(context: Context): String {
        return try {
            val info = context.packageManager.getPackageInfo(context.packageName, 0)
            val version = info.versionName ?: "0.0.1"
            val code = if (android.os.Build.VERSION.SDK_INT >= 28) info.longVersionCode else @Suppress("DEPRECATION") info.versionCode.toLong()
            "$version ($code)"
        } catch (t: Throwable) {
            "0.0.1"
        }
    }

    fun displayNameForUri(context: Context, uriString: String, fallback: String): String {
        val uri = Uri.parse(uriString)
        if (uri.scheme == "content") {
            try {
                context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
                    if (cursor.moveToFirst()) {
                        val idx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                        if (idx >= 0) {
                            val name = cursor.getString(idx)
                            if (!name.isNullOrBlank()) return name
                        }
                    }
                }
            } catch (_: Throwable) {
            }
        } else if (uri.scheme == "file" || uri.scheme == null) {
            val path = uri.path
            if (!path.isNullOrBlank()) {
                val name = File(path).name
                if (name.isNotBlank()) return name
            }
        }
        return fallback
    }

    fun decodeDisplayBitmap(context: Context, uriString: String, maxSide: Int): Bitmap? {
        return try {
            decodeBitmapWithCap(context, uriString, maxSide)
        } catch (t: Throwable) {
            if (maxSide <= 0) {
                Log.w(TAG, "Full-res decode failed (${t.message}), falling back to 4096")
                try {
                    decodeBitmapWithCap(context, uriString, 4096)
                } catch (fallbackErr: Throwable) {
                    Log.w(TAG, "4096 fallback decode failed: ${fallbackErr.message}")
                    null
                }
            } else {
                Log.w(TAG, "decodeDisplayBitmap failed: ${t.message}")
                null
            }
        }
    }

    private fun decodeBitmapWithCap(context: Context, uriString: String, maxSide: Int): Bitmap? {
        val uri = Uri.parse(uriString)
        val source = if (uri.scheme == null || uri.scheme == "file") {
            val path = uri.path ?: return null
            ImageDecoder.createSource(File(path))
        } else {
            ImageDecoder.createSource(context.contentResolver, uri)
        }
        return ImageDecoder.decodeBitmap(source) { decoder, info, _ ->
            decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
            decoder.isMutableRequired = false
            val w = info.size.width
            val h = info.size.height
            val (tw, th) = displayDecodeSize(w, h, maxSide)
            if (tw != w || th != h) decoder.setTargetSize(tw, th) else decoder.setTargetSampleSize(1)
        }
    }

    // RecordingCanvas throws for bitmaps over 100 MiB (about 26 MP in ARGB_8888), so a full 4x result
    // (6400x4800 and up) can never be drawn at native size. The long-side cap matches the iOS viewer.
    const val MAX_DRAW_PIXELS = 25_000_000L
    const val MAX_DRAW_LONG_SIDE = 8192

    /** The size the viewer decodes a w x h image to; maxSide 0 means as large as Android can draw. */
    fun displayDecodeSize(w: Int, h: Int, maxSide: Int): Pair<Int, Int> {
        val longSide = maxOf(w, h)
        if (longSide <= 0) return Pair(w, h)
        val sideCap = if (maxSide > 0) minOf(maxSide, MAX_DRAW_LONG_SIDE) else MAX_DRAW_LONG_SIDE
        var scale = minOf(1.0, sideCap.toDouble() / longSide.toDouble())
        val pixels = w.toDouble() * h.toDouble() * scale * scale
        if (pixels > MAX_DRAW_PIXELS) scale *= Math.sqrt(MAX_DRAW_PIXELS / pixels)
        if (scale >= 1.0) return Pair(w, h)
        return Pair(maxOf(1, (w * scale).toInt()), maxOf(1, (h * scale).toInt()))
    }

    fun imagePixelSize(context: Context, uriString: String): List<Int>? {
        return try {
            val uri = Uri.parse(uriString)
            val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            if (uri.scheme == null || uri.scheme == "file") {
                val path = uri.path ?: return null
                BitmapFactory.decodeFile(path, options)
            } else {
                context.contentResolver.openInputStream(uri)?.use { stream ->
                    BitmapFactory.decodeStream(stream, null, options)
                }
            }
            var w = options.outWidth
            var h = options.outHeight
            if ((w <= 0 || h <= 0) && uri.scheme != null && uri.scheme != "file") {
                try {
                    val src = ImageDecoder.createSource(context.contentResolver, uri)
                    ImageDecoder.decodeBitmap(src) { decoder, info, _ ->
                        w = info.size.width
                        h = info.size.height
                        decoder.setTargetSize(1, 1)
                    }.recycle()
                } catch (e: Throwable) {
                    Log.w(TAG, "ImageDecoder header fallback failed for $uriString: ${e.message}")
                }
            }
            if (w <= 0 || h <= 0) {
                Log.w(TAG, "imagePixelSize failed for $uriString (w=$w h=$h)")
                return null
            }
            val orientation = try {
                if (uri.scheme == null || uri.scheme == "file") {
                    uri.path?.let { ExifInterface(it).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL) }
                } else {
                    context.contentResolver.openInputStream(uri)?.use { stream ->
                        ExifInterface(stream).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)
                    }
                }
            } catch (_: Throwable) {
                ExifInterface.ORIENTATION_NORMAL
            } ?: ExifInterface.ORIENTATION_NORMAL
            if (orientation == ExifInterface.ORIENTATION_TRANSPOSE ||
                orientation == ExifInterface.ORIENTATION_ROTATE_90 ||
                orientation == ExifInterface.ORIENTATION_TRANSVERSE ||
                orientation == ExifInterface.ORIENTATION_ROTATE_270) {
                val tmp = w
                w = h
                h = tmp
            }
            listOf(w, h)
        } catch (_: Throwable) {
            null
        }
    }

    fun imageAspectRatio(context: Context, uriString: String): Double? {
        val dims = imagePixelSize(context, uriString) ?: return null
        return dims[0].toDouble() / dims[1].toDouble()
    }

    fun isTablet(context: Context): Boolean {
        // The tablet layout needs the tablet models; 1.0 ships for phones only, so a tablet
        // gets the phone layout unless a build bundles them (assets/tablet).
        return context.resources.configuration.smallestScreenWidthDp >= 600 && bundlesTabletModels(context)
    }

    fun bundlesTabletModels(context: Context): Boolean {
        return try {
            context.assets.list("tablet")?.any { it.endsWith(".tflite") } == true
        } catch (e: java.io.IOException) {
            false
        }
    }

    fun pasteImageUriStrings(context: Context): List<String> {
        val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager ?: return emptyList()
        val clip = cm.primaryClip ?: return emptyList()
        val out = mutableListOf<String>()
        for (i in 0 until clip.itemCount) {
            val uri = clip.getItemAt(i).uri ?: continue
            val mime = context.contentResolver.getType(uri)
            if (mime?.startsWith("image/") == true) {
                out.add(uri.toString())
            }
        }
        return out
    }

    fun collectImagesInTree(context: Context, treeUriString: String): List<String> {
        val treeUri = Uri.parse(treeUriString)
        val out = mutableListOf<String>()
        val rootDocId = DocumentsContract.getTreeDocumentId(treeUri)
        val queue = ArrayDeque<String>()
        queue.addLast(rootDocId)
        val projection = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_MIME_TYPE
        )
        while (queue.isNotEmpty()) {
            val docId = queue.removeFirst()
            val childrenUri = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, docId)
            try {
                context.contentResolver.query(childrenUri, projection, null, null, null)?.use { cursor ->
                    val idCol = cursor.getColumnIndex(DocumentsContract.Document.COLUMN_DOCUMENT_ID)
                    val mimeCol = cursor.getColumnIndex(DocumentsContract.Document.COLUMN_MIME_TYPE)
                    while (cursor.moveToNext()) {
                        val childId = if (idCol >= 0) cursor.getString(idCol) else continue
                        val mime = if (mimeCol >= 0) cursor.getString(mimeCol) else ""
                        if (mime == DocumentsContract.Document.MIME_TYPE_DIR) {
                            queue.addLast(childId)
                        } else if (mime?.startsWith("image/") == true) {
                            val itemUri = DocumentsContract.buildDocumentUriUsingTree(treeUri, childId)
                            out.add(itemUri.toString())
                        }
                    }
                }
            } catch (t: Throwable) {
                Log.w(TAG, "collectImagesInTree failed for $docId: ${t.message}")
            }
        }
        return out
    }

    fun shareImageFile(context: Context, path: String) {
        try {
            val file = File(path)
            val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
            val intent = Intent(Intent.ACTION_SEND).apply {
                type = "image/jpeg"
                putExtra(Intent.EXTRA_STREAM, uri)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            val chooser = Intent.createChooser(intent, null).apply {
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                if (context !is android.app.Activity) {
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                }
            }
            context.startActivity(chooser)
        } catch (t: Throwable) {
            Log.w(TAG, "shareImageFile failed: ${t.message}")
        }
    }
}

@Composable
fun AndroidPhotoPickerHost(
    onReady: (() -> Unit) -> Unit,
    onPicked: (List<String>) -> Unit,
    modifier: Modifier = Modifier,
) {
    val launcher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.PickMultipleVisualMedia(20)
    ) { uris ->
        if (uris.isNotEmpty()) {
            onPicked(uris.map { it.toString() })
        }
    }
    SideEffect {
        onReady {
            launcher.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
        }
    }
    Box(modifier = modifier)
}

@Composable
fun AndroidFilePickerHost(
    onReady: (() -> Unit) -> Unit,
    onPicked: (List<String>) -> Unit,
    modifier: Modifier = Modifier,
) {
    val launcher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.OpenMultipleDocuments()
    ) { uris ->
        if (uris.isNotEmpty()) {
            onPicked(uris.map { it.toString() })
        }
    }
    SideEffect {
        onReady {
            launcher.launch(arrayOf("image/*"))
        }
    }
    Box(modifier = modifier)
}

@Composable
fun AndroidFolderPickerHost(
    onReady: (() -> Unit) -> Unit,
    onPickedTree: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val launcher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.OpenDocumentTree()
    ) { uri ->
        if (uri != null) {
            onPickedTree(uri.toString())
        }
    }
    SideEffect {
        onReady {
            launcher.launch(null)
        }
    }
    Box(modifier = modifier)
}

@Composable
fun AndroidDecodedImage(
    uriString: String,
    maxSide: Int,
    crop: Boolean,
    fillBounds: Boolean = false,
    accessibilityID: String? = null,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    var bitmap by remember { mutableStateOf<Bitmap?>(null) }
    LaunchedEffect(uriString, maxSide) {
        val decoded = withContext(Dispatchers.IO) {
            AndroidUISupport.decodeDisplayBitmap(context, uriString, maxSide)
        }
        if (decoded != null) {
            bitmap = decoded
        }
    }
    DisposableEffect(Unit) {
        onDispose {
            bitmap = null
        }
    }
    val bmp = bitmap
    if (bmp != null) {
        val contentScale = when {
            fillBounds -> ContentScale.FillBounds
            crop -> ContentScale.Crop
            else -> ContentScale.Fit
        }
        Image(
            bitmap = bmp.asImageBitmap(),
            contentDescription = accessibilityID?.let { "$it ${bmp.width}x${bmp.height}" },
            contentScale = contentScale,
            modifier = (if (accessibilityID != null) modifier.testTag(accessibilityID) else modifier).fillMaxSize(),
        )
    } else {
        Box(modifier = modifier.fillMaxSize())
    }
}

/**
 * Clips drawing and layout bounds horizontally to [widthDp] without centering overflowed content,
 * so layers behind this view remain uncovered to Compose accessibility.
 */
fun Modifier.clipHorizontalBounds(widthDp: Double): Modifier =
    this.clipToBounds().layout { measurable, constraints ->
        val placeable = measurable.measure(constraints)
        val clippedWidth = widthDp.coerceAtLeast(0.0).dp.roundToPx().coerceIn(0, placeable.width)
        layout(clippedWidth, placeable.height) {
            placeable.place(0, 0)
        }
    }

@Composable
fun AndroidBackHandlerHost(enabled: Boolean, onBack: () -> Unit) {
    BackHandler(enabled = enabled, onBack = onBack)
}

fun androidLongPressModifier(modifier: Modifier, enabled: Boolean, onLongPress: () -> Unit): Modifier {
    if (!enabled) return modifier
    return modifier.pointerInput(enabled) {
        val slop = viewConfiguration.touchSlop
        awaitEachGesture {
            val down = awaitPointerEvent(pass = PointerEventPass.Initial)
            val start = down.changes.firstOrNull { it.pressed }?.position
            if (start != null) {
                val longPressed = withTimeoutOrNull(viewConfiguration.longPressTimeoutMillis) {
                    var active = true
                    while (active) {
                        val c = awaitPointerEvent(pass = PointerEventPass.Initial).changes.firstOrNull()
                        active = if (c != null) {
                            c.pressed && abs(c.position.x - start.x) <= slop && abs(c.position.y - start.y) <= slop
                        } else {
                            false
                        }
                    }
                } == null
                if (longPressed) {
                    onLongPress()
                    var pressed = true
                    while (pressed) {
                        val event = awaitPointerEvent(pass = PointerEventPass.Initial)
                        event.changes.forEach { it.consume() }
                        pressed = event.changes.any { it.pressed }
                    }
                }
            }
        }
    }
}

fun Modifier.androidHover(onHoverChanged: (Boolean) -> Unit): Modifier =
    this.pointerInput(Unit) {
        awaitPointerEventScope {
            while (true) {
                val event = awaitPointerEvent(pass = PointerEventPass.Initial)
                when (event.type) {
                    PointerEventType.Enter -> onHoverChanged(true)
                    PointerEventType.Exit -> onHoverChanged(false)
                }
            }
        }
    }

@OptIn(ExperimentalFoundationApi::class)
fun Modifier.androidWindowDropTarget(
    context: Context,
    onTargeted: (Boolean) -> Unit,
    onDropped: (List<String>, List<String>, Boolean, Boolean) -> Unit,
): Modifier {
    val target = object : DragAndDropTarget {
        override fun onStarted(event: DragAndDropEvent) {
            onTargeted(true)
        }

        override fun onEntered(event: DragAndDropEvent) {
            onTargeted(true)
        }

        override fun onExited(event: DragAndDropEvent) {
            onTargeted(false)
        }

        override fun onEnded(event: DragAndDropEvent) {
            onTargeted(false)
        }

        override fun onDrop(event: DragAndDropEvent): Boolean {
            onTargeted(false)
            val dragEvent = event.toAndroidDragEvent()
            val clipData = dragEvent.clipData ?: return false
            val activity = context as? Activity
            val permission = try {
                activity?.requestDragAndDropPermissions(dragEvent)
            } catch (_: Throwable) {
                null
            }
            val copiedPaths = mutableListOf<String>()
            val displayNames = mutableListOf<String>()
            var hasVideo = false
            var hasUnreadable = false
            try {
                val inputsDir = File(context.cacheDir, "LocalSRTabletInputs").apply { mkdirs() }
                for (i in 0 until clipData.itemCount) {
                    val uri = clipData.getItemAt(i).uri ?: continue
                    val mime = context.contentResolver.getType(uri) ?: ""
                    if (mime.startsWith("video/")) {
                        hasVideo = true
                        continue
                    }
                    val name = AndroidUISupport.displayNameForUri(context, uri.toString(), "Photo ${i + 1}.jpg")
                    val ext = name.substringAfterLast('.', "jpg").ifEmpty { "jpg" }
                    val dest = File(inputsDir, "${UUID.randomUUID()}.$ext")
                    val copied = try {
                        context.contentResolver.openInputStream(uri)?.use { input ->
                            dest.outputStream().use { out -> input.copyTo(out) }
                            true
                        } ?: false
                    } catch (_: Throwable) {
                        false
                    }
                    if (copied && AndroidUISupport.imagePixelSize(context, dest.toURI().toString()) != null) {
                        copiedPaths.add(dest.absolutePath)
                        displayNames.add(name)
                    } else {
                        dest.delete()
                        hasUnreadable = true
                    }
                }
            } finally {
                try {
                    permission?.release()
                } catch (_: Throwable) {
                }
            }
            onDropped(copiedPaths, displayNames, hasVideo, hasUnreadable)
            return copiedPaths.isNotEmpty() || hasVideo || hasUnreadable
        }
    }
    return this.dragAndDropTarget(
        shouldStartDragAndDrop = { event ->
            val mimes = event.mimeTypes()
            mimes.any {
                it.startsWith("image/") ||
                    it.startsWith("video/") ||
                    it == ClipDescription.MIMETYPE_TEXT_URILIST
            }
        },
        target = target,
    )
}

@OptIn(ExperimentalFoundationApi::class)
fun Modifier.androidDragOutFile(context: Context, path: String?): Modifier {
    if (path.isNullOrBlank()) return this
    return this.dragAndDropSource { _ ->
        try {
            val file = File(path)
            if (!file.exists()) return@dragAndDropSource null
            val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
            val clip = ClipData.newUri(context.contentResolver, file.name, uri)
            DragAndDropTransferData(
                clipData = clip,
                flags = View.DRAG_FLAG_GLOBAL or View.DRAG_FLAG_GLOBAL_URI_READ,
            )
        } catch (t: Throwable) {
            Log.w("LocalSR", "androidDragOutFile failed: ${t.message}")
            null
        }
    }
}

fun Modifier.androidCtrlShortcuts(onAction: (String) -> Boolean): Modifier =
    this.onPreviewKeyEvent { event ->
        if (event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
        val ctrl = event.isCtrlPressed
        val shift = event.isShiftPressed
        val alt = event.isAltPressed
        val action: String? = when {
            ctrl && shift && event.key == Key.O -> "choosePhotos"
            ctrl && !shift && event.key == Key.O -> "chooseFiles"
            ctrl && event.key == Key.V -> "paste"
            ctrl && shift && (event.key == Key.Enter || event.key == Key.NumPadEnter) -> "startAll"
            ctrl && !shift && (event.key == Key.Enter || event.key == Key.NumPadEnter) -> "upscaleSelected"
            ctrl && event.key == Key.Period -> "cancelQueue"
            ctrl && event.key == Key.One -> "tab1"
            ctrl && event.key == Key.Two -> "tab2"
            ctrl && event.key == Key.Three -> "tab3"
            ctrl && alt && (event.key == Key.Zero || event.key == Key.NumPad0) -> "zoom1to1"
            ctrl && !alt && (event.key == Key.Zero || event.key == Key.NumPad0) -> "zoomFit"
            ctrl && (event.key == Key.Equals || event.key == Key.Plus || event.key == Key.NumPadAdd) -> "zoomIn"
            ctrl && (event.key == Key.Minus || event.key == Key.NumPadSubtract) -> "zoomOut"
            ctrl && shift && event.key == Key.S -> "saveAll"
            ctrl && !shift && event.key == Key.S -> "save"
            ctrl && shift && event.key == Key.L -> "modelLibrary"
            ctrl && event.key == Key.Comma -> "settings"
            !ctrl && event.key == Key.DirectionUp -> "prevPhoto"
            !ctrl && event.key == Key.DirectionDown -> "nextPhoto"
            !ctrl && (event.key == Key.Delete || event.key == Key.Backspace) -> "removePhoto"
            !ctrl && event.key == Key.DirectionLeft -> "dividerLeft"
            !ctrl && event.key == Key.DirectionRight -> "dividerRight"
            !ctrl && event.key == Key.MoveHome -> "dividerStart"
            !ctrl && event.key == Key.MoveEnd -> "dividerEnd"
            !ctrl && event.key == Key.M -> "toggleOriginal"
            else -> null
        }
        if (action != null) onAction(action) else false
    }
