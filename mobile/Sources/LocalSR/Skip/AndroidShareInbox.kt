package local.sr

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.webkit.MimeTypeMap
import androidx.core.content.IntentCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.File
import java.util.UUID

class AndroidSharedInput(
    val urlString: String,
    val displayName: String,
)

/** Pure checks for the Activity's launch intent (no Android objects, so JVM tests can cover them). */
object ShareLaunchPolicy {
    /**
     * Whether onCreate should treat its intent as a new share. Not when Android recreates the Activity from saved
     * state (after process death) or relaunches the task from Recents: both hand over the original SEND intent
     * again, and handling it would copy and upscale the same photos a second time.
     */
    fun isNewShare(hasSavedState: Boolean, intentFlags: Int): Boolean =
        !hasSavedState && (intentFlags and Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY) == 0
}

object AndroidShareInbox {
    private const val TAG = "LocalSR"
    private const val MAX_ITEMS = 20

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val stateLock = Any()
    private val copyMutex = Mutex()
    private val pendingInputs = ArrayList<AndroidSharedInput>()
    private var activeJob: Job? = null
    private var onInputsAvailable: (() -> Unit)? = null
    private val mainHandler = Handler(Looper.getMainLooper())

    fun setOnInputsAvailable(callback: (() -> Unit)?) {
        synchronized(stateLock) {
            onInputsAvailable = callback
        }
    }

    fun handleIntent(context: Context, intent: Intent?) {
        if (intent == null) return
        val action = intent.action
        if (action != Intent.ACTION_SEND && action != Intent.ACTION_SEND_MULTIPLE) return

        val uris = extractImageUris(intent)
        intent.action = null
        if (uris.isEmpty()) return

        val appContext = context.applicationContext
        val flags = intent.flags
        if ((flags and Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION) != 0) {
            for (uri in uris) {
                try {
                    appContext.contentResolver.takePersistableUriPermission(
                        uri,
                        Intent.FLAG_GRANT_READ_URI_PERMISSION
                    )
                } catch (_: Throwable) {
                }
            }
        }

        val previousJob = synchronized(stateLock) { activeJob }
        val newJob = scope.launch {
            previousJob?.join()
            val copied = copyMutex.withLock {
                copyUrisToCache(appContext, uris)
            }
            if (copied.isNotEmpty()) {
                val callback = synchronized(stateLock) {
                    pendingInputs.addAll(copied)
                    onInputsAvailable
                }
                if (callback != null) {
                    mainHandler.post { callback() }
                }
            }
        }
        synchronized(stateLock) {
            activeJob = newJob
        }
    }

    suspend fun awaitAndDrainPendingInputs(): List<AndroidSharedInput> {
        val job = synchronized(stateLock) { activeJob }
        job?.join()
        return synchronized(stateLock) {
            if (pendingInputs.isEmpty()) {
                emptyList()
            } else {
                val out = ArrayList(pendingInputs)
                pendingInputs.clear()
                out
            }
        }
    }

    private fun extractImageUris(intent: Intent): List<Uri> {
        val collected = ArrayList<Uri>()
        when (intent.action) {
            Intent.ACTION_SEND -> {
                val streamUri = IntentCompat.getParcelableExtra(intent, Intent.EXTRA_STREAM, Uri::class.java)
                if (streamUri != null) {
                    collected.add(streamUri)
                }
            }
            Intent.ACTION_SEND_MULTIPLE -> {
                val list = IntentCompat.getParcelableArrayListExtra(intent, Intent.EXTRA_STREAM, Uri::class.java)
                if (list != null) {
                    collected.addAll(list)
                }
            }
        }

        if (collected.isEmpty()) {
            val clip = intent.clipData
            if (clip != null) {
                for (i in 0 until clip.itemCount) {
                    clip.getItemAt(i).uri?.let { collected.add(it) }
                }
            }
        }

        val seen = HashSet<String>()
        val unique = ArrayList<Uri>()
        for (uri in collected) {
            val key = uri.toString()
            if (seen.add(key)) {
                unique.add(uri)
                if (unique.size >= MAX_ITEMS) break
            }
        }
        return unique
    }

    /**
     * Copies photos picked with the system photo picker into cache/LocalSRInputs, like shared photos. The picker's
     * read grant ends when the process stops, and results keep pointing at their input ("Before", "Upscale Again").
     * Returns one entry per URI, in order; a photo that cannot be copied keeps its picker URI (works this session).
     */
    suspend fun copyPickedToCache(context: Context, uriStrings: List<String>): List<AndroidSharedInput> = withContext(Dispatchers.IO) {
        val appContext = context.applicationContext
        val inputsDir = inputsDirectory(appContext)
        copyMutex.withLock {
            uriStrings.mapIndexed { i, uriString ->
                copyOne(appContext, inputsDir, Uri.parse(uriString), i)
                    ?: AndroidSharedInput(urlString = uriString, displayName = AndroidUISupport.displayNameForUri(appContext, uriString, "Photo ${i + 1}"))
            }
        }
    }

    private fun inputsDirectory(context: Context): File {
        val inputsDir = File(context.cacheDir, "LocalSRInputs")
        if (!inputsDir.exists()) {
            inputsDir.mkdirs()
        }
        return inputsDir
    }

    private fun copyUrisToCache(context: Context, uris: List<Uri>): List<AndroidSharedInput> {
        val inputsDir = inputsDirectory(context)
        val copied = ArrayList<AndroidSharedInput>()
        for (i in uris.indices) {
            copyOne(context, inputsDir, uris[i], i)?.let { copied.add(it) }
        }
        return copied
    }

    /** Copies one photo into [inputsDir]; null when it cannot be read. */
    private fun copyOne(context: Context, inputsDir: File, uri: Uri, index: Int): AndroidSharedInput? {
        val uriString = uri.toString()
        val displayName = AndroidUISupport.displayNameForUri(context, uriString, "Photo ${index + 1}")
        val ext = resolveExtension(context, uri, displayName)
        val destFile = File(inputsDir, "${UUID.randomUUID()}.$ext")

        try {
            val opened = if (uri.scheme == "file") {
                uri.path?.let { File(it).inputStream() }
            } else {
                context.contentResolver.openInputStream(uri)
            }
            if (opened == null) {
                Log.w(TAG, "Cannot open shared image stream")
                return null
            }
            opened.use { input ->
                destFile.outputStream().use { output ->
                    input.copyTo(output)
                }
            }
            if (destFile.length() <= 0L) {
                destFile.delete()
                return null
            }
            return AndroidSharedInput(
                urlString = Uri.fromFile(destFile).toString(),
                displayName = displayName,
            )
        } catch (t: Throwable) {
            destFile.delete()
            Log.w(TAG, "Failed copying shared image: ${t.message}")
            return null
        }
    }

    private fun resolveExtension(context: Context, uri: Uri, displayName: String): String {
        val mime = try {
            context.contentResolver.getType(uri)
        } catch (_: Throwable) {
            null
        }
        if (!mime.isNullOrBlank()) {
            val fromMime = MimeTypeMap.getSingleton().getExtensionFromMimeType(mime)
            if (!fromMime.isNullOrBlank()) {
                return fromMime
            }
        }
        val dot = displayName.lastIndexOf('.')
        if (dot >= 0 && dot + 1 < displayName.length) {
            val ext = displayName.substring(dot + 1).lowercase()
            if (ext.length in 1..8 && ext.all { it.isLetterOrDigit() }) {
                return ext
            }
        }
        return "jpg"
    }
}
