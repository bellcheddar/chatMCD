<?php
/**
 * chatMCD plugin tests. No PHPUnit, no WordPress: a shim plus assertions.
 *
 *     php wordpress/tests/test_plugin.php
 *
 * These exist because the plugin's whole job is to interpolate user-controlled
 * attributes into markup on someone else's website. Linting proves the file
 * parses; only this proves the attributes are clamped and the output escaped.
 *
 * @package chatMCD
 */

require_once __DIR__ . '/wp-shim.php';
require_once __DIR__ . '/../chatmcd/chatmcd.php';

$GLOBALS['pass'] = 0;
$GLOBALS['fail'] = 0;

/**
 * Assert a condition.
 *
 * @param string $name Test name.
 * @param bool   $cond Condition.
 * @param string $note Extra detail printed on failure.
 */
function ok( $name, $cond, $note = '' ) {
	if ( $cond ) {
		$GLOBALS['pass']++;
		printf( "  ok    %s\n", $name );
	} else {
		$GLOBALS['fail']++;
		printf( "  FAIL  %s%s\n", $name, $note ? "\n          $note" : '' );
	}
}

/**
 * Assert a substring is present.
 *
 * @param string $name     Test name.
 * @param string $haystack Output.
 * @param string $needle   Expected substring.
 */
function has( $name, $haystack, $needle ) {
	ok( $name, false !== strpos( $haystack, $needle ), "expected to find: $needle\n          in: " . substr( $haystack, 0, 400 ) );
}

/**
 * Assert a substring is absent.
 *
 * @param string $name     Test name.
 * @param string $haystack Output.
 * @param string $needle   Forbidden substring.
 */
function hasnt( $name, $haystack, $needle ) {
	ok( $name, false === strpos( $haystack, $needle ), "must not contain: $needle\n          in: " . substr( $haystack, 0, 400 ) );
}

/** Reset the singletons between groups so bubble_rendered does not leak. */
function fresh() {
	foreach ( array( 'ChatMCD', 'ChatMCD_Shortcode', 'ChatMCD_Block', 'ChatMCD_Settings' ) as $cls ) {
		$r = new ReflectionClass( $cls );
		$p = $r->getProperty( 'instance' );
		// PHP 8.1+ makes private properties reflectable without this.
		if ( PHP_VERSION_ID < 80100 ) { $p->setAccessible( true ); }
		$p->setValue( null, null );
	}
	$GLOBALS['wp_shortcodes'] = array();
	return ChatMCD::instance();
}

// -----------------------------------------------------------------------------
echo "\nactivation\n";
chatmcd_activate();
$opts = get_option( ChatMCD_Settings::OPTION );
ok( 'seeds defaults', is_array( $opts ) && CHATMCD_DEFAULT_ENDPOINT === $opts['endpoint'] );
ok( 'bubble off by default', 0 === (int) $opts['bubble_site_wide'] );

update_option( ChatMCD_Settings::OPTION, array( 'endpoint' => 'https://chatmcd.mdeller.com' ) );
chatmcd_activate();
ok(
	'does not clobber an existing endpoint',
	'https://chatmcd.mdeller.com' === get_option( ChatMCD_Settings::OPTION )['endpoint']
);

// -----------------------------------------------------------------------------
echo "\ninline shortcode\n";
update_option( ChatMCD_Settings::OPTION, ChatMCD_Settings::defaults() );
$sc = ChatMCD_Shortcode::instance( fresh() );

$out = $sc->render( array() );
has( 'renders an iframe', $out, '<iframe' );
has( 'points at /embed', $out, 'src="https://chatmcd.mdeller.com/embed"' );
has( 'lazy loads', $out, 'loading="lazy"' );
has( 'default height 560', $out, 'height:560px' );
has( 'default max-width 420', $out, 'max-width:420px' );
hasnt( 'light adds no theme param', $out, 'theme=' );
has( 'marked for autoresize', $out, 'data-chatmcd-autoresize="1"' );
ok( 'enqueued its assets', in_array( 'script:chatmcd', $GLOBALS['wp_enqueued'], true ) );

$out = $sc->render( array( 'theme' => 'dark' ) );
has( 'dark adds the theme param', $out, 'embed?theme=dark' );

$out = $sc->render( array( 'theme' => 'auto' ) );
has( 'auto adds the theme param', $out, 'embed?theme=auto' );

$out = $sc->render( array( 'theme' => 'neon' ) );
hasnt( 'unknown theme falls back to light', $out, 'theme=' );

// -----------------------------------------------------------------------------
echo "\nattribute clamping\n";
$out = $sc->render( array( 'height' => '99999', 'width' => '99999' ) );
has( 'height clamped to 1200', $out, 'height:1200px' );
has( 'width clamped to 1200', $out, 'max-width:1200px' );

$out = $sc->render( array( 'height' => '10', 'width' => '1' ) );
has( 'height floored at 240', $out, 'height:240px' );
has( 'width floored at 280', $out, 'max-width:280px' );

$out = $sc->render( array( 'height' => 'DROP TABLE', 'width' => '-500' ) );
has( 'non-numeric height floors', $out, 'height:240px' );
// absint() is abs(intval()), so a negative width becomes its magnitude and is
// then clamped like any other number. That is WordPress's own convention and it
// cannot produce anything outside the allowed range, so it is left alone.
has( 'negative width becomes its magnitude, still in range', $out, 'max-width:500px' );

