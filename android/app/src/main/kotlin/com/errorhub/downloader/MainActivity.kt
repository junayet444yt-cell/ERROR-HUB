package com.errorhub.downloader

import android.app.Activity
import android.content.ClipboardManager
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.URLUtil
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import java.net.HttpURLConnection
import java.net.ServerSocket
import java.net.URL
import kotlin.concurrent.thread

class MainActivity : Activity() {

    companion object {
        private var port = 0          // the downloader server lives as long as the app process
        private var starting = false
    }

    private lateinit var web: WebView
    private var fileCb: ValueCallback<Array<Uri>>? = null
    private val reqFile = 4711

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        web = WebView(this)
        setContentView(web)
        setupWebView()
        web.loadData("<html><body style='background:#08020f;color:#e8dcc0;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>Starting ERROR HUB…</body></html>", "text/html", "utf-8")
        startServer()
    }

    private fun setupWebView() {
        val s = web.settings
        s.javaScriptEnabled = true
        s.domStorageEnabled = true
        s.databaseEnabled = true
        s.mediaPlaybackRequiresUserGesture = false
        s.allowFileAccess = false
        s.cacheMode = WebSettings.LOAD_DEFAULT
        web.addJavascriptInterface(Bridge(), "AndroidBridge")
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val u = request.url
                if (u.host == "127.0.0.1") return false
                try { startActivity(Intent(Intent.ACTION_VIEW, u)) } catch (e: Exception) { }   // Discord / Facebook open in the browser
                return true
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(w: WebView, cb: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                fileCb?.onReceiveValue(null)
                fileCb = cb
                return try {
                    startActivityForResult(params.createIntent(), reqFile)
                    true
                } catch (e: Exception) {
                    fileCb = null
                    false
                }
            }
        }
        web.setDownloadListener { url, _, contentDisposition, mime, _ -> saveToDownloads(url, contentDisposition, mime) }
    }

    @Deprecated("old API, still works")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == reqFile) {
            fileCb?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data))
            fileCb = null
        }
    }

    private inner class Bridge {
        @JavascriptInterface
        fun paste(): String {
            return try {
                val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                cm.primaryClip?.getItemAt(0)?.coerceToText(this@MainActivity)?.toString() ?: ""
            } catch (e: Exception) { "" }
        }
    }

    private fun startServer() {
        if (port == 0 && !starting) {
            starting = true
            val p = ServerSocket(0).use { it.localPort }
            val staticDir = File(filesDir, "static")
            thread {
                try {
                    if (!Python.isStarted()) Python.start(AndroidPlatform(applicationContext))   // first start unpacks Python: do it off the UI thread
                    staticDir.deleteRecursively()
                    copyAssets("static", staticDir)
                    Python.getInstance().getModule("android_main").callAttr(
                        "start", p, File(cacheDir, "vcache").absolutePath, staticDir.absolutePath, filesDir.absolutePath)
                    port = p
                } catch (e: Exception) {
                    runOnUiThread { Toast.makeText(this, "Could not start: " + e.message, Toast.LENGTH_LONG).show() }
                }
            }
        }
        thread {
            var tries = 0
            while (tries++ < 150) {                       // wait up to ~45 s for the server
                if (port != 0) {
                    try {
                        val c = URL("http://127.0.0.1:$port/health").openConnection() as HttpURLConnection
                        c.connectTimeout = 500; c.readTimeout = 1000
                        if (c.responseCode == 200) {
                            runOnUiThread { web.loadUrl("http://127.0.0.1:$port/") }
                            return@thread
                        }
                    } catch (e: Exception) { }
                }
                Thread.sleep(300)
            }
            runOnUiThread { Toast.makeText(this, "The downloader did not start. Close the app and open it again.", Toast.LENGTH_LONG).show() }
        }
    }

    private fun copyAssets(from: String, to: File) {
        val list = assets.list(from) ?: return
        if (list.isEmpty()) {                              // a file
            to.parentFile?.mkdirs()
            assets.open(from).use { i -> to.outputStream().use { o -> i.copyTo(o) } }
        } else {
            to.mkdirs()
            for (name in list) copyAssets("$from/$name", File(to, name))
        }
    }

    private fun saveToDownloads(url: String, contentDisposition: String?, mime: String?) {
        thread {
            try {
                val conn = URL(url).openConnection() as HttpURLConnection
                val type = mime ?: conn.contentType ?: "video/mp4"
                val name = URLUtil.guessFileName(url, contentDisposition ?: conn.getHeaderField("Content-Disposition"), type)
                val values = ContentValues().apply {
                    put(MediaStore.Downloads.DISPLAY_NAME, name)
                    put(MediaStore.Downloads.MIME_TYPE, type)
                    put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
                }
                val uri = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                    ?: throw Exception("cannot create file")
                runOnUiThread { Toast.makeText(this, "Saving $name …", Toast.LENGTH_SHORT).show() }
                conn.inputStream.use { i -> contentResolver.openOutputStream(uri)!!.use { o -> i.copyTo(o) } }
                runOnUiThread { Toast.makeText(this, "Saved in Downloads: $name", Toast.LENGTH_LONG).show() }
            } catch (e: Exception) {
                runOnUiThread { Toast.makeText(this, "Save failed: " + e.message, Toast.LENGTH_LONG).show() }
            }
        }
    }

    @Deprecated("old API, still works")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else super.onBackPressed()
    }
}
