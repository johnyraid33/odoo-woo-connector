<?php
/**
 * Seed a realistic WooCommerce store for the Odoo connector dev stack.
 *
 * Run inside the wpcli container (from the host):
 *   dev.sh seed
 *   # or directly:
 *   docker compose exec -T wpcli wp --path=/var/www/html eval-file /opt/scripts/seed-catalog.php
 *
 * Idempotent: objects are looked up by SKU / email / coupon code / option
 * registry (products, customers, orders, refunds) and updated in place, so the
 * script can be run on every `dev.sh seed` without duplicating data.
 */

global $wpdb;
global $seed;

if ( ! class_exists( 'WooCommerce' ) ) {
	WP_CLI::error( 'WooCommerce is not active — run scripts/seed-wordpress.sh first.' );
}

$seed = get_option( 'woo_connector_dev_seed', array() );
if ( ! is_array( $seed ) ) {
	$seed = array();
}
$seed += array(
	'products'  => array(),
	'customers' => array(),
	'orders'    => array(),
	'refunds'   => array(),
);

/** Persist the object registry so re-runs update instead of duplicating. */
function dev_seed_save() {
	global $seed;
	update_option( 'woo_connector_dev_seed', $seed, false );
}

/** Create the product category once, reuse it afterwards. */
function dev_seed_category( $name ) {
	$term = get_term_by( 'name', $name, 'product_cat' );
	if ( $term && ! is_wp_error( $term ) ) {
		return (int) $term->term_id;
	}
	$created = wp_insert_term( $name, 'product_cat' );
	if ( is_wp_error( $created ) ) {
		WP_CLI::warning( 'Could not create product category "' . $name . '": ' . $created->get_error_message() );
		return 0;
	}
	return (int) $created['term_id'];
}

function dev_seed_find_product( $key, $sku = '' ) {
	global $seed;
	$id = isset( $seed['products'][ $key ] ) ? (int) $seed['products'][ $key ] : 0;
	if ( $id ) {
		$product = wc_get_product( $id );
		if ( $product ) {
			return $product;
		}
	}
	if ( $sku ) {
		$id = wc_get_product_id_by_sku( $sku );
		if ( $id ) {
			$product = wc_get_product( $id );
			if ( $product ) {
				return $product;
			}
		}
	}
	return null;
}

function dev_seed_simple( $key, array $data ) {
	global $seed;
	$product = dev_seed_find_product( $key, $data['sku'] );
	if ( ! $product ) {
		$product = new WC_Product_Simple();
	}
	$product->set_name( $data['name'] );
	$product->set_slug( $data['slug'] );
	if ( '' !== $data['sku'] ) {
		$product->set_sku( $data['sku'] );
	}
	$product->set_status( 'publish' );
	$product->set_catalog_visibility( 'visible' );
	$product->set_regular_price( $data['regular_price'] );
	$product->set_sale_price( isset( $data['sale_price'] ) ? $data['sale_price'] : '' );
	$product->set_tax_status( 'taxable' );
	// '' is the WooCommerce "Standard" tax class slug.
	$product->set_tax_class( isset( $data['tax_class'] ) ? $data['tax_class'] : '' );
	$product->set_manage_stock( true );
	$product->set_stock_quantity( $data['stock'] );
	$product->set_stock_status( 'instock' );
	$product->set_short_description( $data['short_description'] );
	$product->set_description( $data['description'] );
	if ( ! empty( $data['categories'] ) ) {
		$product->set_category_ids( array_values( array_filter( $data['categories'] ) ) );
	}
	$product->save();
	$seed['products'][ $key ] = $product->get_id();
	dev_seed_save();
	WP_CLI::log( sprintf(
		'  product #%d  %-22s  sku=%-16s  price=%s',
		$product->get_id(),
		$data['name'],
		'' !== $data['sku'] ? $data['sku'] : '(none)',
		$product->get_price()
	) );
	return $product;
}

