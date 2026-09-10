/* chatMCD Gutenberg block.
 *
 * No JSX and no build step: this is plain ES5 against wp.element.createElement,
 * so the plugin ships as a zip that WordPress can install with nothing else.
 * The block renders server-side through the shortcode, so the editor only needs
 * to collect attributes and show a placeholder.
 */
(function (blocks, element, components, blockEditor, i18n) {
  'use strict';

  var el = element.createElement;
  var __ = i18n.__;
  var InspectorControls = blockEditor.InspectorControls;
  var useBlockProps = blockEditor.useBlockProps;

  blocks.registerBlockType('chatmcd/widget', {
    edit: function (props) {
      var a = props.attributes;
      var set = props.setAttributes;

      var controls = el(
        InspectorControls, {},
        el(components.PanelBody, { title: __('chatMCD', 'chatmcd'), initialOpen: true },
          el(components.SelectControl, {
            label: __('Mode', 'chatmcd'),
            value: a.mode,
            options: [
              { label: __('Inline', 'chatmcd'), value: 'inline' },
              { label: __('Floating bubble', 'chatmcd'), value: 'bubble' }
            ],
            onChange: function (v) { set({ mode: v }); }
          }),
          el(components.SelectControl, {
            label: __('Theme', 'chatmcd'),
            value: a.theme,
            options: [
              { label: __('Light (default)', 'chatmcd'), value: 'light' },
              { label: __('Dark', 'chatmcd'), value: 'dark' },
              { label: __('Follow the visitor', 'chatmcd'), value: 'auto' }
            ],
            onChange: function (v) { set({ theme: v }); }
          }),
          el(components.RangeControl, {
            label: __('Height (px)', 'chatmcd'),
            value: a.height, min: 240, max: 1200, step: 10,
            onChange: function (v) { set({ height: v }); }
          }),
          el(components.RangeControl, {
            label: __('Maximum width (px)', 'chatmcd'),
            value: a.width, min: 280, max: 1200, step: 10,
            onChange: function (v) { set({ width: v }); }
          }),
          a.mode === 'bubble' && el(components.TextControl, {
            label: __('Bubble label', 'chatmcd'),
            value: a.label, maxLength: 40,
            onChange: function (v) { set({ label: v }); }
          })
        )
      );

      var summary = a.mode === 'bubble'
        ? __('Floating launcher, bottom right', 'chatmcd')
        : a.width + ' x ' + a.height + ' px';

      var placeholder = el(
        components.Placeholder,
        {
          icon: 'format-chat',
          label: 'chatMCD',
          instructions: __('Renders on the front end. Marc\'s assistant answers questions about his research, publications and companies.', 'chatmcd')
        },
        el('p', { style: { margin: 0, opacity: .7 } }, summary + ' · ' + a.theme)
      );

      return el('div', useBlockProps(), controls, placeholder);
    },

    // Rendered by PHP, so nothing is saved to post content but the attributes.
    save: function () { return null; }
  });
})(window.wp.blocks, window.wp.element, window.wp.components, window.wp.blockEditor, window.wp.i18n);
