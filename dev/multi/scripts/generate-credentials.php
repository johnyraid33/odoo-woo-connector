<?php
/**
 * Creates a WooCommerce REST API key pair (read_write) for the multi stack and
 * prints it as JSON:
 *   { "base_url": "...", "consumer_key": "ck_...", "consumer_secret": "cs_...",
 *     "woo_version": "8.9.1", "generated_at": "..." }
 *
 * Run inside the wpcli-multi container:
 *   docker compose exec -T wpcli-multi wp --path=/var/www/html eval-file /opt/scripts/generate-credentials.php
 *
 * The key is inserted the same way the WooCommerce admin UI does it: a row in
 * {prefix}woocommerce_api_keys with a ck_/cs_ pair and read_write permissions.
 * WooCommerce 8.9 authenticates Basic auth only over SSL (WC_REST_Authentication
 * checks is_ssl()), so base_url is the container-network HTTPS address the
 * connector uses: https://wordpress-multi (never plain HTTP).
 */

global $wpdb;

if ( ! class_exists( 'WooCommerce' ) ) {
	WP_CLI::error( 'WooCommerce is not active.' );
}

$user = get_user_by( 'login', 'admin' );
if ( ! $user ) {
	$user = get_user_by( 'email', 'admin@example.com' );
}
if ( ! $user ) {
	WP_CLI::error( 'Admin user (admin / admin@example.com) not found.' );
}

$table       = $wpdb->prefix . 'woocommerce_api_keys';
$description = 'Odoo connector multi-store dev key';

if ( ! $wpdb->get_var( $wpdb->prepare( 'SHOW TABLES LIKE %s', $table ) ) ) {
	WP_CLI::error( 'Table ' . $table . ' does not exist — is WooCommerce fully installed?' );
}

// Re-running must not pile up keys: drop previous multi-store dev keys first.
$wpdb->query( $wpdb->prepare( "DELETE FROM {$table} WHERE description = %s", $description ) );

$random = function_exists( 'wc_rand_hash' ) ? wc_rand_hash() : bin2hex( random_bytes( 20 ) );
$consumer_key    = 'ck_' . $random;
$consumer_secret = 'cs_' . ( function_exists( 'wc_rand_hash' ) ? wc_rand_hash() : bin2hex( random_bytes( 20 ) ) );

$inserted = $wpdb->insert(
	$table,
	array(
		'user_id'         => (int) $user->ID,
		'description'     => $description,
		'permissions'     => 'read_write',
		// WooCommerce stores the consumer key hashed (wc_api_hash) and looks it
		// up the same way; the secret is stored as-is and compared with
		// hash_equals(). This mirrors WC_Auth::create_keys().
		'consumer_key'    => wc_api_hash( $consumer_key ),
		'consumer_secret' => $consumer_secret,
		'truncated_key'   => substr( $consumer_key, -7 ),
	),
	array( '%d', '%s', '%s', '%s', '%s', '%s' )
);
if ( false === $inserted ) {
	WP_CLI::error( 'Could not insert API key: ' . $wpdb->last_error );
}

// base_url is the address the Odoo connector uses FROM the main stack's Odoo
// container (docker network alias wordpress-multi). WOO_API_BASE_URL is set on
// the wpcli-multi service in docker-compose.yml.
$base_url = getenv( 'WOO_API_BASE_URL' );
if ( ! $base_url ) {
	$base_url = 'https://wordpress-multi';
	fwrite( STDERR, 'Warning: WOO_API_BASE_URL is not set, assuming https://wordpress-multi.' . PHP_EOL );
}

// The host-side address is recorded too: the loopback HTTPS port cannot be
// used by the Odoo container, but it is what curl / tests on the workstation use.
$host_base_url = getenv( 'WOO_API_HOST_URL' );
if ( ! $host_base_url ) {
	$host_base_url = 'https://localhost:8444';
}

$data = array(
	'base_url'        => untrailingslashit( $base_url ),
	'host_base_url'   => untrailingslashit( $host_base_url ),
	'consumer_key'    => $consumer_key,
	'consumer_secret' => $consumer_secret,
	'woo_version'     => defined( 'WC_VERSION' ) ? WC_VERSION : '',
	'generated_at'    => gmdate( 'c' ),
);

echo wp_json_encode( $data, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES ) . PHP_EOL;