function dev_seed_variable() {
	global $seed;
	$product = dev_seed_find_product( 'hoodie', 'WOO-HOODIE' );
	if ( ! $product || ! $product->is_type( 'variable' ) ) {
		$product = new WC_Product_Variable();
	}
	$product->set_name( 'Premium Hoodie' );
	$product->set_slug( 'premium-hoodie' );
	$product->set_sku( 'WOO-HOODIE' );
	$product->set_status( 'publish' );
	$product->set_catalog_visibility( 'visible' );
	$product->set_tax_status( 'taxable' );
	$product->set_tax_class( '' );
	$product->set_short_description( 'Heavyweight hoodie with a kangaroo pocket.' );
	$product->set_description( 'Brushed-back cotton fleece, unisex cut, two sizes in stock.' );
	$product->set_category_ids( array( dev_seed_category( 'Clothing' ) ) );

	$attribute = new WC_Product_Attribute();
	$attribute->set_id( 0 ); // 0 = custom (local) attribute, not a global taxonomy.
	$attribute->set_name( 'Size' );
	$attribute->set_options( array( 'S', 'M' ) );
	$attribute->set_position( 0 );
	$attribute->set_visible( true );
	$attribute->set_variation( true );
	$product->set_attributes( array( $attribute ) );
	$product->save();
	$seed['products']['hoodie'] = $product->get_id();
	dev_seed_save();

	// Reuse existing variations (matched by the Size attribute) on re-runs.
	$existing = array();
	foreach ( $product->get_children() as $child_id ) {
		$variation = wc_get_product( $child_id );
		if ( ! $variation ) {
			continue;
		}
		$attrs = $variation->get_attributes();
		$size  = isset( $attrs['size'] ) ? strtoupper( $attrs['size'] ) : '';
		if ( $size ) {
			$existing[ $size ] = $variation;
		}
	}

	$variations = array(
		'S' => array( 'price' => '49.00', 'stock' => 7 ),
		'M' => array( 'price' => '54.00', 'stock' => 3 ),
	);
	foreach ( $variations as $size => $info ) {
		$variation = isset( $existing[ $size ] ) ? $existing[ $size ] : new WC_Product_Variation();
		$variation->set_parent_id( $product->get_id() );
		$variation->set_attributes( array( 'size' => $size ) );
		$variation->set_sku( 'WOO-HOODIE-' . $size );
		$variation->set_regular_price( $info['price'] );
		$variation->set_manage_stock( true );
		$variation->set_stock_quantity( $info['stock'] );
		$variation->set_stock_status( 'instock' );
		$variation->set_status( 'publish' );
		$variation->save();
		WP_CLI::log( sprintf( '  variation #%d  Premium Hoodie / %s  %s EUR', $variation->get_id(), $size, $info['price'] ) );
	}

	$product->save();
	WC_Product_Variable::sync( $product->get_id() );
	WP_CLI::log( sprintf( '  variable product #%d  Premium Hoodie  (%d variations)', $product->get_id(), count( $variations ) ) );
	return $product;
}

/**
 * German VAT rates (19% standard, 7% reduced). Inserted directly because
 * WooCommerce has no stable public CRUD object for tax rates in every version.
 */
function dev_seed_tax_rates() {
	global $wpdb;
	$table = $wpdb->prefix . 'woocommerce_tax_rates';
	if ( ! $wpdb->get_var( $wpdb->prepare( 'SHOW TABLES LIKE %s', $table ) ) ) {
		WP_CLI::warning( 'woocommerce_tax_rates table not found, skipping tax rates.' );
		return;
	}
	$rates = array(
		array( 'rate' => '19.0000', 'name' => 'VAT 19%', 'class' => '' ),
		array( 'rate' => '7.0000', 'name' => 'Reduced VAT 7%', 'class' => 'reduced-rate' ),
	);
	foreach ( $rates as $rate ) {
		$exists = $wpdb->get_var( $wpdb->prepare(
			"SELECT tax_rate_id FROM {$table} WHERE tax_rate_country = %s AND tax_rate_class = %s AND tax_rate = %s",
			'DE',
			$rate['class'],
			$rate['rate']
		) );
		if ( $exists ) {
			continue;
		}
		$wpdb->insert(
			$table,
			array(
				'tax_rate_country'  => 'DE',
				'tax_rate_state'    => '',
				'tax_rate'          => $rate['rate'],
				'tax_rate_name'     => $rate['name'],
				'tax_rate_priority' => 1,
				'tax_rate_compound' => 0,
				'tax_rate_shipping' => 1,
				'tax_rate_order'    => 0,
				'tax_rate_class'    => $rate['class'],
			)
		);
		WP_CLI::log( '  tax rate  ' . $rate['name'] . ' (DE)' );
	}
	if ( class_exists( 'WC_Cache_Helper' ) && method_exists( 'WC_Cache_Helper', 'invalidate_cache_group' ) ) {
		WC_Cache_Helper::invalidate_cache_group( 'taxes' );
	}
}

