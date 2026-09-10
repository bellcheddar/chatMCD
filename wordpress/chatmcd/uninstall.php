<?php
/**
 * Removes the plugin's option when it is deleted (not merely deactivated).
 *
 * @package chatMCD
 */

defined( 'WP_UNINSTALL_PLUGIN' ) || exit;

delete_option( 'chatmcd_settings' );
delete_site_option( 'chatmcd_settings' );
