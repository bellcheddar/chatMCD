# Embedding chatMCD

Three ways to put chatMCD on a page, in increasing order of how much control you
get. All three load the same widget from `https://chatmcd.mdeller.com/embed`, so
they cannot drift apart.

Preview all of them, at every width and in both themes, at
[chatmcd.mdeller.com/embed/preview](https://chatmcd.mdeller.com/embed/preview).

---

## 1. Elementor (or any HTML block)

Paste this into an Elementor **HTML** widget:

```html
<iframe src="https://chatmcd.mdeller.com/embed"
        style="width:100%;max-width:420px;height:560px;border:0;border-radius:14px"
        title="chatMCD — ask about Marc Deller" loading="lazy"></iframe>
```

### Auto-resizing

The widget reports its own height to the parent page over `postMessage`. Add
this next to the iframe and it will size itself to the conversation:

```html
<script>
(function () {
  var frame = document.currentScript.previousElementSibling;
  window.addEventListener('message', function (e) {
    // Only trust messages from the widget's own origin.
    if (e.origin !== 'https://chatmcd.mdeller.com') return;
    if (!e.data || e.data.type !== 'chatmcd:height') return;
    if (e.source !== frame.contentWindow) return;
    var h = parseInt(e.data.height, 10);
    if (h >= 240 && h <= 2000) frame.style.height = h + 'px';
  });
})();
</script>
```

The origin check is not optional. Without it any page in any other frame can
resize your iframe.

---

## 2. WordPress plugin

Install `chatmcd.zip` (Plugins, Add New, Upload Plugin), activate, and use the
shortcode or the block. The plugin handles the origin check, the auto-resize and
the floating launcher for you, and it only loads its CSS and JS on pages that
actually use it.

```
[chatmcd]
[chatmcd mode="bubble" theme="dark" label="Ask about Marc"]
[chatmcd height="640" width="560"]
```

| Attribute | Values | Default | Meaning |
|---|---|---|---|
| `mode` | `inline`, `bubble` | `inline` | In the page, or a floating launcher bottom right |
| `theme` | `light`, `dark`, `auto` | `light` | Palette. `auto` follows the visitor's system setting |
| `height` | 240&ndash;1200 | 560 | Height in pixels |
| `width` | 280&ndash;1200 | 420 | Maximum width in pixels; the widget is fluid below it |
| `label` | any text, 40 chars | `Ask about Marc` | Launcher label. Bubble mode only |

Out-of-range values are clamped rather than rejected, and every attribute is
escaped, so a typo in a shortcode cannot break a page or inject markup. There is
a test for each of those in `wordpress/tests/test_plugin.php`.

A **chatMCD** block is available under Widgets with the same options in the
sidebar. It renders through the shortcode, so the two always agree.

**Settings, chatMCD** sets the defaults for every embed, and can switch on a
site-wide bubble that appears on every page without a shortcode.

### Bubble mode loads nothing until it is opened

The panel's iframe carries `data-src`, not `src`. A visitor who never clicks the
launcher never loads the widget, never contacts the Space, and pays nothing for
it. That matters on a page where the bubble is site-wide.

---

## 3. Direct link

`https://chatmcd.mdeller.com/` is the full app, with the link row, the footer and
chat export. Link to it from anywhere; it previews with an OG card.

---

## Framing rules

The widget sets `Content-Security-Policy: frame-ancestors` permitting:

- `'self'`
- `https://marcdeller.com` and `https://*.marcdeller.com`
- `https://mdeller.com` and `https://*.mdeller.com`

Any other site's iframe will be refused by the browser. To allow another origin,
add it to `FRAME_ANCESTORS` in the Flask app's environment and restart.

`/api/chat` separately allows cross-origin calls only from the origins in
`ALLOWED_ORIGINS`, and is rate limited per IP.

---

## Sizing notes

- The widget is designed and tested from **320 px** wide. Below that it still
  works but the preset chips get cramped.
- 560 px tall is the comfortable minimum: below about 460 px the transcript
  shows barely two turns.
- On a phone the plugin's bubble panel goes full screen rather than floating,
  because a 420 px card on a 390 px screen is not a card.

## Themes

Light is the default everywhere: the widget is usually embedded in a light
WordPress page, and a dark panel there reads as a hole cut in the page. The
in-widget switch lets a visitor override it, and the choice is remembered in
their browser.

`theme=auto` follows the visitor's own system setting instead, which is the
right choice if the surrounding page also does.
