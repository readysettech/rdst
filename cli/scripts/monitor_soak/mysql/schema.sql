CREATE TABLE IF NOT EXISTS customers (
  id INT PRIMARY KEY AUTO_INCREMENT,
  country VARCHAR(2) NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
  id INT PRIMARY KEY AUTO_INCREMENT,
  customer_id INT NOT NULL,
  total INT NOT NULL,
  status VARCHAR(16) NOT NULL,
  KEY idx_orders_customer (customer_id)
);