$out = $sc->render( array( 'width' => '-5' ) );
has( 'a negative below the floor still floors', $out, 'max-width:280px' );

// -----------------------------------------------------------------------------
echo "\nescaping\n";
$sc  = ChatMCD_Shortcode::instance( fresh() );
$xss = '"><script>alert(1)</script>';
$out = $sc->render( array( 'mode' => 'bubble', 'label' => $xss ) );
hasnt( 'no raw script tag from label', $out, '<script>' );
hasnt( 'label cannot break out of an attribute', $out, '"><script' );
ok( 'label is present but neutralised', false !== strpos( $out, 'alert(1)' ) );

$sc  = ChatMCD_Shortcode::instance( fresh() );
$out = $sc->render( array( 'mode' => 'bubble', 'title' => '" onload="evil()' ) );
hasnt( 'title cannot inject an attribute', $out, ' onload="evil()' );

// -----------------------------------------------------------------------------
echo "\nbubble mode\n";
$sc  = ChatMCD_Shortcode::instance( fresh() );
$out = $sc->render( array( 'mode' => 'bubble' ) );
has( 'renders a launcher', $out, 'data-chatmcd-toggle' );
has( 'renders a panel', $out, 'id="chatmcd-panel"' );
has( 'panel starts hidden', $out, 'hidden' );
has( 'iframe is lazy: data-src, not src', $out, 'data-src="https://chatmcd.mdeller.com/embed"' );
ok( 'no eager src on the bubble iframe', ! preg_match( '/<iframe[^>]* src=/', $out ) );

$second = $sc->render( array( 'mode' => 'bubble' ) );
ok( 'a second bubble on the same page renders nothing', '' === $second );
ok( 'bubble_rendered is sticky', $sc->bubble_rendered() );

// -----------------------------------------------------------------------------
echo "\nsettings sanitisation\n";
$s = ChatMCD_Settings::instance();

$clean = $s->sanitize( array( 'endpoint' => 'javascript:alert(1)' ) );
ok( 'javascript: endpoint rejected', CHATMCD_DEFAULT_ENDPOINT === $clean['endpoint'] );

$clean = $s->sanitize( array( 'endpoint' => 'not a url' ) );
ok( 'garbage endpoint rejected', CHATMCD_DEFAULT_ENDPOINT === $clean['endpoint'] );

$clean = $s->sanitize( array( 'endpoint' => 'https://chat.example.com/' ) );
ok( 'valid endpoint kept, slash trimmed', 'https://chat.example.com' === $clean['endpoint'] );

$clean = $s->sanitize( array( 'theme' => 'neon' ) );
ok( 'unknown theme falls back to light', 'light' === $clean['theme'] );

$clean = $s->sanitize( array( 'height' => 5000, 'width' => 5 ) );
ok( 'height clamped', 1200 === $clean['height'] );
ok( 'width floored', 280 === $clean['width'] );

$clean = $s->sanitize( array( 'bubble_site_wide' => '1' ) );
ok( 'bubble toggle on', 1 === $clean['bubble_site_wide'] );
$clean = $s->sanitize( array() );
ok( 'bubble toggle defaults off', 0 === $clean['bubble_site_wide'] );

$clean = $s->sanitize( array( 'bubble_label' => str_repeat( 'x', 200 ) ) );
ok( 'label truncated to 40', 40 === mb_strlen( $clean['bubble_label'] ) );

$clean = $s->sanitize( 'not an array' );
ok( 'non-array input yields defaults', ChatMCD_Settings::defaults() === $clean );

// -----------------------------------------------------------------------------
echo "\ncustom endpoint\n";
update_option(
	ChatMCD_Settings::OPTION,
	array_merge( ChatMCD_Settings::defaults(), array( 'endpoint' => 'https://chat.example.com' ) )
);
$sc  = ChatMCD_Shortcode::instance( fresh() );
$out = $sc->render( array() );
has( 'uses the configured endpoint', $out, 'src="https://chat.example.com/embed"' );

// -----------------------------------------------------------------------------
echo "\nblock\n";
update_option( ChatMCD_Settings::OPTION, ChatMCD_Settings::defaults() );
$plugin = fresh();
ChatMCD_Block::instance( $plugin )->register();
ok( 'block registered', isset( $GLOBALS['wp_blocks']['chatmcd/widget'] ) );
$block = $GLOBALS['wp_blocks']['chatmcd/widget'];
ok( 'block renders server side', is_callable( $block['render_callback'] ) );
ok( 'block defaults to light', 'light' === $block['attributes']['theme']['default'] );

$out = call_user_func( $block['render_callback'], array( 'theme' => 'dark', 'height' => 700 ) );
has( 'block output matches the shortcode', $out, 'embed?theme=dark' );
has( 'block honours height', $out, 'height:700px' );

// -----------------------------------------------------------------------------
printf( "\n%d passed, %d failed\n", $GLOBALS['pass'], $GLOBALS['fail'] );
exit( $GLOBALS['fail'] > 0 ? 1 : 0 );
