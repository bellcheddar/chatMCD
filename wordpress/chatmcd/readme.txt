=== chatMCD ===
Contributors: marcdeller
Tags: chatbot, ai, assistant, shortcode, embed
Requires at least: 6.2
Tested up to: 6.7
Requires PHP: 7.4
Stable tag: 1.0.0
License: GPLv2 or later
License URI: https://www.gnu.org/licenses/gpl-2.0.html

Embeds chatMCD, the assistant that answers questions about Marc C. Deller, as an inline block or a floating chat bubble.

== Description ==

chatMCD answers from Marc C. Deller's own papers, patents, thesis and notes. It answers questions about his research, his 400+
protein structures, his patents and his companies, in the third person, and says
so rather than guessing when a question is outside what it knows.

This plugin embeds the widget. The model itself runs at chatmcd.mdeller.com; no
credentials or personal data are stored in WordPress and the plugin makes no
server-side requests of its own.

= Two modes =

* **Inline** places the widget in the page, sized by the shortcode or block.
* **Bubble** adds a floating launcher in the bottom right that opens the widget
  in a panel. The widget is only loaded when a visitor actually opens it.

= Shortcode =

`[chatmcd]`
`[chatmcd mode="bubble" theme="dark" label="Ask about Marc"]`
`[chatmcd height="640" width="560"]`

| Attribute | Values | Meaning |
| --------- | ------ | ------- |
| mode   | inline, bubble | In the page, or a floating launcher |
| theme  | light, dark, auto | Palette; light is the default |
| height | 240 to 1200 | Height in pixels |
| width  | 280 to 1200 | Maximum width in pixels |
| label  | any text | Bubble launcher label, bubble mode only |

= Block =

A **chatMCD** block is available under Widgets, with the same options in the
sidebar. It renders through the same code path as the shortcode.

== Installation ==

1. Upload `chatmcd.zip` through Plugins, Add New, Upload Plugin.
2. Activate.
3. Optionally visit Settings, chatMCD to change the defaults or switch on the
   site-wide bubble.

== Privacy ==

The plugin stores one option row and sets no cookies. Questions typed into the
widget are sent to chatmcd.mdeller.com, which logs the question text (with email
addresses and phone numbers scrubbed) to find gaps in the model's knowledge. No
answer text and no visitor identifier is stored.

== Changelog ==

= 1.0.0 =
* First release: shortcode, block, inline and bubble modes, settings screen.
