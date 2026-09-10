<?php
/**
 * The [chatmcd] shortcode and the markup both delivery modes share.
 *
 * @package chatMCD
 */

defined( 'ABSPATH' ) || exit;

/**
 * Renders the widget inline or as a floating bubble.
 */
final class ChatMCD_Shortcode {

	/**
	 * Singleton instance.
	 *
	 * @var ChatMCD_Shortcode|null
	 */
	private static $instance = null;

	/**
	 * Plugin bootstrap.
	 *
	 * @var ChatMCD
	 */
	private $plugin;

	/**
	 * True once a bubble has been emitted on this request. A page may carry a
	 * bubble shortcode and have the site-wide bubble switched on; two floating
	 * launchers in the same corner is not a feature.
	 *
	 * @var bool
	 */
	private $bubble_rendered = false;

	/**
	 * Get the singleton.
	 *
	 * @param ChatMCD|null $plugin Bootstrap, required on first call.
	 * @return ChatMCD_Shortcode
	 */
	public static function instance( $plugin = null ) {
		if ( null === self::$instance ) {
			self::$instance = new self( $plugin );
		}
		return self::$instance;
	}

	/**
	 * Register the shortcode.
	 *
	 * @param ChatMCD $plugin Bootstrap.
	 */
	private function __construct( $plugin ) {
		$this->plugin = $plugin;
		add_shortcode( 'chatmcd', array( $this, 'render' ) );
	}

	/**
	 * Whether a bubble has already been rendered this request.
	 *
	 * @return bool
	 */
	public function bubble_rendered() {
		return $this->bubble_rendered;
	}

	/**
	 * Shortcode handler.
	 *
	 * @param array|string $atts Shortcode attributes.
	 * @return string
	 */
	public function render( $atts ) {
		$atts = shortcode_atts(
			array(
				'mode'   => 'inline',
				'theme'  => $this->plugin->option( 'theme', 'light' ),
				'height' => (int) $this->plugin->option( 'height', 560 ),
				'width'  => (int) $this->plugin->option( 'width', 420 ),
				'label'  => $this->plugin->option( 'bubble_label', __( 'Ask about Marc', 'chatmcd' ) ),
				'title'  => __( 'chatMCD — ask about Marc Deller', 'chatmcd' ),
			),
			$atts,
			'chatmcd'
		);

		$clean = $this->sanitize_atts( $atts );
		$this->plugin->enqueue_assets();

		if ( 'bubble' === $clean['mode'] ) {
			if ( $this->bubble_rendered ) {
				return '';
			}
			return $this->bubble_markup( $clean );
		}
		return $this->inline_markup( $clean );
	}

	/**
	 * Clamp and whitelist every attribute. Nothing reaches the markup raw.
	 *
	 * @param array $atts Raw attributes.
	 * @return array
	 */
	private function sanitize_atts( $atts ) {
		$mode  = in_array( $atts['mode'], array( 'inline', 'bubble' ), true ) ? $atts['mode'] : 'inline';
		$theme = in_array( $atts['theme'], array( 'light', 'dark', 'auto' ), true ) ? $atts['theme'] : 'light';

		return array(
			'mode'   => $mode,
			'theme'  => $theme,
			'height' => max( 240, min( 1200, absint( $atts['height'] ) ) ),
			'width'  => max( 280, min( 1200, absint( $atts['width'] ) ) ),
			'label'  => mb_substr( sanitize_text_field( $atts['label'] ), 0, 40 ),
			'title'  => sanitize_text_field( $atts['title'] ),
		);
	}

	/**
	 * The iframe itself, shared by both modes.
	 *
	 * @param array  $a     Sanitised attributes.
	 * @param string $extra Extra CSS for the element.
	 * @return string
	 */
	private function iframe( $a, $extra = '' ) {
		return sprintf(
			'<iframe class="chatmcd-frame" src="%1$s" title="%2$s" loading="lazy" style="%3$s" referrerpolicy="strict-origin-when-cross-origin"></iframe>',
			esc_url( $this->plugin->embed_url( $a['theme'] ) ),
			esc_attr( $a['title'] ),
			esc_attr( $extra )
		);
	}

	/**
	 * Inline embed.
	 *
	 * @param array $a Sanitised attributes.
	 * @return string
	 */
	private function inline_markup( $a ) {
		$style = sprintf(
			'width:100%%;max-width:%dpx;height:%dpx;border:0;border-radius:14px;display:block',
			$a['width'],
			$a['height']
		);
		return sprintf(
			'<div class="chatmcd-embed chatmcd-inline" data-chatmcd-autoresize="1">%s</div>',
			$this->iframe( $a, $style )
		);
	}

	/**
	 * Floating launcher plus the panel it opens.
	 *
	 * Public because the site-wide bubble in ChatMCD::render_site_bubble() uses
	 * the same markup. Everything inside is escaped.
	 *
	 * @param array $a Sanitised attributes.
	 * @return string
	 */
	public function bubble_markup( $a ) {
		$this->bubble_rendered = true;

		$style = sprintf(
			'width:100%%;height:%dpx;border:0;display:block',
			$a['height']
		);
		$panel_style = sprintf( 'width:min(%dpx, calc(100vw - 32px))', $a['width'] );

		ob_start();
		?>
		<div class="chatmcd-bubble" data-chatmcd-bubble>
			<button type="button" class="chatmcd-launcher" aria-expanded="false"
				aria-controls="chatmcd-panel" data-chatmcd-toggle>
				<span class="chatmcd-launcher-dot" aria-hidden="true"></span>
				<span class="chatmcd-launcher-label"><?php echo esc_html( $a['label'] ); ?></span>
			</button>

			<div class="chatmcd-panel" id="chatmcd-panel" style="<?php echo esc_attr( $panel_style ); ?>" hidden>
				<div class="chatmcd-panel-bar">
					<span><?php echo esc_html( $a['label'] ); ?></span>
					<button type="button" class="chatmcd-close" aria-label="<?php esc_attr_e( 'Close', 'chatmcd' ); ?>"
						data-chatmcd-close>&times;</button>
				</div>
				<?php
				// The iframe src is only written when the panel first opens, so a
				// visitor who never clicks the launcher never loads the widget.
				echo str_replace( ' src="', ' data-src="', $this->iframe( $a, $style ) ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped in iframe().
				?>
			</div>
		</div>
		<?php
		return trim( ob_get_clean() );
	}
}
