<?php
/**
 * Plugin Name:       chatMCD
 * Plugin URI:        https://chatmcd.mdeller.com/
 * Description:       Embeds chatMCD, the assistant that answers questions about Marc C. Deller, as an inline block or a floating chat bubble.
 * Version:           1.0.0
 * Requires at least: 6.2
 * Requires PHP:      7.4
 * Author:            Marc C. Deller
 * Author URI:        https://marcdeller.com/
 * License:           GPL-2.0-or-later
 * License URI:       https://www.gnu.org/licenses/gpl-2.0.html
 * Text Domain:       chatmcd
 *
 * @package chatMCD
 */

defined( 'ABSPATH' ) || exit;

define( 'CHATMCD_VERSION', '1.0.0' );
define( 'CHATMCD_FILE', __FILE__ );
define( 'CHATMCD_DIR', plugin_dir_path( __FILE__ ) );
define( 'CHATMCD_URL', plugin_dir_url( __FILE__ ) );

/** The public API. Everything the plugin renders points here. */
define( 'CHATMCD_DEFAULT_ENDPOINT', 'https://chatmcd.mdeller.com' );

require_once CHATMCD_DIR . 'includes/class-chatmcd-settings.php';
require_once CHATMCD_DIR . 'includes/class-chatmcd-shortcode.php';
require_once CHATMCD_DIR . 'includes/class-chatmcd-block.php';

/**
 * Plugin bootstrap.
 */
final class ChatMCD {

	/**
	 * Singleton instance.
	 *
	 * @var ChatMCD|null
	 */
	private static $instance = null;

	/**
	 * True once assets have been enqueued, so a page with three shortcodes
	 * still only loads one copy.
	 *
	 * @var bool
	 */
	private $assets_done = false;

	/**
	 * Get the singleton.
	 *
	 * @return ChatMCD
	 */
	public static function instance() {
		if ( null === self::$instance ) {
			self::$instance = new self();
		}
		return self::$instance;
	}

	/**
	 * Wire the hooks.
	 */
	private function __construct() {
		ChatMCD_Settings::instance();
		ChatMCD_Shortcode::instance( $this );
		ChatMCD_Block::instance( $this );

		add_action( 'wp_footer', array( $this, 'render_site_bubble' ) );
		add_filter( 'plugin_action_links_' . plugin_basename( CHATMCD_FILE ), array( $this, 'action_links' ) );
	}

	/**
	 * Read a setting, falling back to the registered default.
	 *
	 * @param string $key     Option key.
	 * @param mixed  $default Fallback.
	 * @return mixed
	 */
	public function option( $key, $default = '' ) {
		$opts = get_option( ChatMCD_Settings::OPTION, array() );
		return isset( $opts[ $key ] ) && '' !== $opts[ $key ] ? $opts[ $key ] : $default;
	}

	/**
	 * The chatMCD endpoint, normalised without a trailing slash.
	 *
	 * @return string
	 */
	public function endpoint() {
		$url = $this->option( 'endpoint', CHATMCD_DEFAULT_ENDPOINT );
		return untrailingslashit( esc_url_raw( $url ) );
	}

	/**
	 * Build the widget URL for a given theme.
	 *
	 * @param string $theme light|dark|auto.
	 * @return string
	 */
	public function embed_url( $theme ) {
		$url = $this->endpoint() . '/embed';
		if ( in_array( $theme, array( 'dark', 'auto' ), true ) ) {
			$url = add_query_arg( 'theme', $theme, $url );
		}
		return $url;
	}

	/**
	 * Enqueue the front-end assets, once per request, and only when something
	 * on the page actually needs them.
	 */
	public function enqueue_assets() {
		if ( $this->assets_done ) {
			return;
		}
		$this->assets_done = true;

		wp_enqueue_style(
			'chatmcd',
			CHATMCD_URL . 'assets/chatmcd.css',
			array(),
			CHATMCD_VERSION
		);
		wp_enqueue_script(
			'chatmcd',
			CHATMCD_URL . 'assets/chatmcd.js',
			array(),
			CHATMCD_VERSION,
			true
		);
		wp_localize_script(
			'chatmcd',
			'chatmcdSettings',
			array(
				'origin' => $this->endpoint(),
			)
		);
	}

	/**
	 * Render the site-wide floating bubble, if it is switched on.
	 */
	public function render_site_bubble() {
		if ( ! $this->option( 'bubble_site_wide', '0' ) || is_admin() ) {
			return;
		}
		if ( ChatMCD_Shortcode::instance( $this )->bubble_rendered() ) {
			return; // A [chatmcd mode="bubble"] on this page already did it.
		}
		$this->enqueue_assets();
		echo ChatMCD_Shortcode::instance( $this )->bubble_markup( // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped inside.
			array(
				'theme'  => $this->option( 'theme', 'light' ),
				'height' => (int) $this->option( 'height', 560 ),
				'width'  => (int) $this->option( 'width', 420 ),
				'label'  => $this->option( 'bubble_label', __( 'Ask about Marc', 'chatmcd' ) ),
			)
		);
	}

	/**
	 * Add a Settings link on the plugins screen.
	 *
	 * @param array $links Existing links.
	 * @return array
	 */
	public function action_links( $links ) {
		$url = admin_url( 'options-general.php?page=chatmcd' );
		array_unshift(
			$links,
			'<a href="' . esc_url( $url ) . '">' . esc_html__( 'Settings', 'chatmcd' ) . '</a>'
		);
		return $links;
	}
}

/**
 * Activation: seed the defaults without clobbering an existing configuration.
 */
function chatmcd_activate() {
	$existing = get_option( ChatMCD_Settings::OPTION, array() );
	if ( ! is_array( $existing ) ) {
		$existing = array();
	}
	add_option( ChatMCD_Settings::OPTION, array_merge( ChatMCD_Settings::defaults(), $existing ) );
}
register_activation_hook( __FILE__, 'chatmcd_activate' );

/**
 * Deactivation: nothing to tear down. Settings survive, and uninstall.php
 * removes them if the plugin is deleted rather than merely switched off.
 */
function chatmcd_deactivate() {
	// Intentionally empty.
}
register_deactivation_hook( __FILE__, 'chatmcd_deactivate' );

add_action( 'plugins_loaded', array( 'ChatMCD', 'instance' ) );
