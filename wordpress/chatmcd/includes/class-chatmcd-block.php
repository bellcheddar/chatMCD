<?php
/**
 * Gutenberg block. Wraps the same shortcode, so the two can never drift.
 *
 * @package chatMCD
 */

defined( 'ABSPATH' ) || exit;

/**
 * Registers the chatmcd/widget block.
 */
final class ChatMCD_Block {

	/**
	 * Singleton instance.
	 *
	 * @var ChatMCD_Block|null
	 */
	private static $instance = null;

	/**
	 * Plugin bootstrap.
	 *
	 * @var ChatMCD
	 */
	private $plugin;

	/**
	 * Get the singleton.
	 *
	 * @param ChatMCD|null $plugin Bootstrap, required on first call.
	 * @return ChatMCD_Block
	 */
	public static function instance( $plugin = null ) {
		if ( null === self::$instance ) {
			self::$instance = new self( $plugin );
		}
		return self::$instance;
	}

	/**
	 * Wire the hooks.
	 *
	 * @param ChatMCD $plugin Bootstrap.
	 */
	private function __construct( $plugin ) {
		$this->plugin = $plugin;
		add_action( 'init', array( $this, 'register' ) );
	}

	/**
	 * Register the block type and its editor script.
	 */
	public function register() {
		if ( ! function_exists( 'register_block_type' ) ) {
			return;
		}

		wp_register_script(
			'chatmcd-block',
			CHATMCD_URL . 'assets/block.js',
			array( 'wp-blocks', 'wp-element', 'wp-components', 'wp-block-editor', 'wp-i18n' ),
			CHATMCD_VERSION,
			true
		);

		register_block_type(
			'chatmcd/widget',
			array(
				'api_version'     => 3,
				'title'           => __( 'chatMCD', 'chatmcd' ),
				'description'     => __( 'Ask-about-Marc chat widget, inline or as a floating bubble.', 'chatmcd' ),
				'category'        => 'widgets',
				'icon'            => 'format-chat',
				'keywords'        => array( 'chat', 'marc', 'deller', 'assistant' ),
				'editor_script'   => 'chatmcd-block',
				'attributes'      => array(
					'mode'   => array(
						'type'    => 'string',
						'default' => 'inline',
					),
					'theme'  => array(
						'type'    => 'string',
						'default' => 'light',
					),
					'height' => array(
						'type'    => 'number',
						'default' => 560,
					),
					'width'  => array(
						'type'    => 'number',
						'default' => 420,
					),
					'label'  => array(
						'type'    => 'string',
						'default' => 'Ask about Marc',
					),
				),
				'render_callback' => array( $this, 'render' ),
			)
		);
	}

	/**
	 * Server-side render. Delegates to the shortcode so there is exactly one
	 * implementation of the markup.
	 *
	 * @param array $attributes Block attributes.
	 * @return string
	 */
	public function render( $attributes ) {
		return ChatMCD_Shortcode::instance( $this->plugin )->render(
			array(
				'mode'   => isset( $attributes['mode'] ) ? $attributes['mode'] : 'inline',
				'theme'  => isset( $attributes['theme'] ) ? $attributes['theme'] : 'light',
				'height' => isset( $attributes['height'] ) ? $attributes['height'] : 560,
				'width'  => isset( $attributes['width'] ) ? $attributes['width'] : 420,
				'label'  => isset( $attributes['label'] ) ? $attributes['label'] : 'Ask about Marc',
			)
		);
	}
}
