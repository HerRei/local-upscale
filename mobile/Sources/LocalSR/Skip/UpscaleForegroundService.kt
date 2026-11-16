package local.sr

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.withTimeout
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat

/**
 * Keeps the process alive and shows a progress notification while photos are being upscaled, so a batch
 * survives leaving the app. The work itself runs in the app's coroutines (AndroidUpscaler); this service only
 * holds the foreground state. Type mediaProcessing on API 35+, dataSync before. Reference counted: the first
 * acquire starts it, the last release stops it. Declared in Android/app/src/main/AndroidManifest.xml.
 *
 * A foreground service keeps the process alive but not the CPU awake, so while it runs it also holds a partial
 * wake lock (the batch keeps going with the screen off). The lock has a time limit that every progress update
 * extends, so a stuck job cannot keep the phone awake; it is released with the last release, when the service
 * is destroyed and when the system ends it (onTimeout).
 *
 * The UI must ask for POST_NOTIFICATIONS on API 33+ (without it the service still runs, the notification is hidden).
 */
class UpscaleForegroundService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        // startForeground in onCreate: a stopService right after startForegroundService must not trip the 5 s rule.
        try {
            ensureChannel(this)
            val notification = buildNotification(this, state)
            // mediaProcessing is the right type on API 35+; fall back to dataSync if the system refuses it.
            val types = if (Build.VERSION.SDK_INT >= 35) listOf(ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROCESSING, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC) else listOf(ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
            var started = false
            var last: Throwable? = null
            for (type in types) {
                try {
                    ServiceCompat.startForeground(this, NOTIFICATION_ID, notification, type)
                    Log.i(TAG, "foreground service started with type 0x${Integer.toHexString(type)}")
                    started = true
                    break
                } catch (t: Throwable) {
                    Log.w(TAG, "startForeground(type 0x${Integer.toHexString(type)}) refused: ${t.javaClass.simpleName}: ${t.message}")
                    last = t
                }
            }
            if (!started) throw last ?: IllegalStateException("no foreground type")
            synchronized(lock) { startResult?.complete(Unit) }
        } catch (t: Throwable) {
            // e.g. ForegroundServiceStartNotAllowedException when the app is not in a state that may start one.
            Log.w(TAG, "cannot start foreground: ${t.javaClass.simpleName}: ${t.message}")
            synchronized(lock) { startResult?.completeExceptionally(t) }
            stopSelf()
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_NOT_STICKY

    /** API 35+: the system gives mediaProcessing services a time budget; stop instead of being killed. */
    override fun onTimeout(startId: Int, fgsType: Int) {
        Log.w(TAG, "foreground service timeout (type $fgsType)")
        synchronized(lock) { releaseWakeLockLocked() }
        stopSelf()
    }

    override fun onDestroy() {
        synchronized(lock) {
            running = false
            releaseWakeLockLocked()
        }
        super.onDestroy()
    }

    private class State(
        var fraction: Double = 0.0,
        var done: Int = 0,
        var total: Int = 0,
        var title: String? = null,
        var detail: String? = null,
    )

    companion object {
        private const val TAG = "LocalSR"
        private const val CHANNEL_ID = "localsr_progress"
        private const val NOTIFICATION_ID = 4711
        private val lock = Any()
        private var holders = 0
        private var running = false
        private var startResult: CompletableDeferred<Unit>? = null
        private val state = State()
        private var wakeLock: PowerManager.WakeLock? = null
        private var wakeLockExtendedAt = 0L
        private var lastNotifiedAt = 0L
        private var contentIntent: PendingIntent? = null

        /** Safety net: the wake lock ends this long after the last progress update. */
        private const val WAKE_LOCK_TIMEOUT_MS = 10 * 60 * 1000L
        /** Progress updates extend the wake lock at most this often (each extension is a binder call). */
        private const val WAKE_LOCK_EXTEND_MS = 60 * 1000L
        /** Tile progress rebuilds the notification at most this often (Android drops faster updates anyway). */
        private const val NOTIFY_INTERVAL_MS = 500L

        /** Wait until Android confirms foreground execution before starting work. */
        suspend fun acquire(context: Context) {
            val result = synchronized(lock) {
                if (holders == 0) {
                    state.fraction = 0.0
                    state.done = 0
                    state.total = 0
                    state.title = null
                    state.detail = null
                    startResult = CompletableDeferred()
                    try {
                        ContextCompat.startForegroundService(context, Intent(context, UpscaleForegroundService::class.java))
                        running = true
                        holdWakeLockLocked(context)
                    } catch (t: Throwable) {
                        startResult!!.completeExceptionally(t)
                    }
                }
                holders++
                startResult!!
            }
            try {
                withTimeout(10_000) { result.await() }
            } catch (t: Throwable) {
                release(context)
                throw UpscaleException("Cannot start foreground execution for this batch.", t)
            }
        }

        /** Call when the work ended (success, failure or cancel). */
        fun release(context: Context) {
            synchronized(lock) {
                holders = maxOf(0, holders - 1)
                if (holders == 0) {
                    releaseWakeLockLocked()
                    if (running) {
                        context.stopService(Intent(context, UpscaleForegroundService::class.java))
                        running = false
                    }
                }
            }
        }

        /** Tells the notification which photo of the batch is being processed ("Photo 2 of 5"). Optional. */
        fun setBatch(context: Context, done: Int, total: Int) {
            synchronized(lock) { state.done = done; state.total = total }
            refresh(context, force = true)
        }

        /** Called after every tile; rebuilds the notification at most every [NOTIFY_INTERVAL_MS]. */
        fun updateProgress(context: Context, fraction: Double) {
            synchronized(lock) { state.fraction = fraction }
            refresh(context, force = false)
        }

        fun setStatus(context: Context, title: String, detail: String?, fraction: Double) {
            val titleChanged = synchronized(lock) {
                val changed = state.title != title
                state.title = title
                state.detail = detail
                state.fraction = fraction
                changed
            }
            refresh(context, force = titleChanged)
        }

        private fun refresh(context: Context, force: Boolean) {
            val notification = synchronized(lock) {
                if (!running) return
                holdWakeLockLocked(context)
                val now = SystemClock.elapsedRealtime()
                if (!force && now - lastNotifiedAt < NOTIFY_INTERVAL_MS) return
                lastNotifiedAt = now
                try {
                    buildNotification(context, state)
                } catch (t: Throwable) {
                    Log.w(TAG, "cannot build the notification: ${t.message}")
                    return
                }
            }
            try {
                val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                nm.notify(NOTIFICATION_ID, notification)
            } catch (t: Throwable) {
                Log.w(TAG, "cannot update the notification: ${t.message}")
            }
        }

        /** Takes the partial wake lock, or extends its time limit (at most once a minute). Call with [lock] held. */
        private fun holdWakeLockLocked(context: Context) {
            try {
                val now = SystemClock.elapsedRealtime()
                val current = wakeLock
                if (current != null && current.isHeld && now - wakeLockExtendedAt < WAKE_LOCK_EXTEND_MS) return
                val wl = current ?: (context.applicationContext.getSystemService(Context.POWER_SERVICE) as PowerManager)
                    .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "LocalSR:upscale")
                    .also { it.setReferenceCounted(false); wakeLock = it }
                // Not reference counted: acquiring again only moves the time limit.
                wl.acquire(WAKE_LOCK_TIMEOUT_MS)
                wakeLockExtendedAt = now
            } catch (t: Throwable) {
                Log.w(TAG, "cannot hold a wake lock: ${t.message}")
            }
        }

        private fun releaseWakeLockLocked() {
            val wl = wakeLock ?: return
            wakeLock = null
            try {
                if (wl.isHeld) wl.release()
            } catch (t: Throwable) {
                Log.w(TAG, "cannot release the wake lock: ${t.message}")
            }
        }

        /** The app's status-bar glyph (res/drawable/ic_stat_localsr.xml in the app module), not the system download arrow. */
        private fun smallIcon(context: Context): Int {
            val id = context.resources.getIdentifier("ic_stat_localsr", "drawable", context.packageName)
            return if (id != 0) id else context.applicationInfo.icon
        }

        private fun ensureChannel(context: Context) {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            if (nm.getNotificationChannel(CHANNEL_ID) == null) {
                nm.createNotificationChannel(NotificationChannel(CHANNEL_ID, "Upscaling", NotificationManager.IMPORTANCE_LOW))
            }
        }

        private fun buildNotification(context: Context, s: State): Notification {
            val pct = (s.fraction * 100).toInt().coerceIn(0, 100)
            val fallbackText = if (s.total > 1) "Photo ${minOf(s.done + 1, s.total)} of ${s.total}, $pct %" else "$pct %"
            val title = s.title ?: "Upscaling photos"
            val text = s.detail ?: fallbackText
            val content = contentIntent ?: context.packageManager.getLaunchIntentForPackage(context.packageName)
                ?.let { PendingIntent.getActivity(context, 0, it, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT) }
                ?.also { contentIntent = it }
            return NotificationCompat.Builder(context, CHANNEL_ID)
                .setSmallIcon(smallIcon(context))
                .setContentTitle(title)
                .setContentText(text)
                .setProgress(100, pct, false)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setSilent(true)
                .setCategory(NotificationCompat.CATEGORY_PROGRESS)
                .setContentIntent(content)
                .build()
        }
    }
}
