-- Enable PostGIS extension
CREATE EXTENSION IF NOT EXISTS postgis;

-- Stores table
CREATE TABLE stores (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL,
    location GEOGRAPHY(POINT, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography) STORED,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_stores_location ON stores USING GIST(location);

-- Warehouses table
CREATE TABLE warehouses (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL,
    location GEOGRAPHY(POINT, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography) STORED,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_warehouses_location ON warehouses USING GIST(location);

-- Items table
CREATE TABLE items (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    shelf_life_days INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Inventory table
CREATE TABLE inventory (
    id SERIAL PRIMARY KEY,
    location_id INTEGER NOT NULL,
    location_type VARCHAR(50) NOT NULL CHECK (location_type IN ('store', 'warehouse')),
    item_id INTEGER NOT NULL REFERENCES items(id),
    quantity INTEGER NOT NULL DEFAULT 0,
    expiry_date DATE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_inventory_location ON inventory(location_id, location_type);
CREATE INDEX idx_inventory_item ON inventory(item_id);
CREATE INDEX idx_inventory_expiry ON inventory(expiry_date) WHERE expiry_date IS NOT NULL;

-- Demand history table
CREATE TABLE demand_history (
    id SERIAL PRIMARY KEY,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    item_id INTEGER NOT NULL REFERENCES items(id),
    date DATE NOT NULL,
    quantity INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_demand_history_store_item ON demand_history(store_id, item_id, date);

-- Delivery schedules table
CREATE TABLE delivery_schedules (
    id SERIAL PRIMARY KEY,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    weekday INTEGER NOT NULL CHECK (weekday >= 0 AND weekday <= 6),
    cutoff_time TIME NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_delivery_schedules_store ON delivery_schedules(store_id);

-- Orders table (idempotent)
CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    item_id INTEGER NOT NULL REFERENCES items(id),
    quantity INTEGER NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'fulfilled')),
    idempotency_key VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_orders_store ON orders(store_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_idempotency ON orders(idempotency_key);

-- Transfers table (idempotent)
CREATE TABLE transfers (
    id SERIAL PRIMARY KEY,
    from_store_id INTEGER NOT NULL REFERENCES stores(id),
    to_store_id INTEGER NOT NULL REFERENCES stores(id),
    item_id INTEGER NOT NULL REFERENCES items(id),
    quantity INTEGER NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'fulfilled')),
    idempotency_key VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_transfers_from_store ON transfers(from_store_id);
CREATE INDEX idx_transfers_to_store ON transfers(to_store_id);
CREATE INDEX idx_transfers_status ON transfers(status);
CREATE INDEX idx_transfers_idempotency ON transfers(idempotency_key);
