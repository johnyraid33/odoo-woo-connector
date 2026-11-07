<?php
/**
 * Seed the SECOND (multi-store) WooCommerce store with a small, independent
 * data set so the connector can be tested against two stores at once.
 *
 *   cd dev/multi && ./multi.sh seed
 *   # or directly:
 *   docker compose -f dev/multi/docker-compose.yml exec -T wpcli-multi \
 *     wp --path=/var/www/html eval-file /opt/scripts/seed-catalog.php
 *
 * Contents (deliberately small and disjoint from the main dev store):
 *   * 3 simple products   MULTI-TSHIRT / MULTI-MUG / MULTI-STICKER
 *   * 1 variable product  MULTI-HOODIE with 2 variations (S, M)
 *   * 2 customers         multi.anna@example.com / multi.ben@example.com
 *   * 2 orders            one registered customer order WITH shipping (completed),
 *                         one guest order (processing)
 *   * 1 partial refund    5.00 on the completed order
 *
 * Idempotent: objects are looked up by SKU / email / option registry, so
 * re-running updates in place instead of duplicating.
 */

global $wpdb;
global $seed;

if ( ! class_exists( 'WooCommerce' ) ) {
	WP_CLI::error( 'WooCommerce is not active — run scripts/seed-wordpress.sh first.' );
}

$seed = get_option( 'woo_connector_multi_seed', array() );
if ( ! is_array( $seed ) ) {
	$seed = array();
}
$seed += array(
	'products'  => array(),
	'customers' => array(),
	'orders'    => array(),
	'refunds'   => array(),
);

function multi_seed_save() {
	global $seed;
	update_option( 'woo_connector_multi_seed', $seed, false );
}

