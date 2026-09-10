<?php
/**
 * Settings screen under Settings -> chatMCD.
 *
 * @package chatMCD
 */

defined( 'ABSPATH' ) || exit;

/**
 * Registers and renders the plugin's options.
 */
final class ChatMCD_Settings {

	const OPTION = 'chatmcd_settings';
	const GROUP  = 'chatmcd_settings_group';

	/**
	 * Singleton instance.
	 *
	 * @var ChatMCD_Settings|null
	 */
	private static $instance = null;

	/**
	 * Get the singleton.
	 *
	 * @return ChatMCD_Settings
	 */
	public static function instance() {
		if ( null === self::$instance ) {
			self::$instance = new self();
		}
		return self::$instance;
	}

	/**
	 * Default values, also used to seed the option on activation.
	 *
	 * @return array
	 */
	public static function defaults() {
		return array(
			'endpoint'         => CHATMCD_DEFAULT_ENDPOINT,
			'height'           => 560,
			'width'            => 420,
			'theme'            => 'light',
			'bubble_site_wide' => 0,
			'bubble_label'     => 'Ask about Marc',
		);
	}

	/**
	 * Wire the hooks.
	 */
	private function __construct() {
		add_action( 'admin_menu', array( $this, 'add_page' ) );
		add_action( 'admin_init', array( $this, 'register' ) );
	}

	/**
	 * Add the options page.
	 */
	public function add_page() {
		add_options_page(
			__( 'chatMCD', 'chatmcd' ),
			__( 'chatMCD', 'chatmcd' ),
			'manage_options',
			'chatmcd',
			array( $this, 'render' )
		);
	}

	/**
	 * Register the setting and its fields.
	 */
	public function register() {
		register_setting(
			self::GROUP,
			self::OPTION,
			array(
				'type'              => 'array',
				'sanitize_callback' => array( $this, 'sanitize' ),
				'default'           => self::defaults(),
			)
		);

		add_settings_section(
			'chatmcd_main',
			__( 'Widget', 'chatmcd' ),
			function () {
				echo '<p>' . esc_html__(
					'These values are the defaults. Any shortcode attribute overrides them for that one embed.',
					'chatmcd'
				) . '</p>';
			},
			'chatmcd'
		);

		$fields = array(
			'endpoint'         => __( 'API base URL', 'chatmcd' ),
			'height'           => __( 'Default height (px)', 'chatmcd' ),
			'width'            => __( 'Maximum width (px)', 'chatmcd' ),
			'theme'            => __( 'Theme', 'chatmcd' ),
			'bubble_site_wide' => __( 'Floating bubble', 'chatmcd' ),
			'bubble_label'     => __( 'Bubble label', 'chatmcd' ),
		);
		foreach ( $fields as $key => $label ) {
			add_settings_field(
				$key,
				$label,
				array( $this, 'field_' . $key ),
				'chatmcd',
				'chatmcd_main'
			);
		}
	}

	/**
	 * Current value of one option.
	 *
	 * @param string $key Option key.
	 * @return mixed
	 */
	private function value( $key ) {
		$opts = wp_parse_args( get_option( self::OPTION, array() ), self::defaults() );
		return $opts[ $key ];
	}

	/** Endpoint field. */
	public function field_endpoint() {
		printf(
			'<input type="url" class="regular-text code" name="%s[endpoint]" value="%s" placeholder="%s"><p class="description">%s</p>',
			esc_attr( self::OPTION ),
			esc_attr( $this->value( 'endpoint' ) ),
			esc_attr( CHATMCD_DEFAULT_ENDPOINT ),
			esc_html__( 'Where the chatMCD Flask app lives. Leave as the default unless you are running your own.', 'chatmcd' )
		);
	}

	/** Height field. */
	public function field_height() {
		printf(
			'<input type="number" min="240" max="1200" step="10" name="%s[height]" value="%d"> px',
			esc_attr( self::OPTION ),
			(int) $this->value( 'height' )
		);
	}

	/** Width field. */
	public function field_width() {
		printf(
			'<input type="number" min="280" max="1200" step="10" name="%s[width]" value="%d"> px<p class="description">%s</p>',
			esc_attr( self::OPTION ),
			(int) $this->value( 'width' ),
			esc_html__( 'The widget is responsive down to 320 px; this is the cap, not a fixed size.', 'chatmcd' )
		);
	}

	/** Theme field. */
	public function field_theme() {
		$current = $this->value( 'theme' );
		$choices = array(
			'light' => __( 'Light (default)', 'chatmcd' ),
			'dark'  => __( 'Dark', 'chatmcd' ),
			'auto'  => __( 'Follow the visitor\'s system setting', 'chatmcd' ),
		);
		echo '<select name="' . esc_attr( self::OPTION ) . '[theme]">';
		foreach ( $choices as $val => $label ) {
			printf(
				'<option value="%s"%s>%s</option>',
				esc_attr( $val ),
				selected( $current, $val, false ),
				esc_html( $label )
			);
		}
		echo '</select>';
	}