function dev_seed_coupon() {
	$code      = 'SAVE10';
	$coupon_id = wc_get_coupon_id_by_code( $code );
	$coupon    = $coupon_id ? new WC_Coupon( $coupon_id ) : new WC_Coupon();
	if ( ! $coupon_id ) {
		$coupon->set_code( $code );
	}
	$coupon->set_discount_type( 'percent' );
	$coupon->set_amount( 10 );
	$coupon->set_usage_limit( 100 );
	$coupon->set_usage_limit_per_user( 10 );
	$coupon->set_description( '10% off — dev seed coupon' );
	$coupon->save();
	WP_CLI::log( sprintf( '  coupon #%d  %s (10%%)', $coupon->get_id(), $code ) );
	return $code;
}

function dev_seed_customer( $key, array $data ) {
	global $seed;
	$user = get_user_by( 'email', $data['email'] );
	if ( ! $user ) {
		$user_id = wp_insert_user( array(
			'user_login' => $data['login'],
			'user_pass'  => $data['password'],
			'user_email' => $data['email'],
			'first_name' => $data['first_name'],
			'last_name'  => $data['last_name'],
			'role'       => 'customer',
		) );
		if ( is_wp_error( $user_id ) ) {
			WP_CLI::error( 'Could not create customer: ' . $user_id->get_error_message() );
		}
	} else {
		$user_id = $user->ID;
	}

	$customer = new WC_Customer( $user_id );
	$address  = array(
		'first_name' => $data['first_name'],
		'last_name'  => $data['last_name'],
		'company'    => '',
		'address_1'  => $data['address_1'],
		'address_2'  => '',
		'city'       => $data['city'],
		'state'      => '',
		'postcode'   => $data['postcode'],
		'country'    => 'DE',
		'email'      => $data['email'],
		'phone'      => $data['phone'],
	);
	foreach ( $address as $field => $value ) {
		$setter = 'set_billing_' . $field;
		if ( method_exists( $customer, $setter ) ) {
			$customer->$setter( $value );
		}
		$setter = 'set_shipping_' . $field;
		if ( method_exists( $customer, $setter ) ) {
			$customer->$setter( $value );
		}
	}
	$customer->save();
	$seed['customers'][ $key ] = (int) $user_id;
	dev_seed_save();
	WP_CLI::log( sprintf( '  customer #%d  %s %s <%s>', $user_id, $data['first_name'], $data['last_name'], $data['email'] ) );
	return (int) $user_id;
}

