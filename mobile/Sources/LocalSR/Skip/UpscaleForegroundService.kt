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
        stopSelf()
    }

    override fun onDestroy() {
        synchronized(lock) { running = false }
        super.onDestroy()
    }

    private class State(var fraction: Double = 0.0, var done: Int = 0, var total: Int = 0)

    companion object {
        private const val TAG = "LocalSR"
        private const val CHANNEL_ID = "localsr_progress"
        private const val NOTIFICATION_ID = 4711
        private val lock = Any()
        private var holders = 0
        private var running = false
        private var startResult: CompletableDeferred<Unit>? = null
        private val state = State()

        /** Wait until Android confirms foreground execution before starting work. */
        suspend fun acquire(context: Context) {
            val result = synchronized(lock) {
                if (holders == 0) {
                    state.fraction = 0.0
                    startResult = CompletableDeferred()
                    try {
                        ContextCompat.startForegroundService(context, Intent(context, UpscaleForegroundService::class.java))
                        running = true
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
                if (holders == 0 && running) {
                    context.stopService(Intent(context, UpscaleForegroundService::class.java))
                    running = false
                }
            }
        }

        /** Tells the notification which photo of the batch is being processed ("Photo 2 of 5"). Optional. */
        fun setBatch(context: Context, done: Int, total: Int) {
            synchronized(lock) { state.done = done; state.total = total }
            refresh(context)
        }

        fun updateProgress(context: Context, fraction: Double) {
            synchronized(lock) { state.fraction = fraction }
            refresh(context)
        }

        private fun refresh(context: Context) {
            val isRunning = synchronized(lock) { running }
            if (!isRunning) return
            try {
                val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                nm.notify(NOTIFICATION_ID, buildNotification(context, state))
            } catch (t: Throwable) {
                Log.w(TAG, "cannot update the notification: ${t.message}")
            }
        }

        private fun ensureChannel(context: Context) {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            if (nm.getNotificationChannel(CHANNEL_ID) == null) {
                nm.createNotificationChannel(NotificationChannel(CHANNEL_ID, "Upscaling", NotificationManager.IMPORTANCE_LOW))
            }
        }

        private fun buildNotification(context: Context, s: State): Notification {
            val pct = (s.fraction * 100).toInt().coerceIn(0, 100)
            val text = if (s.total > 1) "Photo ${minOf(s.done + 1, s.total)} of ${s.total}, $pct %" else "$pct %"
            val launch = context.packageManager.getLaunchIntentForPackage(context.packageName)
            val content = launch?.let { PendingIntent.getActivity(context, 0, it, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT) }
            return NotificationCompat.Builder(context, CHANNEL_ID)
                .setSmallIcon(android.R.drawable.stat_sys_download)
                .setContentTitle("Upscaling photos")
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
