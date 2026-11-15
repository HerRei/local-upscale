package local.sr

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.ImageDecoder
import android.net.Uri
import android.provider.OpenableColumns
import android.util.Log
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
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
            val uri = Uri.parse(uriString)
            val source = if (uri.scheme == null || uri.scheme == "file") {
                val path = uri.path ?: return null
                ImageDecoder.createSource(File(path))
            } else {
                ImageDecoder.createSource(context.contentResolver, uri)
            }
            ImageDecoder.decodeBitmap(source) { decoder, info, _ ->
                decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
                decoder.isMutableRequired = false
                val w = info.size.width
                val h = info.size.height
                val longSide = maxOf(w, h)
                if (longSide > maxSide && longSide > 0) {
                    val scale = maxSide.toDouble() / longSide.toDouble()
                    decoder.setTargetSize(maxOf(1, (w * scale).toInt()), maxOf(1, (h * scale).toInt()))
                }
            }
        } catch (t: Throwable) {
            Log.w(TAG, "decodeDisplayBitmap failed")
            null
        }
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
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    var bitmap by remember(uriString, maxSide) { mutableStateOf<Bitmap?>(null) }
    LaunchedEffect(uriString, maxSide) {
        bitmap = withContext(Dispatchers.IO) {
            AndroidUISupport.decodeDisplayBitmap(context, uriString, maxSide)
        }
    }
    val bmp = bitmap
    if (bmp != null) {
        Image(
            bitmap = bmp.asImageBitmap(),
            contentDescription = null,
            contentScale = if (crop) ContentScale.Crop else ContentScale.Fit,
            modifier = modifier.fillMaxSize(),
        )
    } else {
        Box(modifier = modifier.fillMaxSize())
    }
}