function dev_seed_order( $key, array $args ) {
	global $seed;
	$id = isset( $seed['orders'][ $key ] ) ? (int) $seed['orders'][ $key ] : 0;
	if ( $id ) {
		$order = wc_get_order( $id );
		if ( $order ) {
			WP_CLI::log( sprintf( '  order #%d  (%s) already exists', $order->get_id(), $key ) );
			return $order;
		}
	}

	$order = wc_create_order( array(
		'customer_id' => (int) $args['customer_id'],
		'status'      => 'pending',
		'created_via' => 'dev-seed',
	) );
	if ( is_wp_error( $order ) ) {
		WP_CLI::error( 'wc_create_order: ' . $order->get_error_message() );
	}

	foreach ( $args['items'] as $item ) {
		$product = wc_get_product( $item['product_id'] );
		if ( ! $product ) {
			WP_CLI::error( 'Seed order ' . $key . ': product ' . $item['product_id'] . ' not found.' );
		}
		$order->add_product( $product, $item['qty'] );
	}

	if ( ! empty( $args['shipping_total'] ) ) {
		$shipping = new WC_Order_Item_Shipping();
		$shipping->set_method_title( 'Flat rate' );
		$shipping->set_method_id( 'flat_rate' );
		$shipping->set_total( (string) $args['shipping_total'] );
		$order->add_item( $shipping );
	}

	if ( ! empty( $args['coupon'] ) ) {
		$coupon_result = $order->apply_coupon( $args['coupon'] );
		if ( is_wp_error( $coupon_result ) ) {
			WP_CLI::warning( 'could not apply coupon ' . $args['coupon'] . ': ' . $coupon_result->get_error_message() );
		}
	}

	$order->set_address( $args['billing'], 'billing' );
	$order->set_address( $args['billing'], 'shipping' );
	$order->set_payment_method( 'cod' );
	$order->set_payment_method_title( 'Cash on delivery' );
	$order->update_meta_data( '_dev_seed_key', $key );
	$order->calculate_totals( true );
	$order->set_status( $args['status'] );
	$order->add_order_note( 'Dev seed order (' . $key . '): ' . $args['note'] );
	$order->save();

	$seed['orders'][ $key ] = $order->get_id();
	dev_seed_save();
	WP_CLI::log( sprintf(
		'  order #%d  (%s)  status=%s  total=%s %s',
		$order->get_id(),
		$key,
		$order->get_status(),
		$order->get_total(),
		$order->get_currency()
	) );
	return $order;
}

function dev_seed_refund( $order, $amount, $reason ) {
	global $seed;
	$existing = $order->get_refunds();
	if ( ! empty( $existing ) ) {
		$refund = reset( $existing );
		WP_CLI::log( sprintf( '  refund #%d  for order #%d already exists', $refund->get_id(), $order->get_id() ) );
		return $refund;
	}
	$refund = wc_create_refund( array(
		'order_id'       => $order->get_id(),
		'amount'         => $amount,
		'reason'         => $reason,
		'refund_payment' => false,
		'restock_items'  => true,
	) );
	if ( is_wp_error( $refund ) ) {
		WP_CLI::error( 'wc_create_refund: ' . $refund->get_error_message() );
	}
	$seed['refunds']['order1'] = $refund->get_id();
	dev_seed_save();
	WP_CLI::log( sprintf( '  refund #%d  for order #%d  amount=%s', $refund->get_id(), $order->get_id(), $refund->get_amount() ) );
	return $refund;
}

// ---------------------------------------------------------------------------
WP_CLI::log( 'Tax rates:' );
dev_seed_tax_rates();

WP_CLI::log( 'Products:' );
$clothing  = dev_seed_category( 'Clothing' );
$shoes     = dev_seed_category( 'Shoes' );
$tshirt    = dev_seed_simple( 'tshirt', array(
	'name'              => 'Basic Cotton T-Shirt',
	'slug'              => 'basic-cotton-t-shirt',
	'sku'               => 'WOO-TSHIRT',
	'regular_price'     => '19.90',
	'sale_price'        => '',
	'tax_class'         => '', // Standard.
	'stock'             => 50,
	'short_description' => 'Soft 100% cotton t-shirt, unisex fit.',
	'description'       => 'Everyday t-shirt made from 180 g/m² combed cotton. Standard VAT class.',
	'categories'        => array( $clothing ),
) );
$sneakers  = dev_seed_simple( 'sneakers', array(
	'name'              => 'Trail Running Sneakers',
	'slug'              => 'trail-running-sneakers',
	'sku'               => 'WOO-SNEAKERS',
	'regular_price'     => '89.00',
	'sale_price'        => '69.00', // On sale.
	'tax_class'         => '',
	'stock'             => 10,
	'short_description' => 'Lightweight trail shoes with a grippy outsole.',
	'description'       => 'Seeded with a sale price: regular 89.00, sale 69.00.',
	'categories'        => array( $shoes ),
) );
$mystery   = dev_seed_simple( 'mystery-box', array(
	'name'              => 'Mystery Box',
	'slug'              => 'mystery-box',
	'sku'               => '', // Deliberately no SKU.
	'regular_price'     => '15.00',
	'sale_price'        => '',
	'tax_class'         => '',
	'stock'             => 5,
	'short_description' => 'Surprise sample box — intentionally created without a SKU.',
	'description'       => 'This product has no SKU so the connector must handle products without one.',
	'categories'        => array( $clothing ),
) );
$hoodie    = dev_seed_variable();

