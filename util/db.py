import sqlite3

# Connect to SQLite database
from constants.constant import StrategyType

conn = sqlite3.connect('trading_history.db')
cursor = conn.cursor()

# Create a table to store trade history
cursor.execute('''CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock TEXT,
                    action TEXT,
                    price REAL,
                    volume INTEGER,
                    crypto_cost REAL,
                    profit REAL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS positions (
                    stock TEXT PRIMARY KEY,
                    volume REAL,
                    buy_count INTEGER,
                    last_buy_price REAL,
                    total_cost REAL,
                    avg_price REAL,
                    current_price REAL,
                    profit REAL,
                    status TEXT,
                    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP)''')

conn.commit()


def update_position(self):
    # Update the current price, profit, and last updated time in the positions table
    cursor.execute('''INSERT OR REPLACE INTO positions (stock, buy_count, last_buy_price, volume, total_cost, avg_price, current_price, profit, status, last_updated)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                   (self.stock, self.buy_count, self.last_buy_price, self.volume, self.total_cost, self.avg_price,
                    self.current_price, self.profit, self.position_status, self.last_updated))
    conn.commit()


def get_position(stock):
    cursor.execute(
        '''SELECT volume,buy_count, last_buy_price, total_cost, avg_price, current_price, profit, status, last_updated FROM positions WHERE stock = ?''',
        (stock,))
    result = cursor.fetchone()
    return result

def create_position(self):

    # Insert new stock
    cursor.execute('''INSERT INTO positions (stock, buy_count, last_buy_price, volume, total_cost, avg_price, current_price, profit, status, last_updated)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                   (self.stock, self.buy_count, self.last_buy_price, self.volume, self.total_cost, self.avg_price,
                    self.current_price, self.profit, self.position_status, self.last_updated))
    conn.commit()


def record_trade(stock, action, price, volume, crypto_cost, profit):
    cursor.execute('''INSERT INTO trades (stock, action, price, volume, crypto_cost, profit) 
                          VALUES (?, ?, ?, ?, ?, ?)''',
                   (stock, action, price, volume, crypto_cost, profit))
    conn.commit()


def get_last_trade(stock):
    # Return the recent trade for the stock
    cursor.execute('''SELECT id, stock, action, price, volume, crypto_cost, profit FROM trades WHERE stock = ? ORDER BY timestamp DESC LIMIT 1''',
                   (stock,))
    result = cursor.fetchone()
    return result


def delete_trade(trade_id):
    cursor.execute('''DELETE FROM trades WHERE id = ?''',
                   (trade_id,))
    conn.commit()