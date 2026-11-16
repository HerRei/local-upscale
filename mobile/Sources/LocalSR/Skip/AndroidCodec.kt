package local.sr

import android.content.Context
import android.graphics.Bitmap
import android.graphics.ColorSpace
import android.graphics.ImageDecoder
import android.net.Uri
import androidx.exifinterface.media.ExifInterface
import local.srcore.SizePolicy
import java.io.BufferedOutputStream
import java.io.File
import java.io.FileOutputStream
import kotlin.math.sqrt

/** Bitmap-backed pixel access for the tiling loop. */
class BitmapSource(private val bitmap: Bitmap) : PixelSource {
    override val width: Int get() = bitmap.width
    override val height: Int get() = bitmap.height
    override fun readRegion(x: Int, y: Int, w: Int, h: Int, out: IntArray) = bitmap.getPixels(out, 0, w, x, y, w, h)
}

class BitmapSink(private val bitmap: Bitmap) : PixelSink {
    override fun writeRegion(x: Int, y: Int, w: Int, h: Int, pixels: IntArray) = bitmap.setPixels(pixels, 0, w, x, y, w, h)
}

/** Decode, JPEG encode, preview and EXIF copy. */
object AndroidCodec {
    /** Largest default output the app produces: one 4032x3024 photo at 2x = 48,771,072 pixels (~48.8 MP). */
    const val MAX_OUTPUT_PIXELS = 48_771_072L
    const val JPEG_QUALITY = 95
    const val PREVIEW_MAX_SIDE = 4096

    /**
     * Size the input is decoded at: SizePolicy's prescale (input <= 3 MP, so 4x lands at <= 48 MP), plus a
     * hard cap here as a safety net against an Android-side OOM (a no-op while SizePolicy keeps that promise).
     */
    fun decodeSize(width: Int, height: Int): Pair<Int, Int> {
        val t = SizePolicy.target(width = width, height = height)
        return Pair(t.width, t.height)
    }

    private fun source(context: Context, uri: Uri): ImageDecoder.Source =
        if (uri.scheme == null || uri.scheme == "file") ImageDecoder.createSource(File(uri.path ?: throw UpscaleException("Bad file path $uri")))
        else ImageDecoder.createSource(context.contentResolver, uri)

    /**
     * Decodes with EXIF orientation applied, in sRGB, as a software ARGB_8888 bitmap no larger than
     * [decodeSize]. Wide-gamut sources are converted to sRGB (v1 limitation).
     */
    fun decode(context: Context, uri: Uri, preserveDimensions: Boolean = false): Bitmap {
        try {
            val bmp = ImageDecoder.decodeBitmap(source(context, uri)) { decoder, info, _ ->
                decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
                decoder.isMutableRequired = false
                decoder.setTargetColorSpace(ColorSpace.get(ColorSpace.Named.SRGB))
                val (w, h) = decodeSize(info.size.width, info.size.height)
                if (!preserveDimensions && (w != info.size.width || h != info.size.height)) decoder.setTargetSize(w, h)
            }
            return if (bmp.config == Bitmap.Config.ARGB_8888) bmp else (bmp.copy(Bitmap.Config.ARGB_8888, false) ?: throw UpscaleException("Cannot convert the decoded image to ARGB_8888")).also { bmp.recycle() }
        } catch (e: UpscaleException) {
            throw e
        } catch (e: OutOfMemoryError) {
            throw UpscaleException("Not enough memory to decode the photo", e)
        } catch (e: Exception) {
            throw UpscaleException("Cannot decode the photo: ${e.message}", e)
        }
    }

    fun writeJpeg(bitmap: Bitmap, file: File, quality: Int = JPEG_QUALITY) {
        file.parentFile?.mkdirs()
        BufferedOutputStream(FileOutputStream(file), 1 shl 16).use { out ->
            if (!bitmap.compress(Bitmap.CompressFormat.JPEG, quality, out)) throw UpscaleException("JPEG encoding failed")
        }
    }

    fun writePng(bitmap: Bitmap, file: File) {
        file.parentFile?.mkdirs()
        BufferedOutputStream(FileOutputStream(file), 1 shl 16).use { out ->
            if (!bitmap.compress(Bitmap.CompressFormat.PNG, 100, out)) throw UpscaleException("PNG encoding failed")
        }
    }

    /** Downscales to a long side <= [PREVIEW_MAX_SIDE] by repeated halving (box filter) plus one final bilinear step. Never recycles [bitmap]. */
    fun preview(bitmap: Bitmap): Bitmap {
        val longSide = maxOf(bitmap.width, bitmap.height)
        if (longSide <= PREVIEW_MAX_SIDE) return bitmap
        val f = PREVIEW_MAX_SIDE.toDouble() / longSide
        val tw = maxOf(1, (bitmap.width * f).toInt())
        val th = maxOf(1, (bitmap.height * f).toInt())
        var cur = bitmap
        while (cur.width / 2 >= tw && cur.height / 2 >= th) {
            val next = Bitmap.createScaledBitmap(cur, cur.width / 2, cur.height / 2, true)
            if (cur !== bitmap) cur.recycle()
            cur = next
        }
        val last = Bitmap.createScaledBitmap(cur, tw, th, true)
        if (cur !== bitmap) cur.recycle()
        return last
    }

    // MARK: EXIF