function multi_seed_category( $name ) {
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

function multi_seed_find_product( $key, $sku = '' ) {
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

function multi_seed_simple( $key, array $data ) {
	global $seed;
	$product = multi_seed_find_product( $key, $data['sku'] );
	if ( ! $product ) {
		$product = new WC_Product_Simple();
	}
	$product->set_name( $data['name'] );
	$product->set_slug( $data['slug'] );
	$product->set_sku( $data['sku'] );
	$product->set_status( 'publish' );
	$product->set_catalog_visibility( 'visible' );
	$product->set_regular_price( $data['regular_price'] );
	$product->set_sale_price( isset( $data['sale_price'] ) ? $data['sale_price'] : '' );
	$product->set_tax_status( 'taxable' );
	$product->set_tax_class( '' );
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
	multi_seed_save();
	WP_CLI::log( sprintf(
		'  product #%d  %-26s  sku=%-16s  price=%s',
		$product->get_id(),
		$data['name'],
		$data['sku'],
		$product->get_price()
	) );
	return $product;
}

function multi_seed_variable() {
	global $seed;
	$product = multi_seed_find_product( 'hoodie', 'MULTI-HOODIE' );
	if ( ! $product || ! $product->is_type( 'variable' ) ) {
		$product = new WC_Product_Variable();
	}
	$product->set_name( 'Multi Variable Hoodie' );
	$product->set_slug( 'multi-variable-hoodie' );
	$product->set_sku( 'MULTI-HOODIE' );
	$product->set_status( 'publish' );
	$product->set_catalog_visibility( 'visible' );
	$product->set_tax_status( 'taxable' );
	$product->set_tax_class( '' );
	$product->set_short_description( 'Second-store hoodie with two sizes.' );
	$product->set_description( 'Only exists in the multi store; the connector must discover its 2 variations via the API.' );
	$product->set_category_ids( array( multi_seed_category( 'Multi Clothing' ) ) );

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
	multi_seed_save();

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
		'S' => array( 'price' => '39.00', 'stock' => 6 ),
		'M' => array( 'price' => '44.00', 'stock' => 2 ),
	);
	foreach ( $variations as $size => $info ) {
		$variation = isset( $existing[ $size ] ) ? $existing[ $size ] : new WC_Product_Variation();
		$variation->set_parent_id( $product->get_id() );
		$variation->set_attributes( array( 'size' => $size ) );
		$variation->set_sku( 'MULTI-HOODIE-' . $size );
		$variation->set_regular_price( $info['price'] );
		$variation->set_manage_stock( true );
		$variation->set_stock_quantity( $info['stock'] );
		$variation->set_stock_status( 'instock' );
		$variation->set_status( 'publish' );
		$variation->save();
		WP_CLI::log( sprintf( '  variation #%d  Multi Variable Hoodie / %s  %s EUR', $variation->get_id(), $size, $info['price'] ) );
	}

	$product->save();
	WC_Product_Variable::sync( $product->get_id() );
	WP_CLI::log( sprintf( '  variable product #%d  Multi Variable Hoodie  (%d variations)', $product->get_id(), count( $variations ) ) );
	return $product;
}

function multi_seed_customer( $key, array $data ) {
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
	multi_seed_save();
	WP_CLI::log( sprintf( '  customer #%d  %s %s <%s>', $user_id, $data['first_name'], $data['last_name'], $data['email'] ) );
	return (int) $user_id;
}

function multi_seed_order( $key, array $args ) {
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
		'created_via' => 'multi-seed',
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

	$order->set_address( $args['billing'], 'billing' );
	$order->set_address( $args['billing'], 'shipping' );
	$order->set_payment_method( 'cod' );
	$order->set_payment_method_title( 'Cash on delivery' );
	$order->update_meta_data( '_multi_seed_key', $key );
	$order->calculate_totals( true );
	$order->set_status( $args['status'] );
	$order->add_order_note( 'Multi-store seed order (' . $key . '): ' . $args['note'] );
	$order->save();

	$seed['orders'][ $key ] = $order->get_id();
	multi_seed_save();
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

function multi_seed_refund( $order, $amount, $reason ) {
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
	multi_seed_save();
	WP_CLI::log( sprintf( '  refund #%d  for order #%d  amount=%s', $refund->get_id(), $order->get_id(), $refund->get_amount() ) );
	return $refund;
}

// ---------------------------------------------------------------------------
WP_CLI::log( 'Products:' );
$clothing = multi_seed_category( 'Multi Clothing' );
$mugs     = multi_seed_category( 'Multi Drinkware' );

$tshirt = multi_seed_simple( 'tshirt', array(
	'name'              => 'Multi Basic T-Shirt',
	'slug'              => 'multi-basic-t-shirt',
	'sku'               => 'MULTI-TSHIRT',
	'regular_price'     => '12.00',
	'sale_price'        => '',
	'stock'             => 25,
	'short_description' => 'Second-store t-shirt, plain and cheap.',
	'description'       => 'Deliberately different SKU prefix (MULTI-) so the two stores never collide.',
	'categories'        => array( $clothing ),
) );

$mug = multi_seed_simple( 'mug', array(
	'name'              => 'Multi Ceramic Mug',
	'slug'              => 'multi-ceramic-mug',
	'sku'               => 'MULTI-MUG',
	'regular_price'     => '8.50',
	'sale_price'        => '7.00',
	'stock'             => 40,
	'short_description' => 'Second-store mug, currently on sale.',
	'description'       => 'Seeded with a sale price: regular 8.50, sale 7.00.',
	'categories'        => array( $mugs ),
) );

$sticker = multi_seed_simple( 'sticker', array(
	'name'              => 'Multi Sticker Pack',
	'slug'              => 'multi-sticker-pack',
	'sku'               => 'MULTI-STICKER',
	'regular_price'     => '3.20',
	'sale_price'        => '',
	'stock'             => 100,
	'short_description' => 'Second-store sticker pack.',
	'description'       => 'Cheap filler item used by the seeded guest order.',
	'categories'        => array( $clothing ),
) );

$hoodie = multi_seed_variable();

WP_CLI::log( 'Customers:' );
$customer1 = multi_seed_customer( 'customer1', array(
	'login'      => 'multi_anna',
	'password'   => 'multi-anna-dev-pass',
	'email'      => 'multi.anna@example.com',
	'first_name' => 'Anna',
	'last_name'  => 'Multi',
	'address_1'  => 'Multiweg 1',
	'city'       => 'Hamburg',
	'postcode'   => '20095',
	'phone'      => '+49 40 1112223',
) );
$customer2 = multi_seed_customer( 'customer2', array(
	'login'      => 'multi_ben',
	'password'   => 'multi-ben-dev-pass',
	'email'      => 'multi.ben@example.com',
	'first_name' => 'Ben',
	'last_name'  => 'Multi',
	'address_1'  => 'Multiweg 2',
	'city'       => 'Bremen',
	'postcode'   => '28195',
	'phone'      => '+49 421 4445556',
) );

WP_CLI::log( 'Orders:' );
$order1 = multi_seed_order( 'customer-order', array(
	'customer_id'    => $customer1,
	'status'         => 'completed',
	'items'          => array(
		array( 'product_id' => $tshirt->get_id(), 'qty' => 2 ),
		array( 'product_id' => $mug->get_id(), 'qty' => 1 ),
	),
	'shipping_total' => '4.90',
	'billing'        => array(
		'first_name' => 'Anna',
		'last_name'  => 'Multi',
		'company'    => '',
		'address_1'  => 'Multiweg 1',
		'address_2'  => '',
		'city'       => 'Hamburg',
		'state'      => '',
		'postcode'   => '20095',
		'country'    => 'DE',
		'email'      => 'multi.anna@example.com',
		'phone'      => '+49 40 1112223',
	),
	'note'           => 'registered customer, WITH shipping (4.90), completed',
) );

$order2 = multi_seed_order( 'guest-order', array(
	'customer_id'    => 0, // Guest checkout.
	'status'         => 'processing',
	'items'          => array(
		array( 'product_id' => $sticker->get_id(), 'qty' => 3 ),
	),
	'shipping_total' => '0',
	'billing'        => array(
		'first_name' => 'Guest',
		'last_name'  => 'Multi',
		'company'    => '',
		'address_1'  => 'Gastweg 9',
		'address_2'  => '',
		'city'       => 'Köln',
		'state'      => '',
		'postcode'   => '50667',
		'country'    => 'DE',
		'email'      => 'guest.multi@example.com',
		'phone'      => '+49 221 7778889',
	),
	'note'           => 'guest checkout, no shipping cost, processing',
) );

WP_CLI::log( 'Refunds:' );
multi_seed_refund( $order1, '5.00', 'Multi-store partial refund: 5.00 EUR for a damaged mug' );

multi_seed_save();

WP_CLI::success( sprintf(
	'Seeded multi store: %d simple/variable products (3 simple + 1 variable with 2 variations), %d customers, %d orders, 1 refund of 5.00.',
	count( $seed['products'] ),
	count( $seed['customers'] ),
	count( $seed['orders'] )
) );
