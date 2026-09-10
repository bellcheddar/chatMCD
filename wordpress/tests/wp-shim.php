<?php
/**
 * The smallest WordPress that runs the chatMCD plugin.
 *
 * Only the functions the plugin actually calls, with the semantics that matter
 * for the tests: escaping really escapes, absint really casts, shortcode_atts
 * really merges. Anything purely administrative (menus, settings sections) is a
 * no-op, because nothing about the rendered output depends on it.
 *
 * @package chatMCD
 */

define( 'ABSPATH', __DIR__ . '/' );

$GLOBALS['wp_options']    = array();
$GLOBALS['wp_actions']    = array();
$GLOBALS['wp_shortcodes'] = array();
$GLOBALS['wp_enqueued']   = array();

// -- hooks and registration (no-ops; the tests call the methods directly) -----
function add_action( $h, $cb, $p = 10, $a = 1 ) { $GLOBALS['wp_actions'][ $h ][] = $cb; }
function add_filter( $h, $cb, $p = 10, $a = 1 ) { $GLOBALS['wp_actions'][ $h ][] = $cb; }
function add_shortcode( $tag, $cb ) { $GLOBALS['wp_shortcodes'][ $tag ] = $cb; }
function register_activation_hook( $f, $cb ) {}
function register_deactivation_hook( $f, $cb ) {}
function add_options_page( ...$a ) {}
function add_settings_section( ...$a ) {}
function add_settings_field( ...$a ) {}
function register_setting( ...$a ) {}
function settings_fields( $g ) {}
function do_settings_sections( $p ) {}
function submit_button( ...$a ) {}
function register_block_type( $name, $args = array() ) { $GLOBALS['wp_blocks'][ $name ] = $args; }
function wp_register_script( ...$a ) {}
function wp_enqueue_style( $h, ...$a ) { $GLOBALS['wp_enqueued'][] = "style:$h"; }
function wp_enqueue_script( $h, ...$a ) { $GLOBALS['wp_enqueued'][] = "script:$h"; }
function wp_localize_script( ...$a ) {}
function is_admin() { return false; }
function current_user_can( $c ) { return true; }
function admin_url( $p = '' ) { return 'https://example.com/wp-admin/' . $p; }
function plugin_dir_path( $f ) { return dirname( $f ) . '/'; }
function plugin_dir_url( $f ) { return 'https://example.com/wp-content/plugins/chatmcd/'; }
function plugin_basename( $f ) { return 'chatmcd/chatmcd.php'; }

// -- options ------------------------------------------------------------------
function get_option( $k, $d = false ) {
	return array_key_exists( $k, $GLOBALS['wp_options'] ) ? $GLOBALS['wp_options'][ $k ] : $d;
}
function add_option( $k, $v ) {
	if ( ! array_key_exists( $k, $GLOBALS['wp_options'] ) ) {
		$GLOBALS['wp_options'][ $k ] = $v;
	}
}
function update_option( $k, $v ) { $GLOBALS['wp_options'][ $k ] = $v; }
function delete_option( $k ) { unset( $GLOBALS['wp_options'][ $k ] ); }
function delete_site_option( $k ) { delete_option( $k ); }

// -- strings, escaping, sanitising -------------------------------------------
function __( $s, $d = '' ) { return $s; }
function _e( $s, $d = '' ) { echo $s; }
function esc_html__( $s, $d = '' ) { return esc_html( $s ); }
function esc_html_e( $s, $d = '' ) { echo esc_html( $s ); }
function esc_attr_e( $s, $d = '' ) { echo esc_attr( $s ); }
function esc_html( $s ) { return htmlspecialchars( (string) $s, ENT_QUOTES, 'UTF-8' ); }
function esc_attr( $s ) { return htmlspecialchars( (string) $s, ENT_QUOTES, 'UTF-8' ); }
function esc_url( $u ) { return esc_attr( esc_url_raw( $u ) ); }
function esc_url_raw( $u, $protocols = array( 'http', 'https' ) ) {
	$u = trim( (string) $u );
	$scheme = strtolower( (string) parse_url( $u, PHP_URL_SCHEME ) );
	if ( '' === $u || ! in_array( $scheme, $protocols, true ) ) {
		return '';
	}
	return filter_var( $u, FILTER_VALIDATE_URL ) ? $u : '';
}
function sanitize_text_field( $s ) {
	return trim( preg_replace( '/[\r\n\t]+|<[^>]*>/', '', (string) $s ) );
}
function absint( $v ) { return abs( (int) $v ); }
function untrailingslashit( $s ) { return rtrim( (string) $s, '/\\' ); }
function trailingslashit( $s ) { return untrailingslashit( $s ) . '/'; }
function selected( $a, $b, $echo = true ) {
	$r = (string) $a === (string) $b ? ' selected="selected"' : '';
	if ( $echo ) { echo $r; }
	return $r;
}
function checked( $a, $b, $echo = true ) {
	$r = (string) $a === (string) $b ? ' checked="checked"' : '';
	if ( $echo ) { echo $r; }
	return $r;
}
function wp_parse_args( $args, $defaults = array() ) {
	return array_merge( $defaults, is_array( $args ) ? $args : array() );
}
function shortcode_atts( $pairs, $atts, $shortcode = '' ) {
	$atts = (array) $atts;
	$out  = array();
	foreach ( $pairs as $name => $default ) {
		$out[ $name ] = array_key_exists( $name, $atts ) ? $atts[ $name ] : $default;
	}
	return $out;
}
function add_query_arg( $key, $value, $url ) {
	$sep = ( strpos( $url, '?' ) === false ) ? '?' : '&';
	return $url . $sep . rawurlencode( $key ) . '=' . rawurlencode( $value );
}
