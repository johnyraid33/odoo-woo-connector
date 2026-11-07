<?php
/**
 * Creates a WooCommerce REST API key pair (read_write) for the dev stack and
 * prints it as JSON:
 *   { "base_url": "...", "consumer_key": "ck_...", "consumer_secret": "cs_...", "generated_at": "..." }
 *
 * Run inside the wpcli container:
 *   docker compose exec -T wpcli wp --path=/var/www/html eval-file /opt/scripts/generate-credentials.php
 *
 * The key is inserted the same way the WooCommerce admin UI does it: a row in
 * {prefix}woocommerce_api_keys with a ck_/cs_ pair and read_write permissions.
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
$description = 'Odoo connector dev key';

if ( ! $wpdb->get_var( $wpdb->prepare( 'SHOW TABLES LIKE %s', $table ) ) ) {
	WP_CLI::error( 'Table ' . $table . ' does not exist — is WooCommerce fully installed?' );
}

// Re-running must not pile up keys: drop previous dev keys first.
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
		'consumer_key'    => $consumer_key,
		'consumer_secret' => $consumer_secret,
		'truncated_key'   => substr( $consumer_key, -7 ),
	),
	array( '%d', '%s', '%s', '%s', '%s', '%s' )
);
if ( false === $inserted ) {
	WP_CLI::error( 'Could not insert API key: ' . $wpdb->last_error );
}

// The connector authenticates with Basic auth, which WooCommerce accepts only
// over SSL, so base_url points at the stack's HTTPS endpoint (WOO_API_BASE_URL
// is set on the wpcli service in docker-compose.yml). When the variable is not
// set we fall back to home_url() and print a warning to stderr.
$base_url = getenv( 'WOO_API_BASE_URL' );
if ( ! $base_url ) {
	$base_url = untrailingslashit( home_url() );
	fwrite( STDERR, 'Warning: WOO_API_BASE_URL is not set, using home_url() (Basic auth will fail over HTTP).' . PHP_EOL );
}

$data = array(
	'base_url'        => untrailingslashit( $base_url ),
	'consumer_key'    => $consumer_key,
	'consumer_secret' => $consumer_secret,
	'generated_at'    => gmdate( 'c' ),
);

echo wp_json_encode( $data, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES ) . PHP_EOL;