WP_CLI::log( 'Coupon:' );
$coupon_code = dev_seed_coupon();

WP_CLI::log( 'Customers:' );
$customer1 = dev_seed_customer( 'customer1', array(
	'login'      => 'customer1',
	'password'   => 'customer1-dev-pass',
	'email'      => 'customer1@example.com',
	'first_name' => 'Anna',
	'last_name'  => 'Schmidt',
	'address_1'  => 'Alexanderplatz 5',
	'city'       => 'Berlin',
	'postcode'   => '10178',
	'phone'      => '+49 30 1234567',
) );
$customer2 = dev_seed_customer( 'customer2', array(
	'login'      => 'customer2',
	'password'   => 'customer2-dev-pass',
	'email'      => 'customer2@example.com',
	'first_name' => 'Lukas',
	'last_name'  => 'Weber',
	'address_1'  => 'Königsallee 12',
	'city'       => 'Düsseldorf',
	'postcode'   => '40212',
	'phone'      => '+49 211 7654321',
) );

WP_CLI::log( 'Orders:' );
$order1 = dev_seed_order( 'customer-order', array(
	'customer_id'    => $customer1,
	'status'         => 'completed',
	'items'          => array(
		array( 'product_id' => $tshirt->get_id(), 'qty' => 2 ),
		array( 'product_id' => $sneakers->get_id(), 'qty' => 1 ),
	),
	'coupon'         => $coupon_code,
	'shipping_total' => '4.90',
	'billing'        => array(
		'first_name' => 'Anna',
		'last_name'  => 'Schmidt',
		'company'    => '',
		'address_1'  => 'Alexanderplatz 5',
		'address_2'  => '',
		'city'       => 'Berlin',
		'state'      => '',
		'postcode'   => '10178',
		'country'    => 'DE',
		'email'      => 'customer1@example.com',
		'phone'      => '+49 30 1234567',
	),
	'note'           => 'registered customer, 10% coupon SAVE10, completed',
) );
$order2 = dev_seed_order( 'guest-order', array(
	'customer_id'    => 0, // Guest checkout.
	'status'         => 'processing',
	'items'          => array(
		array( 'product_id' => $mystery->get_id(), 'qty' => 1 ),
	),
	'shipping_total' => '0',
	'billing'        => array(
		'first_name' => 'Guest',
		'last_name'  => 'Buyer',
		'company'    => '',
		'address_1'  => 'Hauptstraße 7',
		'address_2'  => '',
		'city'       => 'München',
		'state'      => '',
		'postcode'   => '80331',
		'country'    => 'DE',
		'email'      => 'guest@example.com',
		'phone'      => '+49 89 5551234',
	),
	'note'           => 'guest checkout, no coupon, processing',
) );

WP_CLI::log( 'Refunds:' );
dev_seed_refund( $order1, '20.00', 'Partial refund: damaged item, 20.00 EUR returned' );

dev_seed_save();

WP_CLI::success( sprintf(
	'Seeded: %d products, %d customers, %d orders, 1 refund, coupon %s, %d tax rates.',
	count( $seed['products'] ),
	count( $seed['customers'] ),
	count( $seed['orders'] ),
	$coupon_code,
	2
) );
