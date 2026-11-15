package local.sr

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import android.net.Uri
import android.provider.OpenableColumns
import android.util.Log
import androidx.exifinterface.media.ExifInterface
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
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
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

object AndroidUISupport {
    private const val TAG = "LocalSR"

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
        val am = context.getSystemService(Context.ACTIVITY_SERVICE) as? ActivityManager
        val memInfo = ActivityManager.MemoryInfo()
        am?.getMemoryInfo(memInfo)
        val memoryClassMB = am?.largeMemoryClass ?: am?.memoryClass ?: 256

        return ImageDecoder.decodeBitmap(source) { decoder, info, _ ->
            decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
            decoder.isMutableRequired = false
            val w = info.size.width
            val h = info.size.height
            val longSide = maxOf(w, h)
            if (maxSide > 0) {
                if (longSide > maxSide && longSide > 0) {
                    var sampleSize = 1
                    while (longSide / (sampleSize * 2) >= maxSide) {
                        sampleSize *= 2
                    }
                    if (sampleSize > 1) {
                        decoder.setTargetSampleSize(sampleSize)
                    } else {
                        val scale = maxSide.toDouble() / longSide.toDouble()
                        decoder.setTargetSize(maxOf(1, (w * scale).toInt()), maxOf(1, (h * scale).toInt()))
                    }
                }
            } else {
                val estBytes = w.toLong() * h.toLong() * 4L
                val memoryBudgetBytes = memoryClassMB.toLong() * 1024L * 1024L
                val lowHeadroom = memInfo.lowMemory ||
                    memInfo.availMem < 800L * 1024L * 1024L ||
                    memoryClassMB < 256 ||
                    estBytes > memoryBudgetBytes / 2L ||
                    longSide > 8192
                if (lowHeadroom && longSide > 4096) {
                    var sampleSize = 1
                    while (longSide / sampleSize > 4096) {
                        sampleSize *= 2
                    }
                    decoder.setTargetSampleSize(sampleSize)
                } else {
                    decoder.setTargetSampleSize(1)
                }
            }
        }
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
fun AndroidDecodedImage(
    uriString: String,
    maxSide: Int,
    crop: Boolean,
    fillBounds: Boolean = false,
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
            contentDescription = null,
            contentScale = contentScale,
            modifier = modifier.fillMaxSize(),
        )
    } else {
        Box(modifier = modifier.fillMaxSize())
    }
}