	/** Site-wide bubble toggle. */
	public function field_bubble_site_wide() {
		printf(
			'<label><input type="checkbox" name="%s[bubble_site_wide]" value="1"%s> %s</label>',
			esc_attr( self::OPTION ),
			checked( (int) $this->value( 'bubble_site_wide' ), 1, false ),
			esc_html__( 'Show a floating chatMCD launcher on every page', 'chatmcd' )
		);
	}

	/** Bubble label. */
	public function field_bubble_label() {
		printf(
			'<input type="text" class="regular-text" name="%s[bubble_label]" value="%s" maxlength="40">',
			esc_attr( self::OPTION ),
			esc_attr( $this->value( 'bubble_label' ) )
		);
	}

	/**
	 * Sanitise every field. Nothing reaches the option that has not been through here.
	 *
	 * @param array $input Raw submitted values.
	 * @return array
	 */
	public function sanitize( $input ) {
		$out = self::defaults();
		if ( ! is_array( $input ) ) {
			return $out;
		}

		if ( isset( $input['endpoint'] ) ) {
			$url = esc_url_raw( trim( $input['endpoint'] ), array( 'http', 'https' ) );
			$out['endpoint'] = $url ? untrailingslashit( $url ) : CHATMCD_DEFAULT_ENDPOINT;
		}
		if ( isset( $input['height'] ) ) {
			$out['height'] = max( 240, min( 1200, absint( $input['height'] ) ) );
		}
		if ( isset( $input['width'] ) ) {
			$out['width'] = max( 280, min( 1200, absint( $input['width'] ) ) );
		}
		if ( isset( $input['theme'] ) && in_array( $input['theme'], array( 'light', 'dark', 'auto' ), true ) ) {
			$out['theme'] = $input['theme'];
		}
		$out['bubble_site_wide'] = empty( $input['bubble_site_wide'] ) ? 0 : 1;
		if ( isset( $input['bubble_label'] ) ) {
			$label = sanitize_text_field( $input['bubble_label'] );
			$out['bubble_label'] = '' === $label ? 'Ask about Marc' : mb_substr( $label, 0, 40 );
		}
		return $out;
	}

	/**
	 * Render the options page.
	 */
	public function render() {
		if ( ! current_user_can( 'manage_options' ) ) {
			return;
		}
		?>
		<div class="wrap">
			<h1><?php esc_html_e( 'chatMCD', 'chatmcd' ); ?></h1>
			<p>
				<?php esc_html_e( 'chatMCD answers questions about Marc C. Deller, from his own papers, patents, thesis and notes.', 'chatmcd' ); ?>
			</p>
			<form action="options.php" method="post">
				<?php
				settings_fields( self::GROUP );
				do_settings_sections( 'chatmcd' );
				submit_button();
				?>
			</form>

			<hr>
			<h2><?php esc_html_e( 'Shortcode', 'chatmcd' ); ?></h2>
			<p><?php esc_html_e( 'Drop this anywhere a shortcode works. Every attribute is optional.', 'chatmcd' ); ?></p>
			<table class="widefat striped" style="max-width:820px">
				<thead>
					<tr>
						<th><?php esc_html_e( 'Attribute', 'chatmcd' ); ?></th>
						<th><?php esc_html_e( 'Values', 'chatmcd' ); ?></th>
						<th><?php esc_html_e( 'Meaning', 'chatmcd' ); ?></th>
					</tr>
				</thead>
				<tbody>
					<tr><td><code>mode</code></td><td><code>inline</code> | <code>bubble</code></td><td><?php esc_html_e( 'In the page, or a floating launcher bottom right.', 'chatmcd' ); ?></td></tr>
					<tr><td><code>theme</code></td><td><code>light</code> | <code>dark</code> | <code>auto</code></td><td><?php esc_html_e( 'Palette. Light is the default.', 'chatmcd' ); ?></td></tr>
					<tr><td><code>height</code></td><td>240&ndash;1200</td><td><?php esc_html_e( 'Height in pixels.', 'chatmcd' ); ?></td></tr>
					<tr><td><code>width</code></td><td>280&ndash;1200</td><td><?php esc_html_e( 'Maximum width in pixels.', 'chatmcd' ); ?></td></tr>
					<tr><td><code>label</code></td><td><?php esc_html_e( 'any text', 'chatmcd' ); ?></td><td><?php esc_html_e( 'Bubble launcher label. Bubble mode only.', 'chatmcd' ); ?></td></tr>
				</tbody>
			</table>
			<p><code>[chatmcd]</code></p>
			<p><code>[chatmcd mode="bubble" theme="dark" label="Ask about Marc"]</code></p>
			<p><code>[chatmcd height="640" width="560"]</code></p>
		</div>
		<?php
	}
}
