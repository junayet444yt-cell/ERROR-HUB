package com.errorhub.downloader

import android.app.Activity
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import java.io.File

/** Opens the real login page of a site inside the app. After you press "Done" the login cookies are saved
 *  (cookies.txt in the app folder) and the downloader uses them for that site. Nothing leaves the phone. */
class LoginActivity : Activity() {

    private lateinit var web: WebView
    private var site = ""

    private val start = mapOf(
        "youtube" to "https://accounts.google.com/ServiceLogin?service=youtube&continue=https%3A%2F%2Fwww.youtube.com%2F",
        "instagram" to "https://www.instagram.com/accounts/login/",
        "facebook" to "https://m.facebook.com/login/",
        "reddit" to "https://www.reddit.com/login/",
        "x" to "https://x.com/i/flow/login"
    )
    // pages whose cookies are saved, and the cookie domain written for them
    private val pages = mapOf(
        "youtube" to listOf("https://www.youtube.com/", "https://youtube.com/", "https://accounts.google.com/", "https://www.google.com/", "https://myaccount.google.com/"),
        "instagram" to listOf("https://www.instagram.com/", "https://instagram.com/"),
        "facebook" to listOf("https://www.facebook.com/", "https://m.facebook.com/", "https://facebook.com/"),
        "reddit" to listOf("https://www.reddit.com/", "https://reddit.com/", "https://old.reddit.com/"),
        "x" to listOf("https://x.com/", "https://twitter.com/", "https://www.x.com/", "https://www.twitter.com/")
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        site = intent.getStringExtra("site") ?: "youtube"

        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setBackgroundColor(Color.BLACK) }
        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL; setBackgroundColor(Color.parseColor("#14102f")); setPadding(16, 12, 16, 12)
        }
        val title = TextView(this).apply {
            text = "Log in to " + site + ", then press Done"; setTextColor(Color.WHITE); textSize = 14f
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        val done = Button(this).apply { text = "Done"; setOnClickListener { saveCookies() } }
        val cancel = Button(this).apply { text = "Cancel"; setOnClickListener { setResult(RESULT_CANCELED); finish() } }
        bar.addView(title); bar.addView(cancel); bar.addView(done)

        web = WebView(this)
        root.addView(bar, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        root.addView(web, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        setContentView(root)

        val s = web.settings
        s.javaScriptEnabled = true
        s.domStorageEnabled = true
        s.cacheMode = WebSettings.LOAD_DEFAULT
        // Google refuses to show its login page to an embedded "WebView" browser, so present a normal Chrome identity
        s.userAgentString = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true)
        web.webViewClient = WebViewClient()
        web.loadUrl(start[site] ?: start["youtube"]!!)
    }

    private fun rootDomain(host: String): String {
        val p = host.split(".")
        return if (p.size >= 2) p[p.size - 2] + "." + p[p.size - 1] else host
    }

    private fun saveCookies() {
        try {
            CookieManager.getInstance().flush()
            val lines = ArrayList<String>()
            val domains = HashSet<String>()
            for (u in pages[site] ?: emptyList()) {
                val host = java.net.URL(u).host
                val dom = rootDomain(host)
                val raw = CookieManager.getInstance().getCookie(u) ?: continue
                for (part in raw.split(";")) {
                    val kv = part.trim()
                    val i = kv.indexOf('=')
                    if (i <= 0) continue
                    val name = kv.substring(0, i)
                    val value = kv.substring(i + 1)
                    domains.add(dom)
                    lines.add(".$dom\tTRUE\t/\tTRUE\t2147483647\t$name\t$value")
                }
            }
            if (lines.isEmpty()) {
                Toast.makeText(this, "No login found. Log in first, then press Done.", Toast.LENGTH_LONG).show()
                return
            }
            val f = File(filesDir, "cookies.txt")
            val keep = ArrayList<String>()
            if (f.exists()) {
                for (l in f.readLines()) {
                    val d = l.substringBefore('\t')
                    if (l.startsWith("#") || l.isBlank()) continue
                    if (domains.none { d == ".$it" || d == it }) keep.add(l)
                }
            }
            f.writeText("# Netscape HTTP Cookie File\n" + (keep + lines.distinct()).joinToString("\n") + "\n")
            Toast.makeText(this, "Login saved for $site", Toast.LENGTH_LONG).show()
            setResult(RESULT_OK, intent)
            finish()
        } catch (e: Exception) {
            Toast.makeText(this, "Could not save the login: " + e.message, Toast.LENGTH_LONG).show()
        }
    }

    @Deprecated("old API, still works")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else { setResult(RESULT_CANCELED); super.onBackPressed() }
    }
}