    private val COPIED_TAGS = arrayOf(
        ExifInterface.TAG_DATETIME, ExifInterface.TAG_DATETIME_ORIGINAL, ExifInterface.TAG_DATETIME_DIGITIZED,
        ExifInterface.TAG_SUBSEC_TIME, ExifInterface.TAG_SUBSEC_TIME_ORIGINAL, ExifInterface.TAG_SUBSEC_TIME_DIGITIZED,
        ExifInterface.TAG_OFFSET_TIME, ExifInterface.TAG_OFFSET_TIME_ORIGINAL, ExifInterface.TAG_OFFSET_TIME_DIGITIZED,
        ExifInterface.TAG_MAKE, ExifInterface.TAG_MODEL, ExifInterface.TAG_LENS_MAKE, ExifInterface.TAG_LENS_MODEL,
        ExifInterface.TAG_LENS_SPECIFICATION, ExifInterface.TAG_BODY_SERIAL_NUMBER,
        ExifInterface.TAG_F_NUMBER, ExifInterface.TAG_EXPOSURE_TIME, ExifInterface.TAG_PHOTOGRAPHIC_SENSITIVITY,
        ExifInterface.TAG_APERTURE_VALUE, ExifInterface.TAG_SHUTTER_SPEED_VALUE, ExifInterface.TAG_BRIGHTNESS_VALUE,
        ExifInterface.TAG_EXPOSURE_BIAS_VALUE, ExifInterface.TAG_MAX_APERTURE_VALUE, ExifInterface.TAG_METERING_MODE,
        ExifInterface.TAG_LIGHT_SOURCE, ExifInterface.TAG_FLASH, ExifInterface.TAG_FOCAL_LENGTH,
        ExifInterface.TAG_FOCAL_LENGTH_IN_35MM_FILM, ExifInterface.TAG_EXPOSURE_PROGRAM, ExifInterface.TAG_EXPOSURE_MODE,
        ExifInterface.TAG_WHITE_BALANCE, ExifInterface.TAG_SCENE_CAPTURE_TYPE, ExifInterface.TAG_DIGITAL_ZOOM_RATIO,
        ExifInterface.TAG_SENSING_METHOD, ExifInterface.TAG_SUBJECT_DISTANCE, ExifInterface.TAG_SUBJECT_DISTANCE_RANGE,
        ExifInterface.TAG_IMAGE_DESCRIPTION, ExifInterface.TAG_ARTIST, ExifInterface.TAG_COPYRIGHT,
        ExifInterface.TAG_GPS_VERSION_ID, ExifInterface.TAG_GPS_LATITUDE, ExifInterface.TAG_GPS_LATITUDE_REF,
        ExifInterface.TAG_GPS_LONGITUDE, ExifInterface.TAG_GPS_LONGITUDE_REF, ExifInterface.TAG_GPS_ALTITUDE,
        ExifInterface.TAG_GPS_ALTITUDE_REF, ExifInterface.TAG_GPS_TIMESTAMP, ExifInterface.TAG_GPS_DATESTAMP,
        ExifInterface.TAG_GPS_SPEED, ExifInterface.TAG_GPS_SPEED_REF, ExifInterface.TAG_GPS_IMG_DIRECTION,
        ExifInterface.TAG_GPS_IMG_DIRECTION_REF, ExifInterface.TAG_GPS_TRACK, ExifInterface.TAG_GPS_TRACK_REF,
        ExifInterface.TAG_GPS_DOP, ExifInterface.TAG_GPS_MAP_DATUM, ExifInterface.TAG_GPS_PROCESSING_METHOD,
        ExifInterface.TAG_GPS_AREA_INFORMATION, ExifInterface.TAG_GPS_STATUS, ExifInterface.TAG_GPS_SATELLITES,
        ExifInterface.TAG_GPS_MEASURE_MODE, ExifInterface.TAG_GPS_H_POSITIONING_ERROR,
    )

    /**
     * Copies date, GPS and camera fields from the original into [output] (a JPEG written by [writeJpeg]) and sets
     * the new pixel dimensions, orientation normal (the pixels are already rotated) and Software.
     * Missing or unreadable source EXIF is not an error: the output just gets the dimensions.
     */
    fun copyExif(context: Context, input: Uri, output: File, width: Int, height: Int) {
        val source: ExifInterface? = try {
            val stream = if (input.scheme == null || input.scheme == "file") java.io.FileInputStream(File(input.path ?: "")) else context.contentResolver.openInputStream(input)
            stream?.use { ExifInterface(it) }
        } catch (e: Exception) {
            android.util.Log.w("LocalSR", "cannot read EXIF of the input: ${e.message}")
            null
        }
        val dest = ExifInterface(output)
        if (source != null) {
            for (tag in COPIED_TAGS) {
                val v = source.getAttribute(tag)
                if (v != null) dest.setAttribute(tag, v)
            }
        }
        dest.setAttribute(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL.toString())
        dest.setAttribute(ExifInterface.TAG_PIXEL_X_DIMENSION, width.toString())
        dest.setAttribute(ExifInterface.TAG_PIXEL_Y_DIMENSION, height.toString())
        dest.setAttribute(ExifInterface.TAG_IMAGE_WIDTH, width.toString())
        dest.setAttribute(ExifInterface.TAG_IMAGE_LENGTH, height.toString())
        dest.setAttribute(ExifInterface.TAG_SOFTWARE, "LocalSR")
        dest.saveAttributes()
    }
}
